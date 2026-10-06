"""Write-ahead ordering in the worker statement loop."""

from __future__ import annotations

from cryo.db.repositories.job_steps import InMemoryJobStepStore
from cryo.domain.enums import EngineType, JobStatus, JobStepStatus, Mode, OperationType
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.safety_gates import TableActivity
from cryo.worker.worker import (
    DestinationConfig,
    InMemoryJobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
    Worker,
)


class _RecordingEngine:
    type = EngineType.TRINO

    def __init__(self) -> None:
        self.execute_calls: list[OperationType] = []
        self.saw_engine_ref_callback = False

    def render(self, plan) -> list[RenderedStatement]:
        return [
            RenderedStatement(
                action=OperationType.EXPIRE_SNAPSHOTS,
                sql="ALTER TABLE t EXECUTE expire_snapshots(...)",
                engine=EngineType.TRINO,
            ),
            RenderedStatement(
                action=OperationType.REMOVE_ORPHAN_FILES,
                sql="ALTER TABLE t EXECUTE remove_orphan_files(...)",
                engine=EngineType.TRINO,
            ),
        ]

    def session_setup(self) -> list[str]:
        return []

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        self.execute_calls.append(stmt.action)
        ref = f"ref-{len(self.execute_calls)}"
        if ctx is not None and ctx.on_engine_ref is not None:
            ctx.on_engine_ref(ref)
            self.saw_engine_ref_callback = True
        return ExecResult(ok=True, engine_ref=ref, detail=stmt.sql)

    def execute_plan(self, plan, ctx: ExecCtx | None = None) -> list[ExecResult]:
        return [self.execute(s, ctx) for s in self.render(plan)]


def test_begin_step_before_execute() -> None:
    order: list[str] = []

    class OrderEngine(_RecordingEngine):
        def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
            order.append("execute")
            return super().execute(stmt, ctx)

    queue = InMemoryMaintenanceQueue()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    locks = InMemoryLockStore()

    orig_begin = steps.begin_step

    def tracking_begin(*args, **kwargs):
        order.append("begin")
        return orig_begin(*args, **kwargs)

    steps.begin_step = tracking_begin  # type: ignore[method-assign]

    store.add_destination(DestinationConfig(id="dest", engine=EngineType.TRINO, config={}))
    store.add_table_data(
        "table-1",
        TableMaintenanceData(
            stats={
                "catalog_name": "sample",
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
            status=JobStatus.QUEUED,
            operations=(OperationType.EXPIRE_SNAPSHOTS, OperationType.REMOVE_ORPHAN_FILES),
        )
    )
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", replica="r1")
    locks.acquire_table_lock("table-1", "job-1", 3600)

    eng = OrderEngine()
    result = Worker(
        queue,
        store,
        engine_factory=lambda _d: eng,
        lock_store=locks,
        step_ledger=steps,
        table_activity_probe=lambda: TableActivity.QUIET,
        quiet_interval_sec=0,
        quiet_max_wait_sec=None,
    ).process_next()

    assert result is not None
    assert result.status is JobStatus.SUCCEEDED
    assert order[:2] == ["begin", "execute"]
    assert steps.get_step("job-1", 0).status is JobStepStatus.SUCCEEDED


def test_skip_already_succeeded_steps() -> None:
    queue = InMemoryMaintenanceQueue()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    locks = InMemoryLockStore()
    steps.begin_step("job-1", 0, OperationType.EXPIRE_SNAPSHOTS, detail="x")
    steps.complete_step("job-1", 0, JobStepStatus.SUCCEEDED)

    store.add_destination(DestinationConfig(id="dest", engine=EngineType.TRINO, config={}))
    store.add_table_data(
        "table-1",
        TableMaintenanceData(
            stats={
                "catalog_name": "sample",
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
            status=JobStatus.QUEUED,
            operations=(OperationType.EXPIRE_SNAPSHOTS, OperationType.REMOVE_ORPHAN_FILES),
        )
    )
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", replica="r1")
    locks.acquire_table_lock("table-1", "job-1", 3600)

    eng = _RecordingEngine()
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

    assert eng.execute_calls == [OperationType.REMOVE_ORPHAN_FILES]


def test_active_table_blocks_dangerous_begin() -> None:
    queue = InMemoryMaintenanceQueue()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    locks = InMemoryLockStore()
    store.add_destination(DestinationConfig(id="dest", engine=EngineType.TRINO, config={}))
    store.add_table_data(
        "table-1",
        TableMaintenanceData(
            stats={
                "catalog_name": "sample",
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
            status=JobStatus.QUEUED,
            operations=(OperationType.EXPIRE_SNAPSHOTS,),
        )
    )
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", replica="r1")
    locks.acquire_table_lock("table-1", "job-1", 3600)

    eng = _RecordingEngine()
    result = Worker(
        queue,
        store,
        engine_factory=lambda _d: eng,
        lock_store=locks,
        step_ledger=steps,
        table_activity_probe=lambda: TableActivity.ACTIVE,
        quiet_interval_sec=0,
        quiet_max_wait_sec=0.0,
    ).process_next()

    assert result is not None
    assert eng.execute_calls == []
    assert steps.get_started_step("job-1") is None
