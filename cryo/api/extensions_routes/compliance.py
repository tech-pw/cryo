"""Compliance, snapshot protection, MCP, and catalog-latency routes."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from cryo.api.deps import SessionDep, require_operator, require_viewer
from cryo.api.extensions_helpers import build_mcp_server, table_stats_dict
from cryo.api.security import Principal, Role
from cryo.compliance.governance import track_pending_deletions
from cryo.db.models import AuditLog, ProtectedSnapshot, Source, Table, TableStats
from cryo.db.repositories.protected_snapshots import ProtectedSnapshotRepository
from cryo.sources.latency import get_catalog_latency_monitor

router = APIRouter(prefix="/api/v1", tags=["compliance"])


class ComplianceStatusResponse(BaseModel):
    table_id: uuid.UUID
    table_ref: str
    pending_deletion: dict[str, Any] | None = None
    abac_policy_count: int = 0
    abac_enforcement_note: str | None = None


class ProtectSnapshotRequest(BaseModel):
    snapshot_id: int
    label: str | None = None


class ProtectSnapshotResponse(BaseModel):
    table_id: uuid.UUID
    snapshot_id: int
    label: str | None = None


class ProtectedSnapshotEntry(BaseModel):
    snapshot_id: int
    label: str | None = None
    protected_at: str


class ProtectedSnapshotListResponse(BaseModel):
    table_id: uuid.UUID
    snapshots: list[ProtectedSnapshotEntry]


class UnprotectSnapshotResponse(BaseModel):
    table_id: uuid.UUID
    snapshot_id: int
    removed: bool


class CatalogLatencyResponse(BaseModel):
    source_id: uuid.UUID
    source_name: str
    operations: dict[str, dict[str, float]]


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
    required_role = Role.OPERATOR if payload.name == "run_preset" else Role.VIEWER
    if not principal.can(required_role):
        raise HTTPException(status_code=403, detail=f"Tool {payload.name} requires {required_role.value} role")

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
            detail=f"arguments={payload.arguments}",
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
) -> ProtectSnapshotResponse:
    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")
    repo = ProtectedSnapshotRepository(session)
    row = repo.protect(
        table_id=table_id,
        snapshot_id=payload.snapshot_id,
        label=payload.label,
    )
    return ProtectSnapshotResponse(
        table_id=table_id,
        snapshot_id=row.snapshot_id,
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
                snapshot_id=row.snapshot_id,
                label=row.label,
                protected_at=row.protected_at.isoformat()
                if row.protected_at is not None
                else "",
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
    snapshot_id: int,
    session: SessionDep,
) -> UnprotectSnapshotResponse:
    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")
    repo = ProtectedSnapshotRepository(session)
    removed = repo.unprotect(table_id=table_id, snapshot_id=snapshot_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Protected snapshot not found")
    return UnprotectSnapshotResponse(
        table_id=table_id,
        snapshot_id=snapshot_id,
        removed=True,
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