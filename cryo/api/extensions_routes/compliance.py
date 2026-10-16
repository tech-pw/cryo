"""Compliance, snapshot protection, MCP, and catalog-latency routes."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.api.deps import SessionDep, require_admin, require_operator, require_viewer
from cryo.api.extensions_helpers import build_mcp_server, table_stats_dict
from cryo.api.security import Principal, Role
from cryo.api.snapshot_presence import (
    CatalogMetadataError,
    load_current_snapshot_ids,
    parse_snapshot_id,
)
from cryo.compliance.governance import track_pending_deletions
from cryo.db.models import AuditLog, Source, Table, TableStats
from cryo.db.repositories.protected_snapshots import ProtectedSnapshotRepository
from cryo.sources.latency import get_catalog_latency_monitor

# No prefix here: included into extensions.py's `router`, which already has prefix="/api/v1".
router = APIRouter(tags=["compliance"])


class ComplianceStatusResponse(BaseModel):
    table_id: uuid.UUID
    table_ref: str
    pending_deletion: dict[str, Any] | None = None
    abac_policy_count: int = 0
    abac_enforcement_note: str | None = None


class ProtectSnapshotRequest(BaseModel):
    snapshot_id: str
    label: str | None = None

    @field_validator("snapshot_id", mode="before")
    @classmethod
    def decimal_snapshot_id(cls, value: object) -> str:
        parsed = parse_snapshot_id(value)
        if parsed is None:
            raise ValueError("snapshot_id must be a non-negative decimal string")
        return str(parsed)


class ProtectSnapshotResponse(BaseModel):
    table_id: uuid.UUID
    snapshot_id: str
    label: str | None = None


class ProtectedSnapshotEntry(BaseModel):
    snapshot_id: str
    label: str | None = None
    protected_at: str
    protected_by: str | None = None


class ProtectedSnapshotListResponse(BaseModel):
    table_id: uuid.UUID
    snapshots: list[ProtectedSnapshotEntry]


class UnprotectSnapshotResponse(BaseModel):
    table_id: uuid.UUID
    snapshot_id: str
    removed: bool


class RollbackSnapshotRequest(BaseModel):
    snapshot_id: str
    # Required, not just a UI confirm dialog: rollback moves the table's current pointer
    # backwards, which is the one write in this file that loses data other writers may
    # already depend on (anything committed after the target snapshot stops being "current").
    confirm: bool = False

    @field_validator("snapshot_id", mode="before")
    @classmethod
    def decimal_snapshot_id(cls, value: object) -> str:
        parsed = parse_snapshot_id(value)
        if parsed is None:
            raise ValueError("snapshot_id must be a non-negative decimal string")
        return str(parsed)


class RollbackSnapshotResponse(BaseModel):
    table_id: uuid.UUID
    snapshot_id: str
    previous_snapshot_id: str | None = None


class CatalogLatencyResponse(BaseModel):
    source_id: uuid.UUID
    source_name: str
    operations: dict[str, dict[str, float]]


class ABACPolicyRead(BaseModel):
    id: int
    table_id: uuid.UUID
    role: str
    has_row_filter: bool
    masked_columns: list[str]
    allowed_operations: list[str]


class ABACPolicyListResponse(BaseModel):
    items: list[ABACPolicyRead]
    total: int
    enforcement_note: str


class McpToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


class McpToolCallResponse(BaseModel):
    result: Any


@router.get(
    "/mcp/tools",
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def mcp_list_tools(session: SessionDep) -> list[dict[str, str]]:
    return build_mcp_server(session).list_tools()


@router.post(
    "/mcp/tools/call",
    response_model=McpToolCallResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def mcp_call_tool(
    payload: McpToolCallRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_viewer)],
) -> McpToolCallResponse:
    required_roles: dict[str, Role] = {
        "get_table_health": Role.VIEWER,
        "list_stale_tables": Role.VIEWER,
        "list_compaction_backlog": Role.VIEWER,
        "explain_health_change": Role.VIEWER,
        "simulate_preset": Role.VIEWER,
        "filter_translation": Role.VIEWER,
        "get_table_schema": Role.VIEWER,
        "get_snapshot_history": Role.VIEWER,
        "run_preset": Role.OPERATOR,
    }
    required_role = required_roles.get(payload.name)
    if required_role is None:
        raise HTTPException(status_code=400, detail=f"Unsupported tool: {payload.name}")
    if not principal.can(required_role):
        raise HTTPException(
            status_code=403,
            detail=f"Tool {payload.name} requires {required_role.value} role",
        )

    server = build_mcp_server(session)
    table_id = None
    raw_table_id = payload.arguments.get("table_id")
    if isinstance(raw_table_id, str):
        try:
            table_id = uuid.UUID(raw_table_id)
        except ValueError:
            table_id = None

    try:
        result = server.call(payload.name, payload.arguments)
    except KeyError as exc:
        session.add(
            AuditLog(
                table_id=table_id,
                action=f"mcp.{payload.name}",
                status="ERROR",
                engine="mcp",
                detail=f"tool_not_found: {exc}",
                actor=principal.subject,
            )
        )
        session.commit()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        session.add(
            AuditLog(
                table_id=table_id,
                action=f"mcp.{payload.name}",
                status="ERROR",
                engine="mcp",
                detail=f"tool_error: {exc}",
                actor=principal.subject,
            )
        )
        session.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.add(
        AuditLog(
            table_id=table_id,
            action=f"mcp.{payload.name}",
            status="OK",
            engine="mcp",
            detail=f"argument_keys={sorted(payload.arguments.keys())}",
            actor=principal.subject,
        )
    )
    session.commit()
    return McpToolCallResponse(result=result)


@router.get(
    "/tables/{table_id}/compliance",
    response_model=ComplianceStatusResponse,
    dependencies=[Depends(require_viewer)],
)
def get_compliance_status(table_id: uuid.UUID, session: SessionDep) -> ComplianceStatusResponse:
    from cryo.security.abac import ABACPolicy

    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
    stats_dict = table_stats_dict(table, stats)
    pending = track_pending_deletions(
        f"iceberg.{table.schema_name}.{table.table_name}",
        stats_dict,
    )
    abac_count = len(
        session.scalars(select(ABACPolicy).where(ABACPolicy.table_id == table_id)).all()
    )
    abac_note = (
        "ABAC maintenance enforcement active; scan-time row/column filters require catalog proxy."
        if abac_count
        else None
    )
    return ComplianceStatusResponse(
        table_id=table_id,
        table_ref=f"iceberg.{table.schema_name}.{table.table_name}",
        pending_deletion=(
            {
                "pending_rows": pending.pending_rows,
                "blocked_until": pending.blocked_until.isoformat()
                if pending.blocked_until
                else None,
                "reason": pending.reason,
            }
            if pending
            else None
        ),
        abac_policy_count=abac_count,
        abac_enforcement_note=abac_note,
    )


def _source_view(source: Source) -> dict[str, Any]:
    return {"name": source.name, "type": source.type, "config": dict(source.config or {})}


def _protected_by(session: Session, table_id: uuid.UUID, snapshot_id: int) -> str | None:
    actor = session.scalar(
        select(AuditLog.actor)
        .where(
            AuditLog.table_id == table_id,
            AuditLog.action == "snapshot.protect",
            AuditLog.detail == f"snapshot_id={snapshot_id}",
        )
        .order_by(AuditLog.ts.desc())
        .limit(1)
    )
    if not isinstance(actor, str) or not actor.strip():
        return None
    return actor


@router.post(
    "/tables/{table_id}/protected-snapshots",
    response_model=ProtectSnapshotResponse,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
def protect_snapshot(
    table_id: uuid.UUID,
    payload: ProtectSnapshotRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_operator)],
) -> ProtectSnapshotResponse:
    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    source = session.get(Source, table.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    snapshot_id = parse_snapshot_id(payload.snapshot_id)
    if snapshot_id is None:
        raise HTTPException(
            status_code=400, detail="snapshot_id must be a non-negative decimal string"
        )
    try:
        present = load_current_snapshot_ids(
            _source_view(source), table.schema_name, table.table_name
        )
    except CatalogMetadataError as exc:
        raise HTTPException(
            status_code=503, detail=f"Catalog metadata read failed: {exc}"[:500]
        ) from exc
    if snapshot_id not in present:
        raise HTTPException(status_code=404, detail="Snapshot not found in current table metadata")
    repo = ProtectedSnapshotRepository(session)
    row = repo.protect(
        table_id=table_id,
        snapshot_id=snapshot_id,
        label=payload.label,
    )
    session.add(
        AuditLog(
            table_id=table_id,
            action="snapshot.protect",
            status="OK",
            detail=f"snapshot_id={snapshot_id}",
            actor=principal.subject,
        )
    )
    session.commit()
    return ProtectSnapshotResponse(
        table_id=table_id,
        snapshot_id=str(row.snapshot_id),
        label=row.label,
    )


@router.get(
    "/tables/{table_id}/protected-snapshots",
    response_model=ProtectedSnapshotListResponse,
    dependencies=[Depends(require_viewer)],
)
def list_protected_snapshots(
    table_id: uuid.UUID,
    session: SessionDep,
) -> ProtectedSnapshotListResponse:
    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")
    repo = ProtectedSnapshotRepository(session)
    rows = repo.list_for_table(table_id)
    return ProtectedSnapshotListResponse(
        table_id=table_id,
        snapshots=[
            ProtectedSnapshotEntry(
                snapshot_id=str(row.snapshot_id),
                label=row.label,
                protected_at=row.protected_at.isoformat() if row.protected_at is not None else "",
                protected_by=_protected_by(session, table_id, row.snapshot_id),
            )
            for row in rows
        ],
    )


@router.delete(
    "/tables/{table_id}/protected-snapshots/{snapshot_id}",
    response_model=UnprotectSnapshotResponse,
    dependencies=[Depends(require_operator)],
)
def unprotect_snapshot(
    table_id: uuid.UUID,
    snapshot_id: str,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_operator)],
) -> UnprotectSnapshotResponse:
    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")
    parsed = parse_snapshot_id(snapshot_id)
    if parsed is None:
        raise HTTPException(
            status_code=400, detail="snapshot_id must be a non-negative decimal string"
        )
    repo = ProtectedSnapshotRepository(session)
    removed = repo.unprotect(table_id=table_id, snapshot_id=parsed)
    if not removed:
        raise HTTPException(status_code=404, detail="Protected snapshot not found")
    session.add(
        AuditLog(
            table_id=table_id,
            action="snapshot.unprotect",
            status="OK",
            detail=f"snapshot_id={parsed}",
            actor=principal.subject,
        )
    )
    session.commit()
    return UnprotectSnapshotResponse(
        table_id=table_id,
        snapshot_id=str(parsed),
        removed=True,
    )


@router.post(
    "/tables/{table_id}/snapshots/{snapshot_id}/rollback",
    response_model=RollbackSnapshotResponse,
    dependencies=[Depends(require_admin)],
)
def rollback_table_snapshot(
    table_id: uuid.UUID,
    snapshot_id: str,
    payload: RollbackSnapshotRequest,
    session: SessionDep,
    principal: Annotated[Principal, Depends(require_admin)],
) -> RollbackSnapshotResponse:
    """Move the table's current snapshot pointer back to ``snapshot_id``.

    Admin-gated, unlike protect/unprotect: this changes what every reader sees as
    "the table" going forward, not just what Cryo retains. Loads the table fresh
    immediately before the write (not from any cached metadata) so the commit is
    against the real current state -- a stale load here would otherwise look like
    a successful rollback that silently reverted someone else's commit too.
    """
    parsed = parse_snapshot_id(payload.snapshot_id)
    if parsed is None or str(parsed) != snapshot_id:
        raise HTTPException(
            status_code=400, detail="snapshot_id must be a non-negative decimal string"
        )
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="confirm must be true to roll back a table")

    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    source = session.get(Source, table.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    try:
        present = load_current_snapshot_ids(
            _source_view(source), table.schema_name, table.table_name
        )
    except CatalogMetadataError as exc:
        raise HTTPException(
            status_code=503, detail=f"Catalog metadata read failed: {exc}"[:500]
        ) from exc
    if parsed not in present:
        raise HTTPException(status_code=404, detail="Snapshot not found in current table metadata")

    try:
        from cryo.sources.factory import source_catalog_for

        catalog = source_catalog_for(_source_view(source))
        iceberg_table = catalog.load_table(table.schema_name, table.table_name)
        previous_snapshot_id = iceberg_table.metadata.current_snapshot_id
        iceberg_table.manage_snapshots().rollback_to_snapshot(parsed).commit()
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Rollback commit failed: {type(exc).__name__}: {exc}"[:500]
        ) from exc

    session.add(
        AuditLog(
            table_id=table_id,
            action="snapshot.rollback",
            status="OK",
            detail=f"snapshot_id={parsed} previous_snapshot_id={previous_snapshot_id}",
            actor=principal.subject,
        )
    )
    session.commit()
    return RollbackSnapshotResponse(
        table_id=table_id,
        snapshot_id=str(parsed),
        previous_snapshot_id=(
            str(previous_snapshot_id) if previous_snapshot_id is not None else None
        ),
    )


@router.get(
    "/security/abac/policies",
    response_model=ABACPolicyListResponse,
    dependencies=[Depends(require_viewer)],
)
def list_abac_policies_route(
    session: SessionDep,
    table_id: uuid.UUID | None = None,
) -> ABACPolicyListResponse:
    """List configured ABAC policies (table + role -> allowed operations).

    Enforcement is fail-open: a table/role pair with no matching policy row is
    unrestricted. Only maintenance-execute paths are enforced today; scan-time
    row/column filtering requires a catalog proxy and is not wired up.
    """
    from cryo.security.abac_enforcement import list_abac_policies

    rows = list_abac_policies(session, table_id=table_id)
    return ABACPolicyListResponse(
        items=[
            ABACPolicyRead(
                id=row.id,
                table_id=row.table_id,
                role=row.role,
                has_row_filter=bool(row.row_filter),
                masked_columns=sorted((row.column_masks or {}).keys()),
                allowed_operations=list(row.allowed_operations or []),
            )
            for row in rows
        ],
        total=len(rows),
        enforcement_note=(
            "Enforced on maintenance-execute paths only (fail-open: a table/role with no "
            "policy row is unrestricted). Scan-time row/column filtering is not wired up."
        ),
    )


@router.get(
    "/sources/{source_id}/catalog-latency",
    response_model=CatalogLatencyResponse,
    dependencies=[Depends(require_viewer)],
)
def get_catalog_latency(source_id: uuid.UUID, session: SessionDep) -> CatalogLatencyResponse:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    monitor = get_catalog_latency_monitor(str(source_id))
    return CatalogLatencyResponse(
        source_id=source_id,
        source_name=source.name,
        operations=monitor.all_summaries(),
    )
