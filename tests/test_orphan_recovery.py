"""Orphan recovery adopt / resolve behavior."""

from __future__ import annotations

from datetime import UTC, datetime

from cryo.db.repositories.job_steps import InMemoryJobStepStore
from cryo.domain.enums import EngineType, JobStatus, JobStepStatus, Mode, OperationType
from cryo.engines.trino.query_status import EngineOpState
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.reconcile import reconcile_stale_running_jobs
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker import orphan_recovery
from cryo.worker.orphan_recovery import probe_engine_op_state, recover_orphaned_jobs
from cryo.worker.worker import DestinationConfig, InMemoryJobStore, MaintenanceJobRecord


def _running_job(**overrides) -> MaintenanceJobRecord:
    data = dict(
        id="job-1",
        run_id="run-1",
        table_id="table-1",
        destination_id="dest",
        mode=Mode.STANDARD,
        status=JobStatus.RUNNING,
        started_at=datetime(2026, 7, 27, 11, 0, tzinfo=UTC),
        plan={"table_ref": "cdp.reports.t"},
    )
    data.update(overrides)
    return MaintenanceJobRecord(**data)


def test_adopt_race_only_one_winner() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    store.add_destination(DestinationConfig(id="dest", engine=EngineType.TRINO))
    store.add_job(_running_job())
    steps.begin_step("job-1", 0, OperationType.EXPIRE_SNAPSHOTS, detail="ALTER ...")
    r1 = recover_orphaned_jobs(store, queue, locks, steps, holder_id="worker-a")
    r2 = recover_orphaned_jobs(store, queue, locks, steps, holder_id="worker-b")
    assert r1.adopted == 1
    assert r2.adopted == 0
    assert locks.get_job_owner("job-1") == "worker-a"


def test_uncertain_dangerous_step_does_not_complete(monkeypatch) -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    store.add_destination(DestinationConfig(id="dest", engine=EngineType.TRINO))
    store.add_job(_running_job())
    steps.begin_step("job-1", 0, OperationType.REMOVE_ORPHAN_FILES, detail="orphan")
    monkeypatch.setattr(
        orphan_recovery,
        "probe_engine_op_state",
        lambda *a, **k: EngineOpState.UNCERTAIN,
    )
    result = recover_orphaned_jobs(store, queue, locks, steps, holder_id="worker-a")
    assert result.waiting == 1
    assert steps.get_started_step("job-1") is not None
    assert locks.is_table_locked("table-1") or locks.acquire_table_lock("table-1", "job-1", 60)


def test_succeeded_step_closed_on_recover(monkeypatch) -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    store.add_destination(DestinationConfig(id="dest", engine=EngineType.TRINO))
    store.add_job(_running_job())
    steps.begin_step("job-1", 0, OperationType.OPTIMIZE, detail="opt")
    locks.acquire_table_lock("table-1", "job-1", 3600)
    monkeypatch.setattr(
        orphan_recovery,
        "probe_engine_op_state",
        lambda *a, **k: EngineOpState.SUCCEEDED,
    )
    result = recover_orphaned_jobs(store, queue, locks, steps, holder_id="worker-a")
    assert result.resolved_steps == 1
    assert steps.get_step("job-1", 0).status is JobStepStatus.SUCCEEDED
    # Regression, 2026-07-29: resolving the step used to be the end of the line -- nothing
    # ever handed the job back for its remaining operations to be dispatched. It must be
    # requeued so a normal claim_admitted() pass picks it up and continues (or finalizes it,
    # if that was the last step).
    assert queue.status("job-1") is JobStatus.ADMITTED
    assert queue.claim_admitted() == "job-1"


def test_probe_engine_op_state_is_quiet_not_uncertain_with_no_prior_dispatch() -> None:
    """Regression, 2026-07-28: a fresh job's very first dangerous op (always expire_snapshots,
    always first in the plan) has no engine_ref yet -- there is no prior dispatch to check for
    staleness. probe_engine_op_state used to return UNCERTAIN unconditionally in that case for
    both native and Spark destinations, and wait_for_table_quiet() never resolves out of
    UNCERTAIN for a DANGEROUS_OP (it keeps resetting the consecutive-clean counter), so every
    fresh job held forever at "holding for quiet window" and never dispatched anything.
    Confirmed live: this blocked every fresh Spark-K8s and native job after the quiet-window
    safety gate (f2af45a) was deployed."""
    spark_dest = DestinationConfig(
        id="dest-spark", engine="spark_emr", config={"submit_mode": "k8s"}
    )
    assert (
        probe_engine_op_state(spark_dest, table_ref="cdp.reports.t", engine_ref=None)
        is EngineOpState.SUCCEEDED
    )

    native_dest = DestinationConfig(id="dest-native", engine=EngineType.NATIVE)
    assert (
        probe_engine_op_state(native_dest, table_ref="cdp.reports.t", engine_ref=None, job=None)
        is EngineOpState.SUCCEEDED
    )


def test_spark_k8s_job_state_detects_success_not_just_failure(monkeypatch) -> None:
    """Regression, 2026-07-29: _spark_job_state imported JobRunState from
    cryo.engines.spark.emr while comparing it (via `is`) against the result of
    SparkK8sClient.get_job_run(), which returns cryo.engines.spark.k8s.JobRunState -- a
    different enum class. Identity comparison across two unrelated StrEnum classes is never
    True even when both members are named/valued "SUCCESS", so a genuinely completed Spark-K8s
    job could never be detected as SUCCEEDED, only ever falling through to RUNNING. Confirmed
    live: an adopted orphan job whose Spark K8s Job had already reached completionTime/
    succeeded=1 was still logged "still running; waiting" 40+ minutes after the underlying
    rewrite committed, permanently stalling the job at that step forever."""
    from cryo.engines.spark import k8s as spark_k8s

    class _FakeResult:
        def __init__(self, state: spark_k8s.JobRunState) -> None:
            self.state = state

    monkeypatch.setattr(
        spark_k8s.SparkK8sClient,
        "get_job_run",
        lambda self, job_id: _FakeResult(spark_k8s.JobRunState.SUCCESS),
    )
    dest = DestinationConfig(id="dest-spark", engine="spark_emr", config={"submit_mode": "k8s"})
    assert (
        probe_engine_op_state(dest, table_ref="cdp.reports.t", engine_ref="cryo-spark-abc123")
        is EngineOpState.SUCCEEDED
    )

    monkeypatch.setattr(
        spark_k8s.SparkK8sClient,
        "get_job_run",
        lambda self, job_id: _FakeResult(spark_k8s.JobRunState.FAILED),
    )
    assert (
        probe_engine_op_state(dest, table_ref="cdp.reports.t", engine_ref="cryo-spark-abc123")
        is EngineOpState.FAILED
    )

    monkeypatch.setattr(
        spark_k8s.SparkK8sClient,
        "get_job_run",
        lambda self, job_id: _FakeResult(spark_k8s.JobRunState.RUNNING),
    )
    assert (
        probe_engine_op_state(dest, table_ref="cdp.reports.t", engine_ref="cryo-spark-abc123")
        is EngineOpState.RUNNING
    )


def test_native_live_dispatch_skipped() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    store.add_destination(DestinationConfig(id="dest", engine=EngineType.NATIVE, config={}))
    store.add_job(_running_job(engine_ref="cryo-native-abc", dispatch_id="disp-1"))
    steps.begin_step("job-1", 0, OperationType.OPTIMIZE)
    result = recover_orphaned_jobs(store, queue, locks, steps, holder_id="worker-a")
    assert result.adopted == 0
    assert result.skipped >= 1


def test_reaper_skips_started_step() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    store.add_job(_running_job(started_at=datetime(2026, 7, 1, tzinfo=UTC)))
    steps.begin_step("job-1", 0, OperationType.EXPIRE_SNAPSHOTS)
    locks.acquire_job_owner("job-1", "worker-a", 60)
    reaped = reconcile_stale_running_jobs(
        store,
        queue,
        locks,
        now=datetime(2026, 7, 27, 11, 0, tzinfo=UTC),
        stale_after_seconds=2,
        step_ledger=steps,
    )
    assert reaped == 0
    assert store.get_job("job-1").status is JobStatus.RUNNING
