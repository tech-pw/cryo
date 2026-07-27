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
from cryo.worker.orphan_recovery import recover_orphaned_jobs
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
    r1 = recover_orphaned_jobs(
        store, queue, locks, steps, holder_id="worker-a"
    )
    r2 = recover_orphaned_jobs(
        store, queue, locks, steps, holder_id="worker-b"
    )
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
    result = recover_orphaned_jobs(
        store, queue, locks, steps, holder_id="worker-a"
    )
    assert result.waiting == 1
    assert steps.get_started_step("job-1") is not None
    assert locks.is_table_locked("table-1") or locks.acquire_table_lock(
        "table-1", "job-1", 60
    )


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
    result = recover_orphaned_jobs(
        store, queue, locks, steps, holder_id="worker-a"
    )
    assert result.resolved_steps == 1
    assert steps.get_step("job-1", 0).status is JobStepStatus.SUCCEEDED


def test_native_live_dispatch_skipped() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    store.add_destination(
        DestinationConfig(id="dest", engine=EngineType.NATIVE, config={})
    )
    store.add_job(
        _running_job(engine_ref="cryo-native-abc", dispatch_id="disp-1")
    )
    steps.begin_step("job-1", 0, OperationType.OPTIMIZE)
    result = recover_orphaned_jobs(
        store, queue, locks, steps, holder_id="worker-a"
    )
    assert result.adopted == 0
    assert result.skipped >= 1


def test_reaper_skips_started_step() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    store.add_job(
        _running_job(started_at=datetime(2026, 7, 1, tzinfo=UTC))
    )
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
