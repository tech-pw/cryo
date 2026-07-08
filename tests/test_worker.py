from __future__ import annotations

from cryo.domain.enums import JobStatus, Mode, OperationType
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import (
    DestinationConfig,
    FakeMaintenanceEngine,
    InMemoryJobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
    Worker,
)


def test_worker_builds_plan_executes_statements_and_marks_success() -> None:
    queue, locks, store = _worker_fixture()
    engine = FakeMaintenanceEngine()

    result = Worker(queue, store, lambda destination: engine, locks).process_next()

    assert result is not None
    assert result.status is JobStatus.SUCCEEDED
    assert queue.status("job-1") is JobStatus.SUCCEEDED
    assert result.plan is not None
    assert [operation["type"] for operation in result.plan["operations"]] == [
        "expire_snapshots",
        "optimize",
    ]
    assert [record.action for record in store.audit] == [
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.OPTIMIZE,
    ]
    assert all(record.status == "OK" for record in store.audit)
    assert not locks.is_table_locked("table-1")


def test_worker_records_actual_bytes_freed_when_engine_returns_them() -> None:
    queue, locks, store = _worker_fixture()
    engine = FakeMaintenanceEngine(
        bytes_freed_by_action={
            OperationType.EXPIRE_SNAPSHOTS: 1024**3,
            OperationType.OPTIMIZE: 512 * 1024**2,
        }
    )

    Worker(queue, store, lambda destination: engine, locks).process_next()

    assert [record.actual_bytes_freed for record in store.audit] == [1024**3, 512 * 1024**2]


def test_worker_marks_failed_when_engine_statement_fails() -> None:
    queue, locks, store = _worker_fixture()
    engine = FakeMaintenanceEngine(fail_actions={OperationType.OPTIMIZE})

    result = Worker(queue, store, lambda destination: engine, locks).process_next()

    assert result is not None
    assert result.status is JobStatus.FAILED
    assert result.error == "fake engine failed optimize"
    assert queue.status("job-1") is JobStatus.FAILED
    assert [record.status for record in store.audit] == ["OK", "FAILED"]
    assert not locks.is_table_locked("table-1")


def test_worker_continues_when_engine_skips_unsupported_operation() -> None:
    queue, locks, store = _worker_fixture()
    engine = FakeMaintenanceEngine(skip_actions={OperationType.OPTIMIZE})

    result = Worker(queue, store, lambda destination: engine, locks).process_next()

    assert result is not None
    assert result.status is JobStatus.SUCCEEDED
    assert [record.status for record in store.audit] == ["OK", "SKIPPED"]
    assert not locks.is_table_locked("table-1")


class TestChunkCheckpointing:
    def test_worker_marks_partial_when_some_chunks_fail_with_checkpointing(
        self,
    ) -> None:
        from cryo.domain.enums import EngineType
        from cryo.engines.base import ExecCtx, ExecResult

        class PartialFakeEngine:
            type = EngineType.NATIVE

            def __init__(self) -> None:
                self.call_count = 0

            def with_source_config(self, config):
                return self

            def with_table_stats(self, stats):
                return self

            def render(self, plan):
                from cryo.engines.base import RenderedStatement

                return [
                    RenderedStatement(
                        action=op.type,
                        sql=f"FAKE {op.type.value}",
                        engine=EngineType.NATIVE,
                    )
                    for op in plan.operations
                ]

            def session_setup(self):
                return []

            def execute_plan(self, plan, ctx: ExecCtx | None = None):
                self.call_count += 1
                assert ctx is not None
                assert ctx.chunk_checkpointing is True
                assert ctx.session_factory is not None
                return [
                    ExecResult(ok=True, detail="chunk 0 ok", duration_ms=100),
                    ExecResult(ok=False, error="chunk 1 failed", duration_ms=50),
                    ExecResult(ok=True, detail="chunk 2 ok", duration_ms=80),
                ]

        queue, locks, store = _worker_fixture(
            chunk_checkpointing=True,
            engine_impl="go",
            sql_fallback_destination_id="dest",
        )
        engine = PartialFakeEngine()

        result = Worker(queue, store, lambda destination: engine, locks).process_next()

        assert result is not None
        assert result.status is JobStatus.PARTIAL
        assert "chunk 1 failed" in (result.error or "")

    def test_worker_returns_failed_when_all_chunks_fail_with_checkpointing(
        self,
    ) -> None:
        from cryo.domain.enums import EngineType
        from cryo.engines.base import ExecCtx, ExecResult

        class AllFailFakeEngine:
            type = EngineType.NATIVE

            def with_source_config(self, config):
                return self

            def with_table_stats(self, stats):
                return self

            def render(self, plan):
                from cryo.engines.base import RenderedStatement

                return [
                    RenderedStatement(
                        action=op.type,
                        sql=f"FAKE {op.type.value}",
                        engine=EngineType.NATIVE,
                    )
                    for op in plan.operations
                ]

            def session_setup(self):
                return []

            def execute_plan(self, plan, ctx: ExecCtx | None = None):
                return [
                    ExecResult(ok=False, error="chunk 0 failed", duration_ms=50),
                    ExecResult(ok=False, error="chunk 1 failed", duration_ms=50),
                ]

        queue, locks, store = _worker_fixture(
            chunk_checkpointing=True,
            engine_impl="go",
            sql_fallback_destination_id="dest",
        )
        engine = AllFailFakeEngine()

        result = Worker(queue, store, lambda destination: engine, locks).process_next()

        assert result is not None
        assert result.status is JobStatus.FAILED


def test_worker_uses_destination_catalog_in_plan_table_ref() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig("dest", config={"catalog": "cdp", "host": "trino.example"})
    )
    store.add_table_data(
        "table-1",
        TableMaintenanceData(
            stats={
                "schema_name": "admin",
                "table_name": "orphan_files_removal_audit_log",
                "table_ref": "iceberg.admin.orphan_files_removal_audit_log",
                "small_files_count": 500,
                "total_size_gb": 10,
            },
            partitions=[],
        ),
    )
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            params={"min_small_files": 10, "chunk_size_gb": 100},
            status=JobStatus.ADMITTED,
        )
    )
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", "r1")

    result = Worker(queue, store, lambda destination: FakeMaintenanceEngine(), locks).process_next()

    assert result is not None
    assert result.plan is not None
    assert result.plan["table_ref"] == "cdp.admin.orphan_files_removal_audit_log"


def _worker_fixture(
    chunk_checkpointing: bool = False,
    engine_impl: str | None = None,
    sql_fallback_destination_id: str | None = None,
) -> tuple[InMemoryMaintenanceQueue, InMemoryLockStore, InMemoryJobStore]:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    config: dict = {}
    if chunk_checkpointing:
        config["chunk_checkpointing"] = True
    if engine_impl:
        config["engine_impl"] = engine_impl
    if sql_fallback_destination_id:
        config["sql_fallback_destination_id"] = sql_fallback_destination_id
    store.add_destination(DestinationConfig("dest", config=config))
    store.add_table_data(
        "table-1",
        TableMaintenanceData(
            stats={
                "schema_name": "demo",
                "table_name": "events",
                "small_files_count": 500,
                "total_size_gb": 10,
            },
            partitions=[],
        ),
    )
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            params={"min_small_files": 10, "chunk_size_gb": 100},
            status=JobStatus.ADMITTED,
        )
    )
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", "r1")
    return queue, locks, store
