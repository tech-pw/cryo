from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.ai.assistant.coordinator import _invoke_tool
from cryo.ai.assistant.tool_scope import _scope_statement, install_tenant_guard
from cryo.ai.tools.bootstrap import register_builtin_tools
from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.registry import ToolContext, ToolSpec, get_registry
from cryo.api.security import Principal, Role
from cryo.config import Settings, get_settings
from cryo.db.models import (
    AuditLog,
    Destination,
    LayoutRecommendation,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    QueryLog,
    Source,
    Table,
    TableStats,
    Tenant,
)
from cryo.db.tenant_scope import assert_resource_tenant, bind_tenant

_NO_TENANT_QUERY = frozenset({"get_capabilities", "search_runbooks", "get_feature_help"})
_KIND_FIELDS = {
    "table_id": "table",
    "table_ids": "table",
    "left_table_id": "table",
    "right_table_id": "table",
    "source_id": "source",
    "run_id": "run",
    "job_id": "job",
    "recommendation_id": "recommendation",
}


def _kind(name: str) -> str | None:
    if name in _KIND_FIELDS:
        return _KIND_FIELDS[name]
    if name.endswith("_table_id"):
        return "table"
    if name.endswith("_run_id"):
        return "run"
    if name.endswith("_job_id"):
        return "job"
    if name.endswith("_source_id"):
        return "source"
    if name.endswith("_recommendation_id"):
        return "recommendation"
    return None


def _id_fields(model: type[BaseModel]) -> list[str]:
    return [name for name in model.model_fields if _kind(name)]


def _patch_isolation(monkeypatch: pytest.MonkeyPatch, enforce: bool) -> None:
    settings = get_settings().model_copy(update={"enforce_tenant_isolation": enforce})
    monkeypatch.setattr("cryo.db.tenant_scope.get_settings", lambda: settings)
    monkeypatch.setattr("cryo.config.get_settings", lambda: settings)


def _ctx(session: Session, tenant_id: uuid.UUID) -> ToolContext:
    return ToolContext(
        session=session,
        principal=Principal(
            subject="tenant-a",
            roles=frozenset({Role.OPERATOR}),
            tenant_id=tenant_id,
        ),
        settings=Settings(),
        now=datetime.now(UTC),
        timezone="UTC",
    )


def _seed(session: Session) -> dict[str, uuid.UUID]:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    session.add_all(
        [
            Tenant(
                id=tenant_a,
                name=f"A {tenant_a.hex[:8]}",
                slug=f"a-{tenant_a.hex[:8]}",
                admin_user_id="a",
            ),
            Tenant(
                id=tenant_b,
                name=f"B {tenant_b.hex[:8]}",
                slug=f"b-{tenant_b.hex[:8]}",
                admin_user_id="b",
            ),
        ]
    )
    source_a, source_b = uuid.uuid4(), uuid.uuid4()
    dest_a, dest_b = uuid.uuid4(), uuid.uuid4()
    table_a, table_b = uuid.uuid4(), uuid.uuid4()
    preset_a, preset_b = uuid.uuid4(), uuid.uuid4()
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
    job_a, job_b = uuid.uuid4(), uuid.uuid4()
    recommendation_b = uuid.uuid4()
    now = datetime.now(UTC)
    session.add_all(
        [
            Source(
                id=source_a,
                name=f"src-a-{source_a.hex[:8]}",
                type="rest",
                config={},
                enabled=True,
                tenant_id=tenant_a,
            ),
            Source(
                id=source_b,
                name=f"src-b-{source_b.hex[:8]}",
                type="rest",
                config={},
                enabled=True,
                tenant_id=tenant_b,
            ),
            Destination(
                id=dest_a,
                name=f"dest-a-{dest_a.hex[:8]}",
                engine="trino",
                config={},
                enabled=True,
                tenant_id=tenant_a,
            ),
            Destination(
                id=dest_b,
                name=f"dest-b-{dest_b.hex[:8]}",
                engine="trino",
                config={},
                enabled=True,
                tenant_id=tenant_b,
            ),
        ]
    )
    session.add_all(
        [
            Table(
                id=table_a,
                source_id=source_a,
                schema_name="raw",
                table_name="events_a",
                is_active=True,
                tenant_id=tenant_a,
            ),
            Table(
                id=table_b,
                source_id=source_b,
                schema_name="raw",
                table_name="events_b",
                is_active=True,
                tenant_id=tenant_b,
            ),
            Preset(
                id=preset_a,
                name=f"preset-a-{preset_a.hex[:8]}",
                destination_id=dest_a,
                mode="standard",
                operations=["optimize"],
                params={},
                guardrails={},
                schedule_cron="0 2 * * *",
                enabled=True,
                tenant_id=tenant_a,
            ),
            Preset(
                id=preset_b,
                name=f"preset-b-{preset_b.hex[:8]}",
                destination_id=dest_b,
                mode="standard",
                operations=["optimize"],
                params={},
                guardrails={},
                schedule_cron="0 2 * * *",
                enabled=True,
                tenant_id=tenant_b,
            ),
        ]
    )
    session.add_all(
        [
            _stats(table_a, now, 10.0),
            _stats(table_b, now, 99.0),
            MaintenanceRun(id=run_a, kind="manual", preset_id=preset_a, status="running"),
            MaintenanceRun(id=run_b, kind="manual", preset_id=preset_b, status="running"),
            MaintenanceJob(
                id=job_a,
                run_id=run_a,
                table_id=table_a,
                destination_id=dest_a,
                mode="standard",
                status="queued",
            ),
            MaintenanceJob(
                id=job_b,
                run_id=run_b,
                table_id=table_b,
                destination_id=dest_b,
                mode="standard",
                status="queued",
            ),
            AuditLog(
                table_id=table_a,
                action="optimize",
                status="OK",
                actual_bytes_freed=100,
                ts=now,
            ),
            AuditLog(
                table_id=table_b,
                action="optimize",
                status="OK",
                actual_bytes_freed=999,
                ts=now,
            ),
            QueryLog(
                source_id=source_a,
                table_id=table_a,
                query_id=f"q-{table_a.hex[:8]}",
                engine="trino",
                executed_at=now,
            ),
            QueryLog(
                source_id=source_b,
                table_id=table_b,
                query_id=f"q-{table_b.hex[:8]}",
                engine="trino",
                executed_at=now,
            ),
            LayoutRecommendation(
                id=recommendation_b,
                table_id=table_b,
                recommended_strategy="sort",
                recommended_columns=[{"name": "day"}],
            ),
        ]
    )
    session.commit()
    bind_tenant(session, tenant_a)
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "preset": preset_a,
        "table": table_a,
        "source": source_a,
        "run": run_a,
        "job": job_a,
        "recommendation": recommendation_b,
        "destination_b": dest_b,
        "preset_b": preset_b,
        "table_b": table_b,
        "source_b": source_b,
        "run_b": run_b,
        "job_b": job_b,
        "recommendation_b": recommendation_b,
    }


def _stats(table_id: uuid.UUID, scan_at: datetime, modeled: float) -> TableStats:
    return TableStats(
        table_id=table_id,
        scan_at=scan_at,
        needs_compaction=False,
        compaction_priority="LOW",
        grade="B",
        overall_score=80,
        total_files=10,
        small_files_count=1,
        total_size_gb=1.0,
        estimated_monthly_s3_cost_usd=modeled,
    )


def _foreign_ids(seeded: dict[str, uuid.UUID]) -> set[str]:
    return {
        str(seeded["tenant_b"]),
        str(seeded["table_b"]),
        str(seeded["source_b"]),
        str(seeded["run_b"]),
        str(seeded["job_b"]),
        str(seeded["destination_b"]),
        str(seeded["preset_b"]),
        str(seeded["recommendation_b"]),
    }


def _safe_args(model: type[BaseModel]) -> dict[str, object]:
    args: dict[str, object] = {}
    if "action" in model.model_fields:
        args["action"] = "scan_table"
    if "query" in model.model_fields and model.model_fields["query"].is_required():
        args["query"] = "maintenance"
    return args


def _foreign_args(model: type[BaseModel], seeded: dict[str, uuid.UUID]) -> dict[str, object]:
    args = _safe_args(model)
    for name in _id_fields(model):
        kind = _kind(name)
        assert kind is not None
        foreign_key = {
            "table": "table_b",
            "source": "source_b",
            "run": "run_b",
            "job": "job_b",
            "recommendation": "recommendation_b",
        }[kind]
        value: object = str(seeded[foreign_key])
        if name.endswith("s") and name != "source_id":
            value = [value]
        args[name] = value
    return args


def _blob(envelope: EvidenceEnvelope) -> str:
    return json.dumps(envelope.model_dump(mode="json"))


def _no_rows(result: object) -> bool:
    if isinstance(result, list):
        return not result
    if not isinstance(result, dict):
        return result in (None, "")
    return all(_no_rows(value) for value in result.values())


@pytest.mark.parametrize("enforce", [True, False])
def test_every_registered_tool_stays_in_tenant(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    enforce: bool,
) -> None:
    _patch_isolation(monkeypatch, enforce)
    install_tenant_guard()
    register_builtin_tools()
    seeded = _seed(session)
    if enforce:
        with pytest.raises(HTTPException):
            assert_resource_tenant(session, seeded["tenant_b"], resource_type="table")
    else:
        assert_resource_tenant(session, seeded["tenant_b"], resource_type="table")
    ctx = _ctx(session, seeded["tenant_a"])
    registry = get_registry()
    assert registry.specs
    foreign = _foreign_ids(seeded)
    for spec in registry.specs.values():
        if _id_fields(spec.input_model):
            denied = registry.call(spec.name, ctx, _foreign_args(spec.input_model, seeded))
            assert denied.error_category == "forbidden", (
                spec.name,
                denied.error_category,
                denied.warnings,
            )
            assert denied.row_count == 0
            assert _no_rows(denied.result), spec.name
        if spec.tier >= 1:
            continue
        listed = registry.call(spec.name, ctx, _safe_args(spec.input_model))
        leaked = [item for item in foreign if item in _blob(listed)]
        assert not leaked, (spec.name, leaked, listed.error_category, listed.warnings)
        if spec.name in _NO_TENANT_QUERY:
            assert listed.error_category in {None, "abstain"}
    runs = registry.call("list_runs", ctx, {})
    assert str(seeded["run"]) in _blob(runs)
    assert str(seeded["run_b"]) not in _blob(runs)
    health = registry.call("get_lake_health", ctx, {})
    assert health.result["active_tables"] == 1
    assert health.result["sources_enabled"] == 1
    queue = registry.call("get_queue_summary", ctx, {})
    assert queue.result["job_total"] == 1
    engines = registry.call("get_engine_status", ctx, {})
    assert engines.result["destination_total"] == 1
    assert str(seeded["destination_b"]) not in _blob(engines)
    workflows = registry.call("list_workflows", ctx, {})
    assert str(seeded["preset_b"]) not in _blob(workflows)
    assert str(seeded["preset"]) in _blob(workflows)
    cost = registry.call("get_cost_evidence", ctx, {})
    assert cost.result["modeled"]["estimated_monthly_s3_cost_usd"] == 10.0
    activity = registry.call("get_query_activity", ctx, {})
    assert activity.result["query_log_rows"] == 1
    tables = registry.call("search_tables", ctx, {"query": "events"})
    names = {row["table_name"] for row in tables.result["tables"]}
    assert names == {"events_a"}


def test_scope_statement_count_select_from_join_and_subquery() -> None:
    tenant_id = uuid.uuid4()
    counted = _scope_statement(select(func.count()).select_from(Source), tenant_id)
    counted_sql = _sql(counted)
    assert "cryo.sources.tenant_id" in counted_sql
    assert "cryo.tables" not in counted_sql

    table_count = _scope_statement(select(func.count()).select_from(Table), tenant_id)
    assert "cryo.tables.tenant_id" in _sql(table_count)

    joined = _scope_statement(
        select(func.count()).select_from(Table).join(TableStats, TableStats.table_id == Table.id),
        tenant_id,
    )
    joined_sql = _sql(joined)
    assert "cryo.tables.tenant_id" in joined_sql
    assert "cryo.table_stats.table_id" in joined_sql

    outer = _scope_statement(
        select(func.count())
        .select_from(Table)
        .outerjoin(TableStats, TableStats.table_id == Table.id),
        tenant_id,
    )
    outer_sql = _sql(outer)
    assert "cryo.tables.tenant_id" in outer_sql
    assert "cryo.table_stats.table_id" in outer_sql

    nested = _scope_statement(
        select(func.count()).select_from(select(Source.id).subquery()),
        tenant_id,
    )
    nested_sql = _sql(nested)
    inner = nested_sql[nested_sql.index("FROM (") : nested_sql.rindex(") AS")]
    assert "cryo.sources.tenant_id" in inner
    assert "cryo.sources" not in nested_sql[nested_sql.rindex(") AS") :]


def _sql(statement: object) -> str:
    return " ".join(str(statement).split())


def test_read_session_copies_tenant_scope(session: Session) -> None:
    install_tenant_guard()
    tenant_id = uuid.uuid4()
    bind_tenant(session, tenant_id)

    class EmptyIn(BaseModel):
        pass

    def handler(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
        from cryo.db.tenant_scope import current_tenant_id

        seen = current_tenant_id(ctx.session)
        return EvidenceEnvelope(
            tool_name="_tenant_probe",
            result={"tenant_id": str(seen) if seen else None},
            classification="observed",
        )

    registry = get_registry()
    registry.register(
        ToolSpec(
            name="_tenant_probe",
            family="test",
            description="probe",
            input_model=EmptyIn,
            output_model=EvidenceEnvelope,
            tier=0,
            min_role=Role.VIEWER,
            handler=handler,
        )
    )
    ctx = ToolContext(
        session=session,
        principal=Principal(subject="probe", roles=frozenset({Role.VIEWER})),
        settings=Settings(),
        now=datetime.now(UTC),
        timezone="UTC",
    )
    try:
        envelope = asyncio.run(_invoke_tool(session, registry, ctx, "_tenant_probe", {}, 5))
    finally:
        registry.specs.pop("_tenant_probe", None)
    assert envelope.result["tenant_id"] == str(tenant_id)
