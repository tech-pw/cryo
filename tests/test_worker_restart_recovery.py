"""Restart simulation: open expire step must block orphan removal."""

from __future__ import annotations

from datetime import UTC, datetime

from cryo.db.repositories.job_steps import InMemoryJobStepStore
from cryo.domain.enums import EngineType, JobStatus, JobStepStatus, Mode, OperationType
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.engines.trino.query_status import EngineOpState
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker import orphan_recovery
from cryo.worker.orphan_recovery import recover_orphaned_jobs
from cryo.worker.safety_gates import TableActivity
from cryo.worker.worker import (
    DestinationConfig,
    InMemoryJobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
    Worker,
)


class _Engine:
    type = EngineType.TRINO

    def __init__(self) -> None:
        self.actions: list[OperationType] = []

    def render(self, plan) -> list[RenderedStatement]:
        return [
            RenderedStatement(
                action=OperationType.EXPIRE_SNAPSHOTS,
                sql="expire",
                engine=EngineType.TRINO,
            ),
            RenderedStatement(
                action=OperationType.REMOVE_ORPHAN_FILES,
                sql="orphan",
                engine=EngineType.TRINO,
            ),
        ]

    def session_setup(self) -> list[str]:
        return []

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        self.actions.append(stmt.action)
        return ExecResult(ok=True, engine_ref="x", detail=stmt.sql)

    def execute_plan(self, plan, ctx: ExecCtx | None = None) -> list[ExecResult]:
        return [self.execute(s, ctx) for s in self.render(plan)]


def test_restart_does_not_start_orphan_while_expire_started(monkeypatch) -> None:
    queue = InMemoryMaintenanceQueue()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    locks = InMemoryLockStore()
    eng = _Engine()

    store.add_destination(DestinationConfig(id="dest", engine=EngineType.TRINO, config={}))
    store.add_table_data(
        "table-1",
        TableMaintenanceData(
            stats={
                "catalog_name": "cdp",
                "schema_name": "s",
                "table_name": "t",
                "total_files": 1,
                "total_bytes": 0,
                "small_files": 1,
            }
        ),
    )
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
            plan={"table_ref": "cdp.s.t"},
            operations=(OperationType.EXPIRE_SNAPSHOTS, OperationType.REMOVE_ORPHAN_FILES),
        )
    )
    steps.begin_step("job-1", 0, OperationType.EXPIRE_SNAPSHOTS, detail="expire")
    locks.acquire_table_lock("table-1", "job-1", 3600)

    monkeypatch.setattr(
        orphan_recovery,
        "probe_engine_op_state",
        lambda *a, **k: EngineOpState.RUNNING,
    )
    recover_orphaned_jobs(
        store, queue, locks, steps, holder_id="worker-b"
    )
    assert steps.get_started_step("job-1") is not None

    # Simulate reclaim: re-queue and continue — started expire must not re-run,
    # and orphan must not begin while expire is still STARTED.
    store.mark_status("job-1", JobStatus.QUEUED)
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", replica="r1")

    Worker(
        queue,
        store,
        engine_factory=lambda _d: eng,
        lock_store=locks,
        step_ledger=steps,
        table_activity_probe=lambda: TableActivity.QUIET,
        quiet_interval_sec=0,
        quiet_max_wait_sec=None,
    ).process_next()

    assert OperationType.REMOVE_ORPHAN_FILES not in eng.actions
    assert steps.get_step("job-1", 0).status is JobStepStatus.STARTED
