"""Migrating partitions still written under an older partition spec (#289)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.spark.sql import render as render_spark
from cryo.engines.trino.sql import UnsupportedPredicateError
from cryo.engines.trino.sql import render as render_trino
from cryo.planner.execution import build_execution_plan
from cryo.planner.guardrails import CORRECT_ORDER, MODE_OPERATIONS
from cryo.planner.planner import build_plan

GIB = 1024**3
MIGRATE = OperationType.MIGRATE_PARTITION_SPEC

DAY_FIELD = {
    "base_column": "ts",
    "partition_field": "ts_day",
    "transform": "day",
    "transform_param": None,
    "col_type": "timestamp",
}
MONTH_FIELD = {**DAY_FIELD, "partition_field": "ts_month", "transform": "month"}


def _stats(**overrides: Any) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "table_ref": "iceberg.demo.events",
        "total_size_gb": 500.0,
        "small_files_count": 0,
        "total_files": 200,
        "raw": {
            "partition_fields": [MONTH_FIELD],
            "spec_migration": {
                "default_spec_id": 1,
                "old_spec_partitions": 3,
                "old_spec_files": 30,
                "old_spec_bytes": 30 * GIB,
            },
        },
    }
    stats.update(overrides)
    return stats


def _old_day(day: int, size_gb: float = 10.0, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "partition_value": str(day),
        "partition_values": {"ts_day": day},
        "partition_spec_id": 0,
        "partition_fields": [DAY_FIELD],
        "files_per_partition": 10,
        "small_files_in_partition_count": 0,
        "partition_size_gb": size_gb,
    }
    row.update(overrides)
    return row


def _current_month(month: int) -> dict[str, Any]:
    return {
        "partition_value": str(month),
        "partition_values": {"ts_month": month},
        "partition_spec_id": 1,
        "partition_fields": [MONTH_FIELD],
        "files_per_partition": 4,
        "small_files_in_partition_count": 0,
        "partition_size_gb": 40.0,
    }


def _partitions() -> list[dict[str, Any]]:
    return [_old_day(19000), _old_day(19001), _old_day(19002), _current_month(660)]


def _requested(**params: Any) -> dict[str, Any]:
    return {"requested_operations": [MIGRATE.value], "skip_operations": [], **params}


def _migrations(plan: MaintenancePlan) -> list[Operation]:
    return [op for op in plan.operations if op.type is MIGRATE]


# --- planner -------------------------------------------------------------------------------


def test_planner_selects_only_old_spec_partitions_regardless_of_small_files() -> None:
    plan = build_plan(_stats(), _partitions(), Mode.STANDARD, _requested())

    [operation] = _migrations(plan)
    assert operation.params["rewrite_all"] is True
    assert operation.predicates is not None
    values = sorted(term.value for p in operation.predicates for term in p.terms)
    assert values == [19000, 19001, 19002]
    assert all(term.base_column == "ts" for p in operation.predicates for term in p.terms)
    assert all(term.transform.value == "day" for p in operation.predicates for term in p.terms)
    assert not any(op.type is OperationType.OPTIMIZE for op in plan.operations)


def test_planner_never_plans_migration_unless_requested() -> None:
    for mode in (Mode.LIGHT, Mode.STANDARD, Mode.DEEP):
        plan = build_plan(_stats(), _partitions(), mode, {})
        assert _migrations(plan) == []


def test_migration_is_in_no_mode_so_dedupe_never_treats_a_nightly_job_as_covering_it() -> None:
    assert all(MIGRATE not in operations for operations in MODE_OPERATIONS.values())
    order = list(CORRECT_ORDER)
    assert order.index(OperationType.REWRITE_POSITION_DELETES) < order.index(MIGRATE)
    assert order.index(MIGRATE) < order.index(OperationType.OPTIMIZE)


def test_planner_chunks_under_the_optimize_budget_and_partition_cap() -> None:
    rows = [_old_day(19000 + i, size_gb=30.0) for i in range(5)]
    plan = build_plan(
        _stats(),
        rows,
        Mode.STANDARD,
        _requested(chunk_size_gb=70, max_partitions_per_chunk=2),
    )

    chunks = [len(op.predicates or ()) for op in _migrations(plan)]
    assert chunks == [2, 2, 1]


def test_oversize_partition_runs_alone() -> None:
    rows = [_old_day(19000, size_gb=500.0), _old_day(19001, size_gb=5.0)]
    plan = build_plan(_stats(), rows, Mode.STANDARD, _requested(chunk_size_gb=100))

    assert [len(op.predicates or ()) for op in _migrations(plan)] == [1, 1]


def test_max_chunks_truncates_with_a_note() -> None:
    rows = [_old_day(19000 + i, size_gb=60.0) for i in range(4)]
    plan = build_plan(_stats(), rows, Mode.STANDARD, _requested(chunk_size_gb=100, max_chunks=2))

    assert len(_migrations(plan)) == 2
    assert any("limited to 2 of 4 old-spec partitions" in note for note in plan.notes)


def test_streaming_table_halves_the_chunk_budget_and_partition_cap() -> None:
    rows = [_old_day(19000 + i, size_gb=30.0) for i in range(4)]
    plan = build_plan(
        _stats(is_streaming=True),
        rows,
        Mode.STANDARD,
        _requested(chunk_size_gb=120, max_partitions_per_chunk=4),
    )

    assert [len(op.predicates or ()) for op in _migrations(plan)] == [2, 2]


def test_archived_and_delete_only_partitions_are_skipped() -> None:
    rows = [
        _old_day(19000, storage_class="GLACIER_IR"),
        _old_day(19001, files_per_partition=0),
        _old_day(19002),
    ]
    plan = build_plan(_stats(), rows, Mode.STANDARD, _requested())

    [operation] = _migrations(plan)
    assert [t.value for p in operation.predicates or () for t in p.terms] == [19002]
    assert any("Skipped archived partition 19000" in note for note in plan.notes)


def test_void_fields_of_a_dropped_column_do_not_block_the_filter() -> None:
    void = {**DAY_FIELD, "base_column": "region", "partition_field": "region", "transform": "void"}
    row = _old_day(19000, partition_fields=[DAY_FIELD, void])
    row["partition_values"] = {"ts_day": 19000, "region": None}
    plan = build_plan(_stats(), [row], Mode.STANDARD, _requested())

    [operation] = _migrations(plan)
    [predicate] = operation.predicates or ()
    assert [term.base_column for term in predicate.terms] == ["ts"]


def test_unrenderable_old_spec_partition_is_skipped_with_a_note() -> None:
    truncate = {**DAY_FIELD, "partition_field": "name_trunc", "transform": "truncate[4]"}
    rows = [_old_day(19000, partition_fields=[truncate], partition_values={"name_trunc": "ab"})]
    plan = build_plan(_stats(), rows, Mode.STANDARD, _requested())

    assert _migrations(plan) == []
    assert any("Skipped old-spec partition 19000 (spec 0)" in note for note in plan.notes)


def test_unpartitioned_old_spec_rewrites_the_whole_table_only_within_budget() -> None:
    row = _old_day(0, partition_fields=[], partition_values={}, partition_value="")
    small = build_plan(_stats(total_size_gb=50.0), [row], Mode.STANDARD, _requested())
    [operation] = _migrations(small)
    assert operation.predicates is None
    assert operation.params["rewrite_all"] is True

    large = build_plan(_stats(total_size_gb=500.0), [row], Mode.STANDARD, _requested())
    assert _migrations(large) == []
    assert any("no partition filter selects them" in note for note in large.notes)


def test_scan_without_spec_ids_or_old_partitions_plans_nothing() -> None:
    legacy = _stats(raw={"partition_fields": [MONTH_FIELD]})
    plan = build_plan(legacy, _partitions(), Mode.STANDARD, _requested())
    assert _migrations(plan) == []
    assert any("did not record the table's default partition spec" in n for n in plan.notes)

    migrated = _stats()
    migrated["raw"]["spec_migration"]["old_spec_partitions"] = 0
    plan = build_plan(migrated, _partitions(), Mode.STANDARD, _requested())
    assert _migrations(plan) == []
    assert any("already written under the default partition spec" in n for n in plan.notes)


def test_rows_capped_by_the_scan_are_reported() -> None:
    stats = _stats()
    stats["raw"]["spec_migration"]["old_spec_partitions"] = 10
    plan = build_plan(stats, _partitions(), Mode.STANDARD, _requested())

    assert any("kept 3 of 10 old-spec partition rows" in note for note in plan.notes)


# --- execution gates -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Destination:
    engine: EngineType
    config: dict[str, Any] = field(default_factory=dict)


def _execution_plan(engine: EngineType, **params: Any) -> MaintenancePlan:
    return build_execution_plan(
        stats=_stats(),
        partitions=_partitions(),
        mode=Mode.STANDARD,
        params={"requested_operations": [MIGRATE.value], **params},
        destination=_Destination(engine),
        operations=(MIGRATE,),
    )


def test_unapproved_migration_is_dropped_with_a_note() -> None:
    plan = _execution_plan(EngineType.SPARK_EMR)

    assert _migrations(plan) == []
    assert any("no approved request covers it" in note for note in plan.notes)


def test_approved_migration_runs_on_spark() -> None:
    plan = _execution_plan(EngineType.SPARK_EMR, approved_operations=[MIGRATE.value])

    assert len(_migrations(plan)) == 1


def test_trino_destination_drops_migration_even_when_approved() -> None:
    plan = _execution_plan(EngineType.TRINO, approved_operations=[MIGRATE.value])

    assert _migrations(plan) == []
    assert any("Route the table to a Spark destination" in note for note in plan.notes)


# --- renderers -----------------------------------------------------------------------------


def _rendered_plan() -> MaintenancePlan:
    plan = build_plan(
        _stats(),
        [_old_day(19000), _old_day(19001)],
        Mode.STANDARD,
        _requested(file_size_mb=512),
    )
    return MaintenancePlan(
        table_ref="iceberg.demo.events", mode=Mode.STANDARD, operations=tuple(_migrations(plan))
    )


def test_spark_renders_migration_with_rewrite_all_and_filters() -> None:
    [statement] = render_spark(_rendered_plan())

    assert statement.action is MIGRATE
    assert statement.sql == (
        "CALL iceberg.system.rewrite_data_files(table => 'iceberg.demo.events', "
        "strategy => 'binpack', "
        "where => '(ts >= TIMESTAMP ''2022-01-08 00:00:00'' AND "
        "ts < TIMESTAMP ''2022-01-09 00:00:00'') OR "
        "(ts >= TIMESTAMP ''2022-01-09 00:00:00'' AND "
        "ts < TIMESTAMP ''2022-01-10 00:00:00'')', "
        "options => map('target-file-size-bytes', '536870912', 'rewrite-all', 'true', "
        "'partial-progress.enabled', 'true', 'partial-progress.max-commits', '50', "
        "'max-concurrent-file-group-rewrites', '5', 'rewrite-job-order', 'files-desc'))"
    )


def test_spark_migration_never_persists_table_properties_or_a_sort() -> None:
    [statement] = render_spark(_rendered_plan())

    assert "TBLPROPERTIES" not in statement.sql
    assert "WRITE ORDERED BY" not in statement.sql
    assert "output-spec-id" not in statement.sql


def test_trino_renderer_refuses_migration() -> None:
    with pytest.raises(UnsupportedPredicateError, match="not supported on Trino"):
        render_trino(_rendered_plan())


# --- approvals and the job store -------------------------------------------------------------


def test_migration_requires_approval_by_default() -> None:
    from cryo.workflows.approvals import requires_approval

    assert requires_approval(MIGRATE) is True
    assert requires_approval(OperationType.OPTIMIZE) is False


def _seed_table(session: Any, *, old_spec_partitions: int = 3) -> uuid.UUID:
    from cryo.db.models import Source, Table, TableStats

    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4().hex[:6]}", type="rest", config={})
    session.add(source)
    session.flush()
    table = Table(
        id=uuid.uuid4(), source_id=source.id, schema_name="analytics", table_name="events"
    )
    session.add(table)
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=200,
            small_files_count=0,
            total_size_gb=500.0,
            raw={
                "spec_migration": {
                    "default_spec_id": 1,
                    "old_spec_partitions": old_spec_partitions,
                    "old_spec_files": 30,
                    "old_spec_bytes": 30 * GIB,
                }
            },
        )
    )
    session.commit()
    return table.id


def _approval(session: Any, table_id: uuid.UUID, **overrides: Any) -> Any:
    from cryo.workflows.approvals import ApprovalRequest

    values: dict[str, Any] = {
        "table_id": table_id,
        "operation_type": MIGRATE.value,
        "requested_by": "operator",
        "status": "approved",
        "approved_by": "editor",
        "approved_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(hours=24),
        "impact_preview": {},
        "operation_params": {},
    }
    values.update(overrides)
    request = ApprovalRequest(**values)
    session.add(request)
    session.commit()
    return request


def test_job_store_reads_approvals_only_for_requested_gated_operations(session: Any) -> None:
    from cryo.db.repositories.job_store import _approved_request_operations

    table_id = _seed_table(session)
    requested = {"requested_operations": [MIGRATE.value]}
    assert _approved_request_operations(session, table_id, requested) == []

    request = _approval(session, table_id)
    assert _approved_request_operations(session, table_id, requested) == [MIGRATE.value]
    assert _approved_request_operations(session, table_id, {}) == []

    request.executed_at = datetime.now(UTC)
    session.commit()
    assert _approved_request_operations(session, table_id, requested) == []


def test_job_store_ignores_pending_and_expired_approvals(session: Any) -> None:
    from cryo.db.repositories.job_store import _approved_request_operations

    table_id = _seed_table(session)
    _approval(session, table_id, status="pending", approved_by=None, approved_at=None)
    _approval(session, table_id, expires_at=datetime.now(UTC) - timedelta(hours=1))

    assert (
        _approved_request_operations(session, table_id, {"requested_operations": [MIGRATE.value]})
        == []
    )


def test_estimate_impact_reports_old_spec_counts(session: Any) -> None:
    from cryo.workflows.approvals import estimate_impact

    table_id = _seed_table(session)
    impact = estimate_impact(session, table_id, MIGRATE, {})

    assert impact["old_spec_partitions"] == 3
    assert impact["old_spec_files"] == 30
    assert impact["estimated_rewrite_gb"] == pytest.approx(30.0)


# --- API -----------------------------------------------------------------------------------


def _api(session: Any, role: str = "operator") -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cryo.api.deps import get_current_principal, get_tenant_session
    from cryo.api.routes_table_maintenance import router
    from cryo.api.security import Principal, Role
    from tests.auth_helpers import ensure_test_jwt_secret

    ensure_test_jwt_secret()
    app = FastAPI()
    app.include_router(router)

    def _session() -> Any:
        yield session

    app.dependency_overrides[get_tenant_session] = _session
    app.dependency_overrides[get_current_principal] = lambda: Principal(
        subject=f"{role}-user", roles=frozenset({Role(role)})
    )
    return TestClient(app)


def _route_to(monkeypatch: Any, session: Any, engine: str) -> None:
    from types import SimpleNamespace

    from cryo.db.models import Destination, Preset

    destination = Destination(id=uuid.uuid4(), name=f"dest-{engine}", engine=engine, config={})
    session.add(destination)
    session.flush()
    preset = Preset(
        id=uuid.uuid4(),
        name=f"preset-{engine}",
        destination_id=destination.id,
        mode="standard",
        operations=["optimize"],
        params={},
        guardrails={},
        enabled=True,
    )
    session.add(preset)
    session.commit()
    monkeypatch.setattr(
        "cryo.db.repositories.presets.resolve_effective_preset",
        lambda _session, _table: SimpleNamespace(preset=SimpleNamespace(id=str(preset.id))),
    )


def test_spec_migration_endpoint_reports_counts_and_engine(session: Any, monkeypatch: Any) -> None:
    table_id = _seed_table(session)
    _route_to(monkeypatch, session, "trino")

    body = _api(session, "viewer").get(f"/api/v1/tables/{table_id}/spec-migration").json()

    assert body["recorded"] is True
    assert body["default_spec_id"] == 1
    assert body["old_spec_partitions"] == 3
    assert body["old_spec_files"] == 30
    assert body["old_spec_bytes"] == 30 * GIB
    assert body["destination_engine"] == "trino"
    assert body["engine_supported"] is False
    assert body["approval"] is None
    assert body["last_run"] is None


def test_spec_migration_endpoint_shows_the_last_run(session: Any, monkeypatch: Any) -> None:
    from cryo.db.models import AuditLog

    table_id = _seed_table(session)
    _route_to(monkeypatch, session, "spark_emr")
    session.add(AuditLog(table_id=table_id, action=MIGRATE.value, status="OK", engine="spark"))
    session.commit()

    body = _api(session, "viewer").get(f"/api/v1/tables/{table_id}/spec-migration").json()

    assert body["engine_supported"] is True
    assert body["last_run"]["status"] == "OK"


def test_requesting_approval_is_idempotent_and_needs_old_spec_data(session: Any) -> None:
    from cryo.workflows.approvals import get_pending_approvals

    table_id = _seed_table(session)
    client = _api(session)
    first = client.post(f"/api/v1/tables/{table_id}/spec-migration/approval-request", json={})
    second = client.post(f"/api/v1/tables/{table_id}/spec-migration/approval-request", json={})

    assert first.status_code == 201
    assert first.json()["status"] == "pending"
    assert second.json()["request_id"] == first.json()["request_id"]
    [pending] = get_pending_approvals(session, table_id=table_id)
    assert pending.impact_preview["old_spec_partitions"] == 3
    body = client.get(f"/api/v1/tables/{table_id}/spec-migration").json()
    assert body["approval"]["status"] == "pending"
    assert body["baseline_old_spec_partitions"] == 3

    migrated = _seed_table(session, old_spec_partitions=0)
    response = client.post(f"/api/v1/tables/{migrated}/spec-migration/approval-request", json={})
    assert response.status_code == 409


def test_viewer_cannot_request_approval(session: Any) -> None:
    table_id = _seed_table(session)

    response = _api(session, "viewer").post(
        f"/api/v1/tables/{table_id}/spec-migration/approval-request", json={}
    )

    assert response.status_code == 403


def test_running_a_migration_without_approval_is_refused(session: Any) -> None:
    table_id = _seed_table(session)

    response = _api(session).post(
        f"/api/v1/tables/{table_id}/maintenance/operations",
        json={"operations": [MIGRATE.value]},
    )

    assert response.status_code == 409
    assert "needs an approved request" in response.json()["detail"]


def test_migration_runs_in_standard_mode() -> None:
    from cryo.api.routes_table_maintenance import mode_for_operations

    assert mode_for_operations({MIGRATE}) is Mode.STANDARD
    assert mode_for_operations({MIGRATE, OperationType.REMOVE_ORPHAN_FILES}) is Mode.DEEP


# --- advisor -------------------------------------------------------------------------------


def _advisor_snapshot(**overrides: Any) -> Any:
    from cryo.advisor.path_to_a import TableSnapshot

    values: dict[str, Any] = {
        "table_name": "events",
        "score": 92,
        "grade": "A",
        "deductions": [],
        "total_files": 200,
        "total_partitions": 20,
        "total_size_bytes": 500 * GIB,
        "total_snapshots": 5,
        "manifest_count": 4,
        "commits_per_day": 1.0,
        "is_streaming": False,
        "last_commit_at": None,
        "table_target_bytes": 512 * 1024 * 1024,
        "has_sort_order": True,
        "write_distribution_mode": "hash",
        "params": {},
        "scheduled_operations": frozenset({"optimize"}),
        "partition_fields": (),
        "partitions": [],
    }
    values.update(overrides)
    return TableSnapshot(**values)


def test_advisor_lists_old_spec_partitions_as_a_migration_step() -> None:
    from cryo.advisor.path_to_a import build_path_to_a

    path = build_path_to_a(
        _advisor_snapshot(
            spec_migration={
                "default_spec_id": 1,
                "old_spec_partitions": 3,
                "old_spec_files": 30,
                "old_spec_bytes": 30 * GIB,
            }
        )
    )

    [step] = [s for s in path.steps if s.id == "migrate_partition_spec"]
    assert step.title == "Migrate 3 partitions still written under an older partition spec"
    assert "Routine compaction" in step.detail
    assert "Maintain tab" in step.detail
    assert step.action is None
    assert step.fixes == []
    without = build_path_to_a(_advisor_snapshot())
    assert path.reachable_score == without.reachable_score


def test_advisor_has_no_migration_step_without_old_spec_data() -> None:
    from cryo.advisor.path_to_a import build_path_to_a

    path = build_path_to_a(_advisor_snapshot())

    assert all(step.id != "migrate_partition_spec" for step in path.steps)


def _seed_job(session: Any, table_id: uuid.UUID, *, preset_params: dict[str, Any]) -> Any:
    from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Preset

    destination = Destination(id=uuid.uuid4(), name="spark", engine="spark_emr", config={})
    session.add(destination)
    session.flush()
    preset = Preset(
        id=uuid.uuid4(),
        name="compact",
        destination_id=destination.id,
        mode="standard",
        operations=["optimize"],
        params=preset_params,
        guardrails={},
        enabled=True,
    )
    session.add(preset)
    session.flush()
    run = MaintenanceRun(
        id=uuid.uuid4(),
        kind="adhoc",
        preset_id=preset.id,
        status="running",
        summary={"operations": [MIGRATE.value]},
    )
    session.add(run)
    session.flush()
    job = MaintenanceJob(
        id=uuid.uuid4(),
        run_id=run.id,
        table_id=table_id,
        destination_id=destination.id,
        mode="standard",
        status="queued",
    )
    session.add(job)
    session.commit()
    return job


def test_job_record_carries_approvals_and_never_trusts_a_preset_value(session: Any) -> None:
    from cryo.db.repositories.job_store import _job_record

    table_id = _seed_table(session)
    job = _seed_job(session, table_id, preset_params={"approved_operations": [MIGRATE.value]})

    record = _job_record(session, job)
    assert record.params["requested_operations"] == [MIGRATE.value]
    assert "approved_operations" not in record.params

    _approval(session, table_id)
    record = _job_record(session, job)
    assert record.params["approved_operations"] == [MIGRATE.value]


def test_worker_partitions_carry_each_rows_spec(session: Any) -> None:
    from cryo.db.models import TablePartition
    from cryo.db.repositories.job_store import _latest_partitions

    table_id = _seed_table(session)
    session.add(
        TablePartition(
            table_id=table_id,
            scan_at=datetime.now(UTC),
            partition_value="19000",
            partition_key="spec=0/ts_day=19000",
            partition_values={"ts_day": 19000},
            partition_fields=[DAY_FIELD],
            partition_spec_id=0,
            files_per_partition=10,
            small_files_in_partition_count=0,
            partition_size_gb=10.0,
        )
    )
    session.commit()

    [row] = _latest_partitions(session, table_id)
    assert row["partition_spec_id"] == 0
    assert row["partition_fields"] == [DAY_FIELD]


def test_native_destination_routes_migration_to_its_sql_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cryo.engines.native.routing import resolve_native_routing

    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    plan = MaintenancePlan(
        table_ref="rest.demo.events", mode=Mode.STANDARD, operations=(Operation(MIGRATE, {}),)
    )

    routed = resolve_native_routing(
        plan, {}, {"engine_impl": "go", "sql_fallback_destination_id": "spark-1"}
    )
    assert not routed.native_operations
    [(operation, detail)] = routed.fallback_operations
    assert operation.type is MIGRATE
    assert "route to Spark" in detail

    skipped = resolve_native_routing(plan, {}, {"engine_impl": "go"})
    assert [op.type for op, _ in skipped.skipped_operations] == [MIGRATE]
