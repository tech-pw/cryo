from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from cryo.ai.anomaly import detect_anomalies
from cryo.ai.copilot import answer_verified_question, list_verified_questions
from cryo.ai.explain import explain_health_change
from cryo.api.deps import require_operator, require_viewer
from cryo.api.preview_cost import savings_projection_from_stats, savings_projection_to_dict
from cryo.api.schemas import JsonDict
from cryo.api.serializers import table_to_read
from cryo.compliance.governance import track_pending_deletions
from cryo.config import get_settings
from cryo.db.models import AuditLog, ProtectedSnapshot, Source, Table, TableStats, TableStatsHistory
from cryo.db.session import get_session
from cryo.mcp.server import CryoMcpServer, build_default_mcp_server
from cryo.sources.latency import get_catalog_latency_monitor

SessionDep = Annotated[Session, Depends(get_session)]

router = APIRouter(prefix="/api/v1")


class AnomalyResponse(BaseModel):
    table_id: uuid.UUID
    anomalies: list[JsonDict]


class ExplainHealthRequest(BaseModel):
    before_scan_at: datetime | None = None
    after_scan_at: datetime | None = None
    preset_name: str | None = None


class ExplainHealthResponse(BaseModel):
    table_id: uuid.UUID
    explanation: str


class CopilotQuestionRequest(BaseModel):
    question_id: str
    context: JsonDict = Field(default_factory=dict)


class CopilotQuestionResponse(BaseModel):
    question_id: str
    result: JsonDict


class McpToolCallRequest(BaseModel):
    name: str
    arguments: JsonDict = Field(default_factory=dict)


class McpToolCallResponse(BaseModel):
    result: JsonDict


class ComplianceStatusResponse(BaseModel):
    table_id: uuid.UUID
    table_ref: str
    pending_deletion: JsonDict | None = None


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
    operations: JsonDict


@router.get(
    "/tables/{table_id}/anomalies",
    response_model=AnomalyResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def get_table_anomalies(table_id: uuid.UUID, session: SessionDep) -> AnomalyResponse:
    history = session.scalars(
        select(TableStatsHistory)
        .where(TableStatsHistory.table_id == table_id)
        .order_by(TableStatsHistory.scan_at)
        .limit(30)
    )
    rows = []
    for row in history:
        raw = dict(row.raw or {})
        rows.append(
            {
                "scan_at": row.scan_at.isoformat(),
                "small_files_count": row.small_files_count,
                "total_files": row.total_files,
                "total_size_gb": row.total_size_gb,
                "overall_score": row.overall_score,
                "total_snapshots": raw.get("total_snapshots"),
                "total_delete_files": raw.get("total_delete_files"),
                "manifest_count": raw.get("manifest_count"),
                "projected_savings_usd_month": raw.get("projected_savings_usd_month"),
            }
        )
    return AnomalyResponse(
        table_id=table_id,
        anomalies=[
            {
                "metric": item.metric,
                "value": item.value,
                "baseline": item.baseline,
                "z_score": item.z_score,
                "severity": item.severity,
            }
            for item in detect_anomalies(
                rows,
                metrics=(
                    "small_files_count",
                    "total_files",
                    "total_size_gb",
                    "total_snapshots",
                    "total_delete_files",
                    "manifest_count",
                ),
            )
        ],
    )


@router.post(
    "/tables/{table_id}/explain-health",
    response_model=ExplainHealthResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def explain_table_health(
    table_id: uuid.UUID,
    payload: ExplainHealthRequest,
    session: SessionDep,
) -> ExplainHealthResponse:
    before = _stats_snapshot(session, table_id, payload.before_scan_at, offset=-2)
    after = _stats_snapshot(session, table_id, payload.after_scan_at, offset=-1)
    if before is None or after is None:
        raise HTTPException(status_code=404, detail="Insufficient stats history for comparison")
    explanation = explain_health_change(before, after, preset_name=payload.preset_name)
    return ExplainHealthResponse(table_id=table_id, explanation=explanation)


@router.get(
    "/ai/copilot/questions",
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def copilot_questions() -> list[dict[str, str]]:
    return list_verified_questions()


@router.post(
    "/ai/copilot/ask",
    response_model=CopilotQuestionResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def copilot_ask(payload: CopilotQuestionRequest, session: SessionDep) -> CopilotQuestionResponse:
    try:
        result = answer_verified_question(payload.question_id, payload.context, session)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CopilotQuestionResponse(question_id=payload.question_id, result=result)


@router.get(
    "/mcp/tools",
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def mcp_list_tools(session: SessionDep) -> list[dict[str, str]]:
    return _mcp_server(session).list_tools()


@router.post(
    "/mcp/tools/call",
    response_model=McpToolCallResponse,
    dependencies=[Depends(require_operator)],
    tags=["ai"],
)
def mcp_call_tool(payload: McpToolCallRequest, session: SessionDep) -> McpToolCallResponse:
    server = _mcp_server(session)
    try:
        result = server.call(payload.name, payload.arguments)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return McpToolCallResponse(result=result)


@router.get(
    "/tables/{table_id}/compliance",
    response_model=ComplianceStatusResponse,
    dependencies=[Depends(require_viewer)],
    tags=["compliance"],
)
def get_compliance_status(table_id: uuid.UUID, session: SessionDep) -> ComplianceStatusResponse:
    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
    stats_dict = _table_stats_dict(table, stats)
    pending = track_pending_deletions(
        f"iceberg.{table.schema_name}.{table.table_name}",
        stats_dict,
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
    )


@router.post(
    "/tables/{table_id}/protected-snapshots",
    response_model=ProtectSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_operator)],
    tags=["compliance"],
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
    tags=["compliance"],
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


def _mcp_server(session: Session) -> CryoMcpServer:
    settings = get_settings()

    def get_table_health(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        table = session.scalar(
            select(Table)
            .where(Table.id == table_id)
            .options(selectinload(Table.stats), selectinload(Table.cost))
        )
        if table is None:
            raise KeyError("table not found")
        return table_to_read(table).model_dump(mode="json")

    def list_stale_tables(args: dict[str, Any]) -> dict[str, Any]:
        cooldown_hours = int(args.get("cooldown_hours") or 168)
        cutoff = datetime.now(UTC) - timedelta(hours=cooldown_hours)
        tables = []
        for table in session.scalars(select(Table).where(Table.is_active.is_(True)).limit(50)):
            last = session.scalar(
                select(AuditLog.ts)
                .where(AuditLog.table_id == table.id)
                .order_by(desc(AuditLog.ts))
                .limit(1)
            )
            if last is None or last < cutoff:
                tables.append(
                    {
                        "table_id": str(table.id),
                        "schema_name": table.schema_name,
                        "table_name": table.table_name,
                        "last_maintained_at": last.isoformat() if last else None,
                    }
                )
        limit = int(args.get("limit") or 20)
        return {"tables": tables[:limit]}

    def list_compaction_backlog(args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit") or 20)
        rows = session.execute(
            select(
                Table.schema_name, Table.table_name, TableStats.small_files_count, TableStats.grade
            )
            .join(TableStats, TableStats.table_id == Table.id)
            .where(TableStats.needs_compaction.is_(True))
            .order_by(TableStats.small_files_count.desc())
            .limit(limit)
        )
        return {
            "tables": [
                {
                    "schema_name": schema,
                    "table_name": name,
                    "small_files_count": small_files,
                    "grade": grade,
                }
                for schema, name, small_files, grade in rows
            ]
        }

    def explain_tool(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        before = _stats_snapshot(session, table_id, None, offset=-2)
        after = _stats_snapshot(session, table_id, None, offset=-1)
        if before is None or after is None:
            raise KeyError("insufficient history")
        return {
            "explanation": explain_health_change(
                before,
                after,
                preset_name=args.get("preset_name"),
            )
        }

    def simulate_preset(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        table = session.get(Table, table_id)
        stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
        if table is None or stats is None:
            raise KeyError("table not found")
        stats_dict = _table_stats_dict(table, stats)
        projection = savings_projection_from_stats(stats_dict, settings)
        return savings_projection_to_dict(projection)

    return build_default_mcp_server(
        get_table_health=get_table_health,
        list_stale_tables=list_stale_tables,
        list_compaction_backlog=list_compaction_backlog,
        explain_health_change=explain_tool,
        simulate_preset=simulate_preset,
    )


def _stats_snapshot(
    session: Session,
    table_id: uuid.UUID,
    scan_at: datetime | None,
    *,
    offset: int,
) -> dict[str, Any] | None:
    query = select(TableStatsHistory).where(TableStatsHistory.table_id == table_id)
    if scan_at is not None:
        query = query.where(TableStatsHistory.scan_at == scan_at)
    rows = list(session.scalars(query.order_by(TableStatsHistory.scan_at)))
    if not rows:
        return None
    row = rows[offset] if scan_at is None else rows[0]
    raw = dict(row.raw or {})
    return {
        "grade": row.grade,
        "overall_score": row.overall_score,
        "small_files_count": row.small_files_count,
        "delete_file_pct": raw.get("delete_file_pct"),
        "manifest_count": raw.get("manifest_count"),
        "metadata_to_data_ratio": raw.get("metadata_to_data_ratio"),
        "total_snapshots": raw.get("total_snapshots"),
    }


def _table_stats_dict(table: Table, stats: TableStats | None) -> dict[str, Any]:
    if stats is None:
        return {"schema_name": table.schema_name, "table_name": table.table_name}
    raw = dict(stats.raw or {})
    return {
        "schema_name": table.schema_name,
        "table_name": table.table_name,
        **raw,
        "pending_soft_delete_rows": raw.get("pending_soft_delete_rows"),
        "delete_file_pct": stats.delete_file_pct,
        "small_files_count": stats.small_files_count,
        "target_file_size_bytes": stats.target_file_size_bytes,
        "projected_savings_usd_month": stats.projected_savings_usd_month,
    }
