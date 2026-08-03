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


def test_worker_uses_catalog_binding_override_in_plan_table_ref() -> None:
    from uuid import uuid4

    source_id = str(uuid4())
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "dest",
            engine="trino",
            config={
                "catalog": "session_default",
                "catalog_bindings": [
                    {"source_id": source_id, "trino_catalog": "iceberg"},
                ],
            },
        )
    )
    store.add_table_data(
        "table-1",
        TableMaintenanceData(
            stats={
                "source_id": source_id,
                "catalog": "rest",
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

    result = Worker(queue, store, lambda destination: FakeMaintenanceEngine(), locks).process_next()

    assert result is not None
    assert result.plan is not None
    assert result.plan["table_ref"] == "iceberg.demo.events"


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


def test_worker_deactivates_table_when_catalog_reports_it_gone() -> None:
    """Regression, 2026-07-31: a bulk maintenance run kept scheduling (and failing) against
    tables already dropped from the catalog, because nothing reconciled Table.is_active
    against real catalog state. A failure whose error contains a NoSuchTableException-style
    signature should flip the table inactive so future scans/maintenance skip it."""
    from cryo.engines.base import ExecCtx

    class _DroppedTableEngine(FakeMaintenanceEngine):
        def execute(self, stmt, ctx: ExecCtx | None = None):
            raise RuntimeError(
                "Spark-on-K8s Job FAILED: ...\nCaused by: "
                "org.apache.spark.sql.catalyst.analysis.NoSuchTableException: "
                "[TABLE_OR_VIEW_NOT_FOUND] The table or view x.y cannot be found."
            )

    queue, locks, store = _worker_fixture()
    engine = _DroppedTableEngine()

    result = Worker(queue, store, lambda destination: engine, locks).process_next()

    assert result is not None
    assert result.status is JobStatus.FAILED
    assert "table-1" in store.deactivated_tables
    assert "NoSuchTableException" in store.deactivated_tables["table-1"]


def test_worker_does_not_deactivate_table_on_unrelated_failure() -> None:
    queue, locks, store = _worker_fixture()
    engine = FakeMaintenanceEngine(fail_actions={OperationType.OPTIMIZE})

    result = Worker(queue, store, lambda destination: engine, locks).process_next()

    assert result is not None
    assert result.status is JobStatus.FAILED
    assert store.deactivated_tables == {}


def test_skip_operations_from_preset_derives_the_complement() -> None:
    from cryo.worker.worker import _skip_operations_from_preset

    skipped = _skip_operations_from_preset(
        (OperationType.OPTIMIZE, OperationType.OPTIMIZE_MANIFESTS)
    )
    assert set(skipped) == {"expire_snapshots", "remove_orphan_files"}


def test_skip_operations_from_preset_empty_when_no_preset_operations_configured() -> None:
    """None/() means the preset never explicitly set operations -- must not skip anything,
    preserving the pre-existing default behavior for every preset that doesn't use this."""
    from cryo.worker.worker import _skip_operations_from_preset

    assert _skip_operations_from_preset(None) == []
    assert _skip_operations_from_preset(()) == []


def test_worker_derives_skip_operations_from_job_operations_end_to_end() -> None:
    """A preset that only lists optimize/optimize_manifests must actually produce a plan
    without expire_snapshots/remove_orphan_files -- not just pass ABAC/PII validation with
    the field, then silently ignore it at plan-build time."""
    queue, locks, store = _worker_fixture()
    store.jobs["job-1"].operations = (OperationType.OPTIMIZE, OperationType.OPTIMIZE_MANIFESTS)
    store.jobs["job-1"].mode = Mode.DEEP
    engine = FakeMaintenanceEngine()

    result = Worker(queue, store, lambda destination: engine, locks).process_next()

    assert result is not None
    assert result.plan is not None
    op_types = [op["type"] for op in result.plan["operations"]]
    assert "expire_snapshots" not in op_types
    assert "remove_orphan_files" not in op_types


def test_worker_calls_with_table_stats_for_non_native_engines_too() -> None:
    """Regression, 2026-08-03: this hook was gated to `engine.type is EngineType.NATIVE`, so
    SparkK8sEngine.with_table_stats (table-size-aware executor sizing -- see its docstring)
    never actually ran even though the engine defines it. Any engine defining the hook should
    get it, matching how with_source_config is already applied unconditionally just above."""
    from cryo.domain.enums import EngineType

    class _SizeAwareFakeEngine(FakeMaintenanceEngine):
        type = EngineType.SPARK_EMR

        def __init__(self) -> None:
            super().__init__(engine_type=EngineType.SPARK_EMR)
            self.received_stats = None

        def with_table_stats(self, stats):
            self.received_stats = stats
            return self

    queue, locks, store = _worker_fixture()
    engine = _SizeAwareFakeEngine()

    Worker(queue, store, lambda destination: engine, locks).process_next()

    assert engine.received_stats is not None
    assert engine.received_stats["total_size_gb"] == 10


def test_default_engine_factory_injects_destination_id_into_config() -> None:
    """destination.config (the raw DB blob) never carries the destination's own id --
    engines that need it for K8s Job labeling (native's job_dispatch, Spark-K8s's live
    capacity probe) read config["destination_id"], so default_engine_factory must inject
    it rather than leave every engine to rediscover it. Regression for a gap found while
    wiring Spark-K8s's live capacity probe: SparkK8sEngine had no way to stamp its
    dispatch Jobs with a destination label without this."""
    from cryo.worker.worker import default_engine_factory

    destination = DestinationConfig(
        "dest-abc", engine="spark_emr", config={"submit_mode": "k8s", "slot_cap": 3}
    )
    engine = default_engine_factory(destination)
    assert engine.config["destination_id"] == "dest-abc"
    # The original destination.config dict itself must stay untouched (no aliasing).
    assert "destination_id" not in destination.config


def test_default_engine_factory_does_not_override_explicit_destination_id() -> None:
    from cryo.worker.worker import default_engine_factory

    destination = DestinationConfig(
        "dest-abc",
        engine="spark_emr",
        config={"submit_mode": "k8s", "slot_cap": 3, "destination_id": "explicit-override"},
    )
    engine = default_engine_factory(destination)
    assert engine.config["destination_id"] == "explicit-override"
