"""#180: size tiers raise the Spark deadline for large tables, and a table that keeps hitting
its deadline or OOM backs off instead of being planned again every run."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.job_store import SQLJobStore
from cryo.domain.enums import JobStatus
from cryo.engines.spark.sizing import DISPATCH_DEADLINE_SECONDS, cap_deadline_seconds_for_table
from cryo.maintenance.failure_backoff import (
    backoff_for_streak,
    is_resource_exhaustion_error,
    record_resource_failure_backoff,
    resource_failure_streak,
)
from cryo.scheduler.rules import TableCandidate

NOW = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
TIMEOUT = "Spark Job cryo-spark-abc did not complete within 7200s"
OOM = "Spark-on-K8s Job failed: OOMKilled exit 137\ndriver log"


# --- size tiers ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("size_gb", "expected"),
    [(10.0, DISPATCH_DEADLINE_SECONDS), (578.0, 10_800), (785.0, 10_800), (2_700.0, 14_400)],
)
def test_default_base_deadline_takes_the_size_tier(size_gb: float, expected: int) -> None:
    # The k8s default is 7200s. Before #180 min(7200, tier) left every tier at 7200s.
    assert cap_deadline_seconds_for_table(7_200, total_size_gb=size_gb) == expected


def test_tier_never_exceeds_the_ceiling() -> None:
    assert cap_deadline_seconds_for_table(99_999, total_size_gb=500.0) == 10_800
    assert cap_deadline_seconds_for_table(99_999, total_size_gb=50_000.0) == 14_400


def test_a_destination_configured_below_the_base_keeps_its_shorter_window() -> None:
    assert cap_deadline_seconds_for_table(1_800, total_size_gb=900.0) == 1_800
    assert cap_deadline_seconds_for_table(1_800, total_size_gb=None) == 1_800


# --- error classification and schedule ------------------------------------------------------


def test_only_deadline_and_oom_errors_count() -> None:
    assert is_resource_exhaustion_error(TIMEOUT)
    assert is_resource_exhaustion_error(f"optimize: {OOM}")
    assert is_resource_exhaustion_error("Spark-on-K8s Job failed: DeadlineExceeded")
    assert not is_resource_exhaustion_error("Spark-on-K8s Job failed: AnalysisException")
    assert not is_resource_exhaustion_error(None)


def test_backoff_starts_at_the_second_failure_and_grows() -> None:
    assert backoff_for_streak(0) is None
    assert backoff_for_streak(1) is None
    waits = [backoff_for_streak(n) for n in (2, 3, 4, 5, 11)]
    assert all(wait is not None for wait in waits)
    assert waits == sorted(waits)  # type: ignore[type-var]
    assert waits[-1] == timedelta(days=7)


# --- streak and the retry-after gate --------------------------------------------------------


def _seed_table(session: Session) -> tuple[Table, Destination, MaintenanceRun]:
    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4()}", type="rest", config={})
    table = Table(id=uuid.uuid4(), source_id=source.id, schema_name="s", table_name="t")
    destination = Destination(
        id=uuid.uuid4(), name=f"dest-{uuid.uuid4()}", engine="spark_k8s", config={}
    )
    run = MaintenanceRun(id=uuid.uuid4(), kind="scheduled", status="queued", trigger="cron")
    session.add_all([source, table, destination, run])
    session.commit()
    return table, destination, run


def _finished_job(
    session: Session,
    table: Table,
    destination: Destination,
    run: MaintenanceRun,
    *,
    status: str,
    error: str | None,
    days_ago: int,
) -> MaintenanceJob:
    started = NOW - timedelta(days=days_ago)
    job = MaintenanceJob(
        id=uuid.uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status=status,
        error=error,
        started_at=started,
        finished_at=started + timedelta(hours=2),
    )
    session.add(job)
    session.commit()
    return job


def test_streak_counts_consecutive_resource_failures_only(session: Session) -> None:
    table, destination, run = _seed_table(session)
    _finished_job(session, table, destination, run, status="failed", error=TIMEOUT, days_ago=5)
    _finished_job(session, table, destination, run, status="succeeded", error=None, days_ago=4)
    _finished_job(session, table, destination, run, status="failed", error="boom", days_ago=3)
    _finished_job(session, table, destination, run, status="failed", error=TIMEOUT, days_ago=2)
    _finished_job(
        session, table, destination, run, status="partial", error=f"optimize: {OOM}", days_ago=1
    )

    assert resource_failure_streak(session, table.id) == 2


def test_one_failure_does_not_back_off(session: Session) -> None:
    table, destination, run = _seed_table(session)
    _finished_job(session, table, destination, run, status="failed", error=TIMEOUT, days_ago=1)

    assert record_resource_failure_backoff(session, table.id, now=NOW) is None
    session.refresh(table)
    assert table.maintenance_retry_after is None


def test_backoff_never_shortens_a_later_retry_after(session: Session) -> None:
    table, destination, run = _seed_table(session)
    later = NOW + timedelta(days=30)
    table.maintenance_retry_after = later
    session.commit()
    for days_ago in (2, 1):
        _finished_job(
            session, table, destination, run, status="failed", error=TIMEOUT, days_ago=days_ago
        )

    assert record_resource_failure_backoff(session, table.id, now=NOW) is None
    session.refresh(table)
    assert table.maintenance_retry_after.replace(tzinfo=UTC) == later


def test_job_store_arms_the_gate_and_the_scheduler_skips_the_table(
    session: Session, session_factory: sessionmaker
) -> None:
    from cryo.scheduler.rules import tables_for_preset

    table, destination, run = _seed_table(session)
    for days_ago in (3, 2, 1):
        _finished_job(
            session, table, destination, run, status="failed", error=TIMEOUT, days_ago=days_ago
        )
    store = SQLJobStore(session_factory)

    store.update_resource_failure_backoff(str(table.id), JobStatus.FAILED, TIMEOUT)

    with session_factory() as fresh:
        retry_after = fresh.get(Table, table.id).maintenance_retry_after
    assert retry_after is not None
    assert retry_after.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(hours=24)

    candidate = TableCandidate(
        id=str(table.id), schema_name="s", table_name="t", maintenance_retry_after=retry_after
    )
    from cryo.scheduler.rules import PresetConfig

    preset = PresetConfig(
        id=str(uuid.uuid4()),
        name="nightly",
        destination_id=str(destination.id),
        guardrails={},  # adaptive_routing off: the backoff must still apply.
        schedule_cron="0 2 * * *",
        is_default=True,
    )
    clear = TableCandidate(id=str(uuid.uuid4()), schema_name="s", table_name="other")
    selected = tables_for_preset(
        preset, [candidate, clear], [preset], [], [], now=datetime.now(UTC)
    )
    assert [c.id for c in selected] == [clear.id]


def test_success_ends_the_backoff_but_not_a_missing_metadata_containment(
    session: Session, session_factory: sessionmaker
) -> None:
    store = SQLJobStore(session_factory)
    backed_off, _, _ = _seed_table(session)
    contained, _, _ = _seed_table(session)
    until = datetime.now(UTC) + timedelta(days=3)
    backed_off.maintenance_retry_after = until
    contained.maintenance_retry_after = until
    contained.maintenance_missing_metadata_reason = "metadata.json missing"
    session.commit()

    store.update_resource_failure_backoff(str(backed_off.id), JobStatus.SUCCEEDED, None)
    store.update_resource_failure_backoff(str(contained.id), JobStatus.SUCCEEDED, None)

    with session_factory() as fresh:
        assert fresh.get(Table, backed_off.id).maintenance_retry_after is None
        assert fresh.get(Table, contained.id).maintenance_retry_after is not None


def test_worker_reports_finished_jobs_to_the_backoff() -> None:
    from cryo.worker.worker import Worker

    calls: list[tuple[str, JobStatus, str | None]] = []

    class _Store:
        def update_resource_failure_backoff(
            self, table_id: str, status: JobStatus, error: str | None
        ) -> None:
            calls.append((table_id, status, error))

    worker = Worker.__new__(Worker)
    worker._store = _Store()  # type: ignore[assignment]
    worker._update_failure_backoff("t1", JobStatus.FAILED, TIMEOUT)
    worker._update_failure_backoff("t1", JobStatus.DEFERRED, None)
    worker._update_failure_backoff("t1", JobStatus.SUCCEEDED, None)

    assert calls == [("t1", JobStatus.FAILED, TIMEOUT), ("t1", JobStatus.SUCCEEDED, None)]
