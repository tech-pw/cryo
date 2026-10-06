"""Identity tools and shared evidence helpers for Ask Cryo reads."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.security import Principal, Role
from cryo.db.models import Table, TableStats

_MAX_LIMIT = 200


class TableRefInput(BaseModel):
    table_id: uuid.UUID | None = None
    schema_name: str | None = None
    table_name: str | None = None
    name: str | None = None


class SearchTablesInput(BaseModel):
    query: str = ""
    limit: int = 50
    cursor: str | None = None
    include_inactive: bool = False


class EmptyInput(BaseModel):
    pass


def scope_of(ctx: ToolContext) -> dict[str, Any]:
    tenant = ctx.principal.tenant_id
    return {
        "subject": ctx.principal.subject,
        "tenant_id": str(tenant) if tenant else None,
        "timezone": ctx.timezone,
    }


def make_envelope(
    tool_name: str,
    result: dict[str, Any],
    *,
    ctx: ToolContext | None = None,
    classification: str = "observed",
    observed_at: datetime | None = None,
    units: dict[str, str] | None = None,
    coverage: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    unsupported_fields: list[str] | None = None,
    error_category: str | None = None,
    truncated: bool = False,
    row_count: int = 0,
    source_links: list[dict[str, str]] | None = None,
    source_record_ids: list[dict[str, str]] | None = None,
    measurement_method: str = "stored_scan",
) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        tool_name=tool_name,
        authorized_scope=scope_of(ctx) if ctx is not None else {},
        observed_at=observed_at,
        result=result,
        units=units or {},
        measurement_method=measurement_method,
        coverage=coverage or {},
        classification=classification,  # type: ignore[arg-type]
        source_links=source_links or [],
        source_record_ids=source_record_ids or [],
        warnings=warnings or [],
        unsupported_fields=unsupported_fields or [],
        error_category=error_category,
        truncated=truncated,
        row_count=row_count,
    )


def clamp_limit(value: int | None, *, default: int = 50, maximum: int = _MAX_LIMIT) -> int:
    if value is None:
        return default
    return max(1, min(int(value), maximum))


def parse_offset(cursor: str | None) -> int | None:
    if cursor is None or cursor == "":
        return 0
    if not str(cursor).isdigit():
        return None
    return int(cursor)


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso(value: datetime | None) -> str | None:
    normalized = as_utc(value)
    if normalized is None:
        return None
    return normalized.isoformat()


def sql_dt(session: Session, value: datetime) -> datetime:
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "sqlite" and value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def scalar_count(session: Session, stmt: Any) -> int:
    value = session.scalar(stmt)
    return int(value or 0)


def missing_fields(fields: dict[str, Any]) -> list[str]:
    return [key for key, value in fields.items() if value is None]


def candidate(table: Table) -> dict[str, str]:
    return {
        "table_id": str(table.id),
        "schema_name": table.schema_name,
        "table_name": table.table_name,
        "qualified_name": f"{table.schema_name}.{table.table_name}",
        "source_id": str(table.source_id),
    }


def table_link(table: Table) -> dict[str, str]:
    return {"rel": "table", "href": f"/tables/{table.id}"}


def _filters(
    *,
    table_id: uuid.UUID | None,
    schema_name: str | None,
    table_name: str | None,
    name: str | None,
    tenant_id: uuid.UUID | None,
    active_only: bool,
) -> list[Any] | None:
    filters: list[Any] = []
    if tenant_id is not None:
        filters.append(Table.tenant_id == tenant_id)
    if active_only:
        filters.append(Table.is_active.is_(True))
    if table_id is not None:
        filters.append(Table.id == table_id)
        return filters
    if schema_name and table_name:
        filters.append(Table.schema_name == schema_name)
        filters.append(Table.table_name == table_name)
        return filters
    raw = (name or "").strip()
    if raw and "." in raw:
        schema, table = raw.split(".", 1)
        filters.append(Table.schema_name == schema)
        filters.append(Table.table_name == table)
        return filters
    bare = table_name or raw
    if bare:
        filters.append(Table.table_name == bare)
        return filters
    return None


def find_tables(
    session: Session,
    *,
    table_id: uuid.UUID | None = None,
    schema_name: str | None = None,
    table_name: str | None = None,
    name: str | None = None,
    tenant_id: uuid.UUID | None = None,
    active_only: bool = False,
    limit: int | None = None,
) -> list[Table]:
    filters = _filters(
        table_id=table_id,
        schema_name=schema_name,
        table_name=table_name,
        name=name,
        tenant_id=tenant_id,
        active_only=active_only,
    )
    if filters is None:
        return []
    stmt = select(Table).where(*filters).order_by(Table.schema_name, Table.table_name, Table.id)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt))


def count_tables(
    session: Session,
    *,
    table_id: uuid.UUID | None = None,
    schema_name: str | None = None,
    table_name: str | None = None,
    name: str | None = None,
    tenant_id: uuid.UUID | None = None,
    active_only: bool = False,
) -> int:
    filters = _filters(
        table_id=table_id,
        schema_name=schema_name,
        table_name=table_name,
        name=name,
        tenant_id=tenant_id,
        active_only=active_only,
    )
    if filters is None:
        return 0
    return scalar_count(session, select(func.count()).select_from(Table).where(*filters))


def require_table(
    ctx: ToolContext,
    parsed: BaseModel,
    tool_name: str,
) -> tuple[Table | None, EvidenceEnvelope | None]:
    spec = TableRefInput.model_validate(parsed.model_dump())
    if spec.table_id is None and not spec.schema_name and not spec.table_name and not spec.name:
        return None, make_envelope(
            tool_name,
            {"error": "table_required"},
            ctx=ctx,
            classification="unknown",
            error_category="invalid_arguments",
            warnings=["A table id or name is required. Names are not guessed."],
        )
    total = count_tables(
        ctx.session,
        table_id=spec.table_id,
        schema_name=spec.schema_name,
        table_name=spec.table_name,
        name=spec.name,
        tenant_id=ctx.principal.tenant_id,
    )
    if total == 0:
        return None, make_envelope(
            tool_name,
            {"error": "not_found", "total": 0},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
            warnings=["No table matched. Nothing was guessed."],
            coverage={"total": 0, "returned": 0},
        )
    if total > 1:
        rows = find_tables(
            ctx.session,
            table_id=spec.table_id,
            schema_name=spec.schema_name,
            table_name=spec.table_name,
            name=spec.name,
            tenant_id=ctx.principal.tenant_id,
            limit=25,
        )
        return None, make_envelope(
            tool_name,
            {
                "ambiguous": True,
                "candidates": [candidate(table) for table in rows],
                "total": total,
            },
            ctx=ctx,
            classification="unknown",
            error_category="ambiguous",
            warnings=["Multiple tables match. Pick one; this tool will not guess."],
            coverage={"total": total, "returned": len(rows)},
            truncated=total > len(rows),
            row_count=len(rows),
        )
    found = find_tables(
        ctx.session,
        table_id=spec.table_id,
        schema_name=spec.schema_name,
        table_name=spec.table_name,
        name=spec.name,
        tenant_id=ctx.principal.tenant_id,
        limit=1,
    )
    return found[0], None


def latest_stats(session: Session, table_id: uuid.UUID) -> TableStats | None:
    return session.scalar(select(TableStats).where(TableStats.table_id == table_id))


def relation_exists(session: Session, name: str) -> bool:
    bind = session.get_bind()
    if bind is None:
        return False
    from sqlalchemy import inspect

    inspector = inspect(bind)
    if name in inspector.get_table_names(schema="cryo"):
        return True
    return name in inspector.get_table_names()


def resolve_entity(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = TableRefInput.model_validate(parsed.model_dump())
    if spec.table_id is None and not spec.schema_name and not spec.table_name and not spec.name:
        return make_envelope(
            "resolve_entity",
            {"resolved": False, "ambiguous": False, "entity": None, "candidates": []},
            ctx=ctx,
            classification="unknown",
            error_category="invalid_arguments",
            warnings=["Provide a table id or schema.table. Bare guesses are not resolved."],
        )
    total = count_tables(
        ctx.session,
        table_id=spec.table_id,
        schema_name=spec.schema_name,
        table_name=spec.table_name,
        name=spec.name,
        tenant_id=ctx.principal.tenant_id,
    )
    rows = find_tables(
        ctx.session,
        table_id=spec.table_id,
        schema_name=spec.schema_name,
        table_name=spec.table_name,
        name=spec.name,
        tenant_id=ctx.principal.tenant_id,
        limit=25,
    )
    if total == 0:
        return make_envelope(
            "resolve_entity",
            {"resolved": False, "ambiguous": False, "entity": None, "candidates": [], "total": 0},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
            warnings=["No table matched."],
            coverage={"total": 0, "returned": 0},
        )
    if total > 1:
        return make_envelope(
            "resolve_entity",
            {
                "resolved": False,
                "ambiguous": True,
                "entity": None,
                "candidates": [candidate(table) for table in rows],
                "total": total,
            },
            ctx=ctx,
            classification="unknown",
            error_category="ambiguous",
            warnings=["Multiple tables match. Candidates are returned; none was chosen."],
            coverage={"total": total, "returned": len(rows)},
            truncated=total > len(rows),
            row_count=len(rows),
        )
    table = rows[0]
    return make_envelope(
        "resolve_entity",
        {"resolved": True, "ambiguous": False, "entity": candidate(table), "candidates": []},
        ctx=ctx,
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        coverage={"total": 1, "returned": 1},
        row_count=1,
    )


def search_tables(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = SearchTablesInput.model_validate(parsed.model_dump())
    offset = parse_offset(spec.cursor)
    if offset is None:
        return make_envelope(
            "search_tables",
            {"error": "invalid_cursor"},
            ctx=ctx,
            classification="unknown",
            error_category="invalid_arguments",
            warnings=["cursor must be a non-negative integer offset."],
        )
    limit = clamp_limit(spec.limit)
    filters: list[Any] = []
    if not spec.include_inactive:
        filters.append(Table.is_active.is_(True))
    if ctx.principal.tenant_id is not None:
        filters.append(Table.tenant_id == ctx.principal.tenant_id)
    query = spec.query.strip().lower()
    if query:
        like = f"%{query}%"
        filters.append(
            or_(
                func.lower(Table.schema_name).like(like),
                func.lower(Table.table_name).like(like),
            )
        )
    count_stmt = select(func.count()).select_from(Table)
    page_stmt = select(Table).order_by(Table.schema_name, Table.table_name, Table.id)
    if filters:
        count_stmt = count_stmt.where(*filters)
        page_stmt = page_stmt.where(*filters)
    total = scalar_count(ctx.session, count_stmt)
    rows = list(ctx.session.scalars(page_stmt.offset(offset).limit(limit)))
    returned = len(rows)
    truncated = offset + returned < total
    next_cursor = str(offset + returned) if truncated else None
    return make_envelope(
        "search_tables",
        {
            "summary": "Name search over stored tables. total is a count, not the page length.",
            "tables": [candidate(table) for table in rows],
            "total": total,
            "cursor": next_cursor,
        },
        ctx=ctx,
        coverage={"total": total, "returned": returned, "offset": offset, "limit": limit},
        truncated=truncated,
        row_count=returned,
        source_record_ids=[{"table_id": str(table.id)} for table in rows],
    )


def get_capabilities(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    del parsed
    from cryo.ai.tools.registry import get_registry

    specs = get_registry().visible(ctx.principal, max_tier=2)
    tools = [
        {
            "name": spec.name,
            "family": spec.family,
            "tier": spec.tier,
            "min_role": spec.min_role.value,
            "description": spec.description,
        }
        for spec in specs
    ]
    return make_envelope(
        "get_capabilities",
        {
            "summary": "Tools visible to this role. Tier-3 actions are not tools.",
            "tools": tools,
            "tier_3_available": False,
            "total": len(tools),
        },
        ctx=ctx,
        measurement_method="registry",
        coverage={"total": len(tools), "returned": len(tools)},
        row_count=len(tools),
        warnings=["purge, force, deactivate, restore, and force-admit are UI actions, not tools."],
    )


def _viewer_spec(
    name: str,
    family: str,
    description: str,
    input_model: type[BaseModel],
    handler: Any,
    *,
    max_rows: int = 200,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        family=family,
        description=description,
        input_model=input_model,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=handler,
        max_rows=max_rows,
    )


SPECS: tuple[ToolSpec, ...] = (
    _viewer_spec(
        "resolve_entity",
        "identity",
        "Resolve a table id or schema.table. Ambiguous names return candidates.",
        TableRefInput,
        resolve_entity,
    ),
    _viewer_spec(
        "search_tables",
        "identity",
        "Search stored table names. total is a count query; the page may be truncated.",
        SearchTablesInput,
        search_tables,
    ),
    _viewer_spec(
        "get_capabilities",
        "identity",
        "List tools visible to the caller. Tier-3 actions are omitted.",
        EmptyInput,
        get_capabilities,
    ),
)


def principal_tenant(principal: Principal) -> uuid.UUID | None:
    return principal.tenant_id


class LimitInput(BaseModel):
    limit: int = 50
    cursor: str | None = None


class OptionalTableInput(TableRefInput):
    limit: int = 50
    cursor: str | None = None
    days: int = 30
    params: dict[str, Any] = Field(default_factory=dict)
