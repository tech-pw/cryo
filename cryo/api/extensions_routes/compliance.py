"""Compliance, snapshot protection, MCP, and catalog-latency routes."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from cryo.api.deps import SessionDep, require_operator, require_viewer
from cryo.api.extensions_helpers import build_mcp_server, table_stats_dict
from cryo.compliance.governance import track_pending_deletions
from cryo.db.models import ProtectedSnapshot, Source, Table, TableStats
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
    dependencies=[Depends(require_operator)],
    tags=["ai"],
)
def mcp_call_tool(payload: McpToolCallRequest, session: SessionDep) -> McpToolCallResponse:
    server = build_mcp_server(session)
    try:
        result = server.call(payload.name, payload.arguments)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    row = ProtectedSnapshot(
        table_id=table_id,
        snapshot_id=payload.snapshot_id,
        label=payload.label,
    )
    session.add(row)
    session.commit()
    return ProtectSnapshotResponse(
        table_id=table_id,
        snapshot_id=payload.snapshot_id,
        label=payload.label,
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