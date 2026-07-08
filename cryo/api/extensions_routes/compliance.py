"""Compliance, snapshot protection, MCP, and catalog-latency routes."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from cryo.api.deps import SessionDep, require_operator, require_viewer
from cryo.api.extensions_helpers import build_mcp_server, table_stats_dict
from cryo.compliance.governance import track_pending_deletions
from cryo.db.models import Source, Table, TableStats
from cryo.db.repositories.deletion_tracking import DeletionTrackingRepository
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


class DeletionTrackingRequest(BaseModel):
    subject_id: str
    soft_deleted_at: str
    pending_rows: int
    blocked_until: str
    reason: str


class DeletionTrackingEntry(BaseModel):
    id: uuid.UUID
    subject_id: str
    soft_deleted_at: str
    pending_rows: int
    blocked_until: str
    reason: str


class DeletionTrackingListResponse(BaseModel):
    table_id: uuid.UUID
    entries: list[DeletionTrackingEntry]


class PendingDeletionSummaryRead(BaseModel):
    table_id: uuid.UUID
    pending_rows: int
    earliest_soft_deleted_at: str
    blocked_until: str
    reason: str


class FleetDeletionSummaryResponse(BaseModel):
    total_pending_rows: int
    affected_tables: int
    message: str
    cutoff: str | None = None
    summaries: list[PendingDeletionSummaryRead] = []


class DeletionResolveResponse(BaseModel):
    table_id: uuid.UUID
    resolved_count: int
    resolved_at: str


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


@router.post(
    "/tables/{table_id}/deletion-tracking",
    response_model=DeletionTrackingEntry,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
def record_deletion_tracking(
    table_id: uuid.UUID,
    payload: DeletionTrackingRequest,
    session: SessionDep,
) -> DeletionTrackingEntry:
    from datetime import datetime

    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")
    repo = DeletionTrackingRepository(session)
    soft_deleted_at = datetime.fromisoformat(payload.soft_deleted_at.replace("Z", "+00:00"))
    blocked_until = datetime.fromisoformat(payload.blocked_until.replace("Z", "+00:00"))
    row = repo.record(
        table_id=table_id,
        subject_id=payload.subject_id,
        soft_deleted_at=soft_deleted_at,
        pending_rows=payload.pending_rows,
        blocked_until=blocked_until,
        reason=payload.reason,
    )
    return DeletionTrackingEntry(
        id=row.id,
        subject_id=row.subject_id,
        soft_deleted_at=row.soft_deleted_at.isoformat(),
        pending_rows=row.pending_rows,
        blocked_until=row.blocked_until.isoformat(),
        reason=row.reason,
    )


@router.get(
    "/tables/{table_id}/deletion-tracking",
    response_model=DeletionTrackingListResponse,
    dependencies=[Depends(require_viewer)],
)
def list_deletion_tracking(
    table_id: uuid.UUID,
    session: SessionDep,
) -> DeletionTrackingListResponse:
    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")
    repo = DeletionTrackingRepository(session)
    rows = repo.pending_for_table(table_id)
    return DeletionTrackingListResponse(
        table_id=table_id,
        entries=[
            DeletionTrackingEntry(
                id=row.id,
                subject_id=row.subject_id,
                soft_deleted_at=row.soft_deleted_at.isoformat(),
                pending_rows=row.pending_rows,
                blocked_until=row.blocked_until.isoformat(),
                reason=row.reason,
            )
            for row in rows
        ],
    )


@router.post(
    "/tables/{table_id}/deletion-tracking/resolve",
    response_model=DeletionResolveResponse,
    dependencies=[Depends(require_operator)],
)
def resolve_deletion_tracking(
    table_id: uuid.UUID,
    session: SessionDep,
) -> DeletionResolveResponse:
    from datetime import datetime

    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")
    repo = DeletionTrackingRepository(session)
    resolved = repo.mark_resolved(table_id, up_to=datetime.now(UTC))
    return DeletionResolveResponse(
        table_id=table_id,
        resolved_count=resolved,
        resolved_at=datetime.now(UTC).isoformat(),
    )


@router.get(
    "/deletion-tracking/fleet",
    response_model=FleetDeletionSummaryResponse,
    dependencies=[Depends(require_viewer)],
)
def fleet_deletion_summary(session: SessionDep) -> FleetDeletionSummaryResponse:
    """Fleet-wide pending-deletion summary.

    Returns the human-friendly
    \"N pending hard deletions, blocked until expiry after <date>\"
    message that compliance teams need: how many rows across how many
    tables are awaiting the next successful expire_snapshots pass, and
    the earliest cutoff at which they will be physically cleared.
    """
    repo = DeletionTrackingRepository(session)
    summaries = repo.fleet_summary()
    total_rows = repo.total_pending_rows()
    affected = len({s.table_id for s in summaries})
    cutoff_iso: str | None = None
    if summaries:
        cutoff_iso = summaries[0].blocked_until.isoformat()
    if total_rows <= 0:
        message = "No pending hard deletions."
    else:
        message = (
            f"{total_rows} pending hard deletions across {affected} table"
            f"{'s' if affected != 1 else ''}, "
            f"blocked until expiry after {cutoff_iso or 'unknown'}."
        )
    return FleetDeletionSummaryResponse(
        total_pending_rows=total_rows,
        affected_tables=affected,
        message=message,
        cutoff=cutoff_iso,
        summaries=[
            PendingDeletionSummaryRead(
                table_id=row.table_id,
                pending_rows=row.pending_rows,
                earliest_soft_deleted_at=row.earliest_soft_deleted_at.isoformat(),
                blocked_until=row.blocked_until.isoformat(),
                reason=row.reason,
            )
            for row in summaries
        ],
    )