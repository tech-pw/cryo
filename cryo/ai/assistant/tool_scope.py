"""Tenant guard for Ask Cryo tool calls."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select
from sqlalchemy.sql.visitors import replacement_traverse

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.registry import ToolContext, ToolRegistry, _role_ok, _trim_result
from cryo.db.base import Base
from cryo.db.models import (
    JobStep,
    LayoutRecommendation,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    Source,
    Table,
)
from cryo.db.tenant_scope import (
    assert_resource_tenant,
    bind_tenant,
    current_tenant_id,
    scope_by_tenant,
)

_FIELD_KIND = {
    "table_id": "table",
    "table_ids": "table",
    "left_table_id": "table",
    "right_table_id": "table",
    "source_id": "source",
    "run_id": "run",
    "job_id": "job",
    "recommendation_id": "recommendation",
}


def forbidden_envelope(tool_name: str) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        tool_name=tool_name,
        classification="unknown",
        error_category="forbidden",
        warnings=["Resource is outside this tenant."],
        result={},
        row_count=0,
    )


def install_tenant_guard() -> None:
    current = ToolRegistry.call
    if getattr(current, "_cryo_tenant_guard", False):
        return

    def guarded(
        self: ToolRegistry,
        name: str,
        ctx: ToolContext,
        arguments: dict[str, Any],
    ) -> EvidenceEnvelope:
        return _guarded(current, self, name, ctx, arguments)

    guarded._cryo_tenant_guard = True  # type: ignore[attr-defined]
    ToolRegistry.call = guarded  # type: ignore[method-assign]


def ensure_bound(ctx: ToolContext) -> uuid.UUID | None:
    if ctx.session is None:
        return None
    bound = current_tenant_id(ctx.session)
    if bound is None and ctx.principal.tenant_id is not None:
        bind_tenant(ctx.session, ctx.principal.tenant_id)
        bound = ctx.principal.tenant_id
    return bound


class TenantSession:
    """Query proxy. Writes delegate to the real session; reads are tenant-scoped."""

    def __init__(self, session: Session, tenant_id: uuid.UUID) -> None:
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_tenant_id", tenant_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return self._session.execute(_scope_statement(statement, self._tenant_id), *args, **kwargs)

    def scalars(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return self._session.scalars(_scope_statement(statement, self._tenant_id), *args, **kwargs)

    def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        return self._session.scalar(_scope_statement(statement, self._tenant_id), *args, **kwargs)

    def get(self, entity: Any, ident: Any, **kwargs: Any) -> Any:
        obj = self._session.get(entity, ident, **kwargs)
        if obj is None or not _attributable(entity):
            return obj
        owner = _resource_tenant(self._session, obj)
        if owner != self._tenant_id:
            return None
        return obj


def _guarded(
    original: Callable[[ToolRegistry, str, ToolContext, dict[str, Any]], EvidenceEnvelope],
    registry: ToolRegistry,
    name: str,
    ctx: ToolContext,
    arguments: dict[str, Any],
) -> EvidenceEnvelope:
    spec = registry.specs.get(name)
    if spec is None or spec.tier >= 3 or ctx.session is None:
        return original(registry, name, ctx, arguments)
    tenant_id = ensure_bound(ctx)
    if tenant_id is not None:
        refusal = _refuse_arguments(ctx.session, spec.name, arguments)
        if refusal is not None:
            return refusal
    if not _role_ok(ctx.principal, spec.min_role):
        return original(registry, name, ctx, arguments)
    try:
        parsed = spec.input_model.model_validate(arguments)
    except ValidationError:
        return original(registry, name, ctx, arguments)
    if tenant_id is not None:
        ctx = _replace_session(ctx, TenantSession(ctx.session, tenant_id))
    try:
        envelope = spec.handler(ctx, parsed)
    except HTTPException:
        return forbidden_envelope(spec.name)
    except Exception as exc:  # noqa: BLE001 - tool failures stay data
        return EvidenceEnvelope(
            tool_name=name,
            classification="unknown",
            error_category="tool_error",
            warnings=[type(exc).__name__],
            result={"error": "tool_error"},
        )
    if not isinstance(envelope, EvidenceEnvelope):
        raise TypeError("tool did not return evidence")
    _trim_result(envelope, spec.max_rows)
    return envelope


def _replace_session(ctx: ToolContext, session: Any) -> ToolContext:
    from dataclasses import replace

    return replace(ctx, session=session)


def _kind(key: str) -> str | None:
    if key in _FIELD_KIND:
        return _FIELD_KIND[key]
    if key.endswith("_table_id") or key == "table_ids":
        return "table"
    if key.endswith("_run_id"):
        return "run"
    if key.endswith("_job_id"):
        return "job"
    if key.endswith("_source_id"):
        return "source"
    if key.endswith("_recommendation_id"):
        return "recommendation"
    return None


def _refuse_arguments(
    session: Session,
    tool_name: str,
    arguments: dict[str, Any],
) -> EvidenceEnvelope | None:
    return _refuse_mapping(session, tool_name, arguments)


def _refuse_mapping(
    session: Session,
    tool_name: str,
    value: dict[str, Any],
) -> EvidenceEnvelope | None:
    for key, item in value.items():
        kind = _kind(key)
        if kind is not None:
            refused = _refuse_value(session, tool_name, kind, item)
            if refused is not None:
                return refused
            continue
        if isinstance(item, dict):
            refused = _refuse_mapping(session, tool_name, item)
            if refused is not None:
                return refused
    return None


def _refuse_value(
    session: Session,
    tool_name: str,
    kind: str,
    value: Any,
) -> EvidenceEnvelope | None:
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, dict):
            refused = _refuse_mapping(session, tool_name, item)
            if refused is not None:
                return refused
            continue
        parsed = _as_uuid(item)
        if parsed is None:
            continue
        if _is_foreign(session, kind, parsed):
            return forbidden_envelope(tool_name)
    return None


def _as_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _is_foreign(session: Session, kind: str, resource_id: uuid.UUID) -> bool:
    obj = _load(session, kind, resource_id)
    if obj is None:
        return False
    owner = _resource_tenant(session, obj)
    tenant_id = current_tenant_id(session)
    if tenant_id is None:
        return False
    try:
        assert_resource_tenant(session, owner, resource_type=kind)
    except HTTPException:
        return True
    return owner != tenant_id


def _load(session: Session, kind: str, resource_id: uuid.UUID) -> Any:
    if kind == "table":
        return session.get(Table, resource_id)
    if kind == "source":
        return session.get(Source, resource_id)
    if kind == "run":
        return session.get(MaintenanceRun, resource_id)
    if kind == "job":
        return session.get(MaintenanceJob, resource_id)
    if kind == "recommendation":
        return session.get(LayoutRecommendation, resource_id)
    return None


def _attributable(entity: Any) -> bool:
    model = entity if isinstance(entity, type) else type(entity)
    if model in {MaintenanceRun, MaintenanceJob, JobStep}:
        return True
    return any(hasattr(model, name) for name in ("tenant_id", "table_id", "job_id"))


def _resource_tenant(session: Session, obj: Any) -> uuid.UUID | None:
    if isinstance(obj, MaintenanceRun):
        return _run_tenant(session, obj)
    if isinstance(obj, MaintenanceJob):
        table = session.get(Table, obj.table_id)
        return table.tenant_id if table is not None else None
    if isinstance(obj, JobStep):
        job = session.get(MaintenanceJob, obj.job_id)
        if job is None:
            return None
        table = session.get(Table, job.table_id)
        return table.tenant_id if table is not None else None
    direct: uuid.UUID | None = getattr(obj, "tenant_id", None)
    if direct is not None:
        return direct
    table_id = getattr(obj, "table_id", None)
    if table_id is None:
        return getattr(obj, "tenant_id", None)
    table = session.get(Table, table_id)
    return table.tenant_id if table is not None else None


def _run_tenant(session: Session, run: MaintenanceRun) -> uuid.UUID | None:
    if run.preset_id is not None:
        preset = session.get(Preset, run.preset_id)
        if preset is not None and preset.tenant_id is not None:
            return preset.tenant_id
    table_id = session.scalar(
        select(MaintenanceJob.table_id).where(MaintenanceJob.run_id == run.id).limit(1)
    )
    if table_id is None:
        return None
    table = session.get(Table, table_id)
    return table.tenant_id if table is not None else None


def _scope_statement(statement: Any, tenant_id: uuid.UUID) -> Any:
    if tenant_id.int == 0 or not isinstance(statement, Select):
        return statement
    statement = _scope_nested_selects(statement, tenant_id)
    models = _outer_models(statement)
    clauses: list[Any] = []
    scoped = statement
    for model in models:
        if model is MaintenanceRun:
            clauses.append(_run_clause(tenant_id))
        elif model is JobStep:
            clauses.append(JobStep.job_id.in_(_job_ids(tenant_id)))
        elif hasattr(model, "tenant_id"):
            scoped = scope_by_tenant(scoped, model, tenant_id)
        elif hasattr(model, "table_id") and hasattr(model, "job_id"):
            clauses.append(
                or_(
                    model.table_id.in_(_table_ids(tenant_id)),
                    model.job_id.in_(_job_ids(tenant_id)),
                )
            )
        elif hasattr(model, "table_id") and hasattr(model, "source_id"):
            clauses.append(
                or_(
                    model.table_id.in_(_table_ids(tenant_id)),
                    model.source_id.in_(_source_ids(tenant_id)),
                )
            )
        elif hasattr(model, "table_id"):
            clauses.append(model.table_id.in_(_table_ids(tenant_id)))
        elif hasattr(model, "job_id"):
            clauses.append(model.job_id.in_(_job_ids(tenant_id)))
        elif hasattr(model, "source_id"):
            clauses.append(model.source_id.in_(_source_ids(tenant_id)))
    if clauses:
        scoped = scoped.where(*clauses)
    return scoped


def _table_ids(tenant_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    return select(Table.id).where(Table.tenant_id == tenant_id)


def _source_ids(tenant_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    return select(Source.id).where(Source.tenant_id == tenant_id)


def _job_ids(tenant_id: uuid.UUID) -> Select[tuple[uuid.UUID]]:
    return select(MaintenanceJob.id).where(MaintenanceJob.table_id.in_(_table_ids(tenant_id)))


def _run_clause(tenant_id: uuid.UUID) -> Any:
    via_job = select(MaintenanceJob.run_id).where(
        MaintenanceJob.table_id.in_(_table_ids(tenant_id))
    )
    via_preset = (
        select(MaintenanceRun.id)
        .join(Preset, Preset.id == MaintenanceRun.preset_id)
        .where(Preset.tenant_id == tenant_id)
    )
    return or_(MaintenanceRun.id.in_(via_job), MaintenanceRun.id.in_(via_preset))


def _scope_nested_selects(statement: Select[Any], tenant_id: uuid.UUID) -> Select[Any]:
    def replace(element: Any, **_kwargs: Any) -> Any | None:
        if isinstance(element, Select) and element is not statement:
            return _scope_statement(element, tenant_id)
        return None

    opts: dict[str, Any] = {}
    rewritten = replacement_traverse(statement, opts, replace)
    if isinstance(rewritten, Select):
        return rewritten
    return statement


def _outer_models(statement: Select[Any]) -> set[type[Any]]:
    found: set[type[Any]] = set()
    for desc in statement.column_descriptions:
        _add_model(desc.get("entity"), found)
    for frm in statement.get_final_froms():
        _walk_from(frm, found)
    return found


def _walk_from(frm: Any, found: set[type[Any]]) -> None:
    if frm is None:
        return
    if type(frm).__name__ in {"Join", "_ORMJoin"} or getattr(frm, "_is_join", False):
        _walk_from(getattr(frm, "left", None), found)
        _walk_from(getattr(frm, "right", None), found)
        return
    _add_model(_annotated_entity(frm), found)
    _add_model(_model_for_table(frm), found)


def _annotated_entity(frm: Any) -> Any:
    annotations = getattr(frm, "_annotations", None)
    if not annotations:
        return None
    return annotations.get("parententity") or annotations.get("parentmapper")


def _model_for_table(frm: Any) -> type[Any] | None:
    for mapper in Base.registry.mappers:
        local = mapper.local_table
        selectable = mapper.persist_selectable
        if frm is local or frm is selectable:
            mapped = mapper.class_
            if isinstance(mapped, type):
                return mapped
    return None


def _add_model(entity: Any, found: set[type[Any]]) -> None:
    model = _mapped_class(entity)
    if model is not None:
        found.add(model)


def _mapped_class(entity: Any) -> type[Any] | None:
    if isinstance(entity, type):
        return entity if _is_mapped_model(entity) else None
    if entity is None:
        return None
    mapper = getattr(entity, "mapper", None)
    mapped = getattr(mapper, "class_", None)
    if isinstance(mapped, type):
        return mapped
    mapped = getattr(entity, "class_", None)
    if isinstance(mapped, type) and _is_mapped_model(mapped):
        return mapped
    return None


def _is_mapped_model(model: type[Any]) -> bool:
    return hasattr(model, "__tablename__") or hasattr(model, "__mapper__")
