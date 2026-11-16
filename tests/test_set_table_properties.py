"""The set_table_properties operation (#560): planner, renderers, sizing, retry, worker."""

from __future__ import annotations

import pytest

from cryo.domain.enums import EngineType, JobStatus, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines import spark as spark_engine
from cryo.engines import trino as trino_engine
from cryo.engines.spark import sql as spark_sql
from cryo.engines.spark.k8s_engine import is_spark_retryable_catalog_error
from cryo.engines.spark.sizing import LOCAL_MODE_OPS, cap_dispatch_profile
from cryo.engines.trino import sql as trino_sql
from cryo.planner.execution import build_execution_plan
from cryo.planner.guardrails import CORRECT_ORDER, MODE_OPERATIONS
from cryo.planner.table_properties import (
    TablePropertiesPlanError,
    build_property_plan,
    is_property_job,
)
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import (
    DestinationConfig,
    InMemoryJobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
    Worker,
)

OP = OperationType.SET_TABLE_PROPERTIES
CHANGE = {"table_properties": {"set": {"write.distribution-mode": "hash"}, "remove": ["x.y"]}}


def _plan(**params: object) -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="lake.analytics.events",
        mode=Mode.MANIFEST_ONLY,
        operations=(Operation(OP, dict(params)),),
    )


# --- not maintenance -------------------------------------------------------------------


def test_set_table_properties_is_in_no_mode_and_not_in_correct_order() -> None:
    assert OP not in CORRECT_ORDER
    assert all(OP not in ops for ops in MODE_OPERATIONS.values())


# --- planner ---------------------------------------------------------------------------


def test_property_plan_emits_one_set_and_one_remove_operation() -> None:
    plan = build_property_plan("lake.analytics.events", Mode.MANIFEST_ONLY, CHANGE)
    assert [(op.type, op.params) for op in plan.operations] == [
        (OP, {"set": {"write.distribution-mode": "hash"}}),
        (OP, {"remove": ["x.y"]}),
    ]


def test_property_plan_refuses_an_empty_change() -> None:
    with pytest.raises(TablePropertiesPlanError):
        build_property_plan("t", Mode.LIGHT, {"table_properties": {"set": {}, "remove": []}})
    with pytest.raises(TablePropertiesPlanError):
        build_property_plan("t", Mode.LIGHT, {})


def test_execution_plan_for_a_property_job_never_adds_maintenance() -> None:
    stats = {
        "schema_name": "analytics",
        "table_name": "events",
        "small_files_count": 5000,
        "total_size_gb": 900,
        "total_snapshots": 900,
    }
    plan = build_execution_plan(
        stats=stats,
        partitions=[],
        mode=Mode.MANIFEST_ONLY,
        params={**CHANGE, "requested_operations": [OP.value]},
        destination=DestinationConfig("d", engine=EngineType.SPARK_EMR, config={"catalog": "lake"}),
        operations=(OP,),
    )
    assert plan.table_ref == "lake.analytics.events"
    assert {op.type for op in plan.operations} == {OP}
    assert is_property_job([OP.value]) and not is_property_job(["optimize"])


# --- renderers (golden) ----------------------------------------------------------------


def test_spark_renders_set_table_properties_golden() -> None:
    plan = MaintenancePlan(
        table_ref="lake.analytics.events",
        mode=Mode.MANIFEST_ONLY,
        operations=(
            Operation(OP, {"set": {"write.distribution-mode": "hash", "format-version": "3"}}),
            Operation(OP, {"remove": ["write.metadata.delete-after-commit.enabled"]}),
        ),
    )
    rendered = spark_sql.render(plan)
    assert [stmt.sql for stmt in rendered] == [
        "ALTER TABLE lake.analytics.events SET TBLPROPERTIES "
        "('format-version' = '3', 'write.distribution-mode' = 'hash')",
        "ALTER TABLE lake.analytics.events UNSET TBLPROPERTIES IF EXISTS "
        "('write.metadata.delete-after-commit.enabled')",
    ]
    assert all(stmt.action is OP and stmt.engine is EngineType.SPARK_EMR for stmt in rendered)


def test_spark_set_table_properties_escapes_values() -> None:
    (stmt,) = spark_sql.render(_plan(set={"comment": "it's \\ ok"}))
    assert stmt.sql == (
        "ALTER TABLE lake.analytics.events SET TBLPROPERTIES ('comment' = 'it\\'s \\\\ ok')"
    )


def test_spark_refuses_an_operation_with_both_set_and_remove() -> None:
    with pytest.raises(ValueError):
        spark_sql.render(_plan(set={"a": "1"}, remove=["b"]))


def test_trino_renders_set_table_properties_golden() -> None:
    (stmt,) = trino_sql.render(_plan(set={"commit.retry.num-retries": "10", "format-version": "3"}))
    assert stmt.sql == (
        "ALTER TABLE lake.analytics.events SET PROPERTIES max_commit_retry = 10, format_version = 3"
    )
    assert stmt.action is OP and stmt.engine is EngineType.TRINO


def test_trino_refuses_removals_and_unmapped_keys() -> None:
    with pytest.raises(trino_sql.UnsupportedPredicateError):
        trino_sql.render(_plan(remove=["commit.retry.num-retries"]))
    with pytest.raises(ValueError):
        trino_sql.render(_plan(set={"write.distribution-mode": "hash"}))


def test_both_engines_list_the_operation_as_supported() -> None:
    assert OP in spark_engine.SUPPORTED_OPS
    assert OP in trino_engine.SUPPORTED_OPS


def test_native_destination_is_never_a_property_route() -> None:
    from unittest.mock import MagicMock

    from cryo.table_config.engine_route import choose_route

    native = MagicMock(enabled=True, engine="native", config={})
    assert choose_route(MagicMock(id="s"), [native], ["a"], []) is None


# --- sizing and retry ------------------------------------------------------------------


def test_spark_sizing_is_small_and_fixed_whatever_the_table_size() -> None:
    from cryo.engines.spark.sizing import ExecutorProfile

    big = ExecutorProfile(
        executor_instances=20,
        executor_cpu_milli=4000,
        executor_mem_mib=16384,
        driver_cpu_milli=4000,
        driver_mem_mib=16384,
    )
    small = cap_dispatch_profile(big, operation=OP.value, total_size_gb=5000)
    assert small == cap_dispatch_profile(big, operation=OP.value, total_size_gb=1)
    assert small.executor_instances == 1 and small.driver_mem_mib == 1024
    assert OP.value in LOCAL_MODE_OPS


def test_spark_does_not_retry_a_rejected_property_change() -> None:
    validation = "org.apache.iceberg.exceptions.ValidationException: Cannot downgrade v3 to v2"
    assert is_spark_retryable_catalog_error(validation, "optimize_manifests")
    assert not is_spark_retryable_catalog_error(validation, OP.value)
    assert not is_spark_retryable_catalog_error(
        "org.apache.spark.sql.AnalysisException: bad key", OP.value
    )
    assert is_spark_retryable_catalog_error(
        "org.apache.iceberg.exceptions.CommitFailedException: conflict", OP.value
    )


# --- worker ----------------------------------------------------------------------------


class _SparkEngine:
    type = EngineType.SPARK_EMR

    def __init__(self) -> None:
        self.executed: list[str] = []

    def render(self, plan: MaintenancePlan):
        return spark_sql.render(plan)

    def session_setup(self) -> list[str]:
        return []

    def execute(self, stmt, ctx=None):
        from cryo.engines.base import ExecResult

        self.executed.append(stmt.sql)
        return ExecResult(ok=True, detail=stmt.sql)


def test_worker_runs_a_property_job_and_audits_it_as_set_table_properties() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig("dest", engine=EngineType.SPARK_EMR, config={"catalog": "lake"})
    )
    store.add_table_data(
        "table-1",
        TableMaintenanceData(
            stats={
                "schema_name": "analytics",
                "table_name": "events",
                "small_files_count": 5000,
                "total_size_gb": 900,
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
            mode=Mode.MANIFEST_ONLY,
            params={**CHANGE, "requested_operations": [OP.value]},
            status=JobStatus.ADMITTED,
            operations=(OP,),
        )
    )
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", "r1")
    engine = _SparkEngine()

    result = Worker(queue, store, lambda _d: engine, locks).process_next()

    assert result is not None and result.status is JobStatus.SUCCEEDED
    assert engine.executed == [
        "ALTER TABLE lake.analytics.events SET TBLPROPERTIES ('write.distribution-mode' = 'hash')",
        "ALTER TABLE lake.analytics.events UNSET TBLPROPERTIES IF EXISTS ('x.y')",
    ]
    assert [record.action for record in store.audit] == [OP, OP]
    assert all(record.status == "OK" for record in store.audit)
    assert not locks.is_table_locked("table-1")


# --- scheduling ------------------------------------------------------------------------


def test_a_property_job_skips_the_roi_gate() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from cryo.governor.governor import Governor

    governor = object.__new__(Governor)
    governor._roi_gate_enabled = True
    governor._roi_gate_shadow_mode = False
    governor._roi_gate = MagicMock()
    job = SimpleNamespace(id="j", requested_operations=(OP.value,))
    wave = SimpleNamespace(session=object(), destination_id="d")
    assert governor._gate_roi(job, wave) is None
    governor._roi_gate.decide.assert_not_called()


def test_a_finished_property_job_does_not_start_the_nightly_cooldown(session) -> None:
    import uuid
    from datetime import UTC, datetime

    from sqlalchemy import select

    from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
    from cryo.maintenance.selection import job_starts_cooldown

    source = Source(id=uuid.uuid4(), name="s", type="rest", config={})
    destination = Destination(id=uuid.uuid4(), name="d", engine="spark_emr", config={})
    session.add_all([source, destination])
    session.flush()
    table = Table(id=uuid.uuid4(), source_id=source.id, schema_name="a", table_name="t")
    run = MaintenanceRun(id=uuid.uuid4(), kind="adhoc", status="succeeded")
    session.add_all([table, run])
    session.flush()
    now = datetime.now(UTC)
    jobs = {
        kind: MaintenanceJob(
            id=uuid.uuid4(),
            run_id=run.id,
            table_id=table.id,
            destination_id=destination.id,
            mode="manifest_only" if kind == "property" else "standard",
            operations=[OP.value] if kind == "property" else None,
            dedupe_key=OP.value if kind == "property" else None,
            status="succeeded",
            finished_at=now,
            plan={"operations": []},
        )
        for kind in ("property", "maintenance")
    }
    session.add_all(list(jobs.values()))
    session.flush()
    ids = set(session.scalars(select(MaintenanceJob.id).where(job_starts_cooldown())))
    assert ids == {jobs["maintenance"].id}
