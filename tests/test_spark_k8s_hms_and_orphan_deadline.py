"""#257: Spark-on-K8s metastore timeouts retry with backoff and are counted apart.
#258: orphan recovery enforces the Spark Job deadline on adopted steps and cleans up."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from cryo.db.repositories.job_steps import InMemoryJobStepStore
from cryo.domain.enums import JobStatus, JobStepStatus, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.base import TABLE_NOT_FOUND_MARKER, is_metastore_unreachable_error
from cryo.engines.spark import k8s as spark_k8s
from cryo.engines.spark.k8s import JobRunResult, JobRunState
from cryo.engines.spark.k8s_engine import (
    SparkK8sEngine,
    is_spark_retryable_catalog_error,
    is_spark_transient_metastore_error,
)
from cryo.maintenance.coordinator import BatchMaintenance, RunReport, TableOutcome
from cryo.maintenance.report import build_blocks
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker import orphan_recovery
from cryo.worker.orphan_recovery import recover_orphaned_jobs
from cryo.worker.worker import (
    DestinationConfig,
    InMemoryJobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
)

HMS_TIMEOUT = (
    "MetaException(message:Could not connect to meta store using any of the URIs provided. "
    "Most recent failure: org.apache.thrift.transport.TTransportException: "
    "java.net.ConnectException: Connection timed out (Connection timed out)"
)


def _plan() -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="demo.mart.events",
        mode=Mode.DEEP,
        operations=(Operation(OperationType.OPTIMIZE_MANIFESTS, {}),),
    )


# --- #257 -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        HMS_TIMEOUT,
        "java.net.SocketTimeoutException: Read timed out",
        "org.apache.thrift.transport.TTransportException: java.net.ConnectException: refused",
    ],
)
def test_metastore_timeouts_are_transient_and_retryable(message: str) -> None:
    assert is_spark_transient_metastore_error(message)
    assert is_spark_retryable_catalog_error(message)


def test_report_classifies_metastore_specific_text_without_the_marker() -> None:
    assert is_metastore_unreachable_error(HMS_TIMEOUT)
    assert not is_metastore_unreachable_error("java.net.SocketTimeoutException: Read timed out")


def test_a_dropped_table_is_not_a_metastore_outage() -> None:
    message = "NoSuchTableException: gone\njava.net.ConnectException: noise"
    assert not is_spark_transient_metastore_error(message)
    assert not is_spark_retryable_catalog_error(message)
    assert not is_spark_transient_metastore_error("AnalysisException: bad column")


def test_execute_retries_a_metastore_timeout_with_longer_backoff(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("cryo.engines.spark.k8s_engine.time.sleep", sleeps.append)
    engine = SparkK8sEngine(config={"catalog": "demo"})
    client = MagicMock()
    client.submit_spark_sql.side_effect = ["cryo-spark-hms0", "cryo-spark-ok"]
    client.wait_for_completion.side_effect = [
        JobRunResult(
            job_run_id="cryo-spark-hms0", state=JobRunState.FAILED, state_details=HMS_TIMEOUT
        ),
        JobRunResult(job_run_id="cryo-spark-ok", state=JobRunState.SUCCESS),
    ]
    engine._client = client

    results = engine.execute_plan(_plan())

    assert results[0].ok
    assert client.submit_spark_sql.call_count == 2
    assert sleeps == [30]


def test_execute_marks_a_persistent_metastore_outage(monkeypatch) -> None:
    monkeypatch.setattr("cryo.engines.spark.k8s_engine.time.sleep", lambda _s: None)
    engine = SparkK8sEngine(config={"catalog": "demo"})
    client = MagicMock()
    client.submit_spark_sql.side_effect = [f"cryo-spark-hms{i}" for i in range(6)]
    client.wait_for_completion.side_effect = [
        JobRunResult(
            job_run_id=f"cryo-spark-hms{i}", state=JobRunState.FAILED, state_details=HMS_TIMEOUT
        )
        for i in range(6)
    ]
    engine._client = client

    results = engine.execute_plan(_plan())

    assert not results[0].ok
    assert client.submit_spark_sql.call_count == 3  # bounded like the catalog retry
    assert "metastore_unreachable:" in (results[0].error or "")
    assert "after 3 attempt(s)" in (results[0].error or "")
    assert is_metastore_unreachable_error(results[0].error)


def test_run_report_counts_metastore_failures_separately() -> None:
    report = RunReport(
        run_id=None,
        trigger="batch-cron",
        dry_run=False,
        started_at=datetime(2026, 10, 26, tzinfo=UTC),
    )

    def outcome(name: str, error: str) -> TableOutcome:
        return TableOutcome(
            table_id=uuid.uuid4(),
            name=name,
            job_id=uuid.uuid4(),
            lane=0,
            statement_count=1,
            before_size_gb=1.0,
            before_files=1,
            status=JobStatus.FAILED,
            error=error,
        )

    outcomes = {
        o.table_id: o
        for o in (
            outcome("a", f"Spark-on-K8s Job FAILED: metastore_unreachable: x\n{HMS_TIMEOUT}"),
            outcome("b", "Spark-on-K8s Job FAILED: AnalysisException"),
            outcome(
                "c", f"{TABLE_NOT_FOUND_MARKER} NoSuchTableException java.net.ConnectException"
            ),
        )
    }
    BatchMaintenance._tally(cast(Any, None), report, outcomes)

    assert report.failed == 3
    assert report.metastore_unreachable == 1
    _text, blocks = build_blocks(report)
    assert "Hive Metastore was unreachable" in str(blocks)


# --- #258 -------------------------------------------------------------------------------

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def _spark_dest() -> DestinationConfig:
    return DestinationConfig(id="dest", engine="spark_emr", config={"submit_mode": "k8s"})


def _job() -> MaintenanceJobRecord:
    return MaintenanceJobRecord(
        id="job-1",
        run_id="run-1",
        table_id="table-1",
        destination_id="dest",
        mode=Mode.STANDARD,
        status=JobStatus.RUNNING,
        started_at=NOW - timedelta(hours=4),
        plan={"table_ref": "demo.mart.events"},
        engine_ref="cryo-spark-adopted",
    )


class _FakeK8s:
    def __init__(self, state: JobRunState, started_at: datetime | None) -> None:
        self.state = state
        self.started_at = started_at
        self.cancelled: list[str] = []
        self.cleaned: list[str] = []

    def install(self, monkeypatch) -> None:
        fake = self
        monkeypatch.setattr(
            spark_k8s.SparkK8sClient,
            "get_job_run",
            lambda self, job_id: JobRunResult(
                job_run_id=job_id, state=fake.state, started_at=fake.started_at
            ),
        )
        monkeypatch.setattr(
            spark_k8s.SparkK8sClient, "cancel_job_run", lambda self, j: fake.cancelled.append(j)
        )
        monkeypatch.setattr(
            spark_k8s.SparkK8sClient, "cleanup_after_run", lambda self, j: fake.cleaned.append(j)
        )


def _setup(size_gb: float | None = 10.0):
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    store.add_destination(_spark_dest())
    store.add_job(_job())
    store.tables["table-1"] = TableMaintenanceData(
        stats={"total_size_gb": size_gb} if size_gb is not None else {}
    )
    steps.begin_step("job-1", 0, OperationType.OPTIMIZE, detail="CALL rewrite_data_files")
    steps.set_step_engine_ref("job-1", 0, "cryo-spark-adopted")
    locks.acquire_table_lock("table-1", "job-1", 3600)
    orphan_recovery._LOGGED_WAITING.clear()
    return queue, locks, store, steps


@pytest.fixture
def frozen_now(monkeypatch):
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(orphan_recovery, "datetime", _DT)


def test_adopted_spark_step_past_its_deadline_is_cancelled_and_failed(
    monkeypatch, frozen_now
) -> None:
    fake = _FakeK8s(JobRunState.RUNNING, started_at=NOW - timedelta(hours=3, minutes=42))
    fake.install(monkeypatch)
    queue, locks, store, steps = _setup()

    result = recover_orphaned_jobs(store, queue, locks, steps, holder_id="worker-a")

    assert result.resolved_steps == 1
    assert fake.cancelled == ["cryo-spark-adopted"]
    assert steps.get_step("job-1", 0).status is JobStepStatus.FAILED
    job = store.jobs["job-1"]
    assert job.status is JobStatus.FAILED
    assert "did not complete within 7200s" in (job.error or "")
    assert not locks.is_table_locked("table-1")


def test_adopted_spark_step_inside_its_deadline_keeps_waiting(monkeypatch, frozen_now) -> None:
    fake = _FakeK8s(JobRunState.RUNNING, started_at=NOW - timedelta(minutes=90))
    fake.install(monkeypatch)
    queue, locks, store, steps = _setup()

    result = recover_orphaned_jobs(store, queue, locks, steps, holder_id="worker-a")

    assert result.waiting == 1
    assert fake.cancelled == []
    assert store.jobs["job-1"].status is JobStatus.RUNNING


def test_unknown_table_size_waits_for_the_largest_deadline(monkeypatch, frozen_now) -> None:
    """Unknown size must not cancel a job the original waiter could still be waiting on."""
    fake = _FakeK8s(JobRunState.RUNNING, started_at=NOW - timedelta(hours=3))
    fake.install(monkeypatch)
    monkeypatch.setattr(
        "cryo.engines.spark.sizing.cap_deadline_seconds_for_table",
        lambda seconds, *, total_size_gb: 3600 if total_size_gb is None else 14_400,
    )
    queue, locks, store, steps = _setup(size_gb=None)

    result = recover_orphaned_jobs(store, queue, locks, steps, holder_id="worker-a")

    assert result.waiting == 1
    assert fake.cancelled == []


def test_driver_start_falls_back_to_the_step_start(monkeypatch, frozen_now) -> None:
    fake = _FakeK8s(JobRunState.RUNNING, started_at=None)
    fake.install(monkeypatch)
    queue, locks, store, steps = _setup()
    step = steps.get_step("job-1", 0)
    assert step.started_at is not None  # the real ledger stamps it at begin_step
    overdue = orphan_recovery._adopted_spark_k8s_overdue(
        store,
        _spark_dest(),
        _job(),
        step,
        "cryo-spark-adopted",
        now=step.started_at + timedelta(hours=3),
    )
    assert overdue == 7200
    assert fake.cancelled == ["cryo-spark-adopted"]


@pytest.mark.parametrize("state", [JobRunState.SUCCESS, JobRunState.FAILED])
def test_resolved_adopted_spark_step_cleans_up_the_driver(monkeypatch, frozen_now, state) -> None:
    fake = _FakeK8s(state, started_at=None)
    fake.install(monkeypatch)
    queue, locks, store, steps = _setup()

    recover_orphaned_jobs(store, queue, locks, steps, holder_id="worker-a")

    assert fake.cleaned == ["cryo-spark-adopted"]
    assert fake.cancelled == []


def test_adoption_is_logged_once_per_job(monkeypatch, frozen_now, caplog) -> None:
    fake = _FakeK8s(JobRunState.RUNNING, started_at=NOW - timedelta(minutes=5))
    fake.install(monkeypatch)
    queue, locks, store, steps = _setup()

    with caplog.at_level(logging.INFO, logger="cryo.worker.orphan_recovery"):
        for _ in range(3):
            recover_orphaned_jobs(store, queue, locks, steps, holder_id="worker-a")

    waiting = [
        r for r in caplog.records if "waiting" in r.getMessage() and r.levelno == logging.INFO
    ]
    assert len(waiting) == 1


def test_an_oom_with_peer_connection_errors_is_not_a_metastore_outage() -> None:
    from cryo.engines.spark.k8s_engine import EXECUTOR_OOM_PREFIX

    driver = "OOMKilled exit 137\njava.net.ConnectException: Connection refused"
    executor = f"{EXECUTOR_OOM_PREFIX} exec-1\njava.net.ConnectException: Connection refused"
    for message in (driver, executor):
        assert not is_spark_transient_metastore_error(message)
    assert not is_metastore_unreachable_error("S3 java.net.SocketTimeoutException: Read timed out")
