"""Shared helpers for /api/v1 extension routers.

These were extracted from the formerly-monolithic ``cryo.api.extensions`` module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from cryo.ai.explain import explain_health_change
from cryo.api.preview_cost import savings_projection_from_stats, savings_projection_to_dict
from cryo.api.serializers import table_to_read
from cryo.config import get_settings
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    Table,
    TableStats,
    TableStatsHistory,
)
from cryo.domain.enums import Mode
from cryo.infra.wiring import build_runtime_wiring
from cryo.mcp.server import CryoMcpServer, build_default_mcp_server


def provider_api_key(stored: str | None) -> str | None:
    from cryo.security.crypto import decrypt_secret

    return decrypt_secret(stored)


def slack_webhook_url(stored: str | None) -> str | None:
    from cryo.security.crypto import decrypt_secret

    return decrypt_secret(stored)


def stats_snapshot(
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


def table_stats_dict(table: Table, stats: TableStats | None) -> dict[str, Any]:
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


def gather_copilot_context(session: Session) -> dict[str, Any]:
    """Gather datalake context for LLM prompts."""
    tables_count = session.execute(select(Table.id).where(Table.is_active.is_(True))).fetchall()
    unhealthy = session.execute(
        select(Table.schema_name, Table.table_name, TableStats.grade, TableStats.overall_score)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(TableStats.overall_score < 70)
        .order_by(TableStats.overall_score.asc())
        .limit(5)
    ).fetchall()
    compaction = session.execute(
        select(Table.schema_name, Table.table_name, TableStats.small_files_count)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(TableStats.needs_compaction.is_(True))
        .order_by(TableStats.small_files_count.desc())
        .limit(5)
    ).fetchall()
    return {
        "total_tables": len(tables_count),
        "unhealthy_tables": [
            {"table": f"{r[0]}.{r[1]}", "grade": r[2], "score": r[3]} for r in unhealthy
        ],
        "compaction_backlog": [
            {"table": f"{r[0]}.{r[1]}", "small_files": r[2]} for r in compaction
        ],
    }


def build_mcp_server(session: Session) -> CryoMcpServer:
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
        before = stats_snapshot(session, table_id, None, offset=-2)
        after = stats_snapshot(session, table_id, None, offset=-1)
        if before is None or after is None:
            raise KeyError("insufficient history")
        return {
            "explanation": explain_health_change(
                before, after, preset_name=args.get("preset_name")
            )
        }

    def simulate_preset(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        table = session.get(Table, table_id)
        stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
        if table is None or stats is None:
            raise KeyError("table not found")
        stats_dict = table_stats_dict(table, stats)
        projection = savings_projection_from_stats(stats_dict, settings)
        return savings_projection_to_dict(projection)

    def get_table_schema(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
        if stats is None:
            raise KeyError("table stats not found")
        raw = dict(stats.raw or {})
        return {
            "table_id": str(table_id),
            "partition_fields": raw.get("partition_fields") or [],
            "column_quality": raw.get("column_quality") or [],
            "format_version": raw.get("format_version"),
        }

    def get_snapshot_history(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        limit = int(args.get("limit") or 30)
        rows = session.scalars(
            select(TableStatsHistory)
            .where(TableStatsHistory.table_id == table_id)
            .order_by(TableStatsHistory.scan_at.desc())
            .limit(limit)
        ).all()
        history = []
        for row in reversed(rows):
            raw = dict(row.raw or {})
            history.append(
                {
                    "scan_at": row.scan_at.isoformat(),
                    "total_snapshots": raw.get("total_snapshots"),
                    "grade": row.grade,
                    "overall_score": row.overall_score,
                }
            )
        return {"table_id": str(table_id), "history": history}

    def run_preset(args: dict[str, Any]) -> dict[str, Any]:
        if not args.get("confirm"):
            raise ValueError("run_preset requires confirm=true")
        table_id = uuid.UUID(str(args["table_id"]))
        destination_id = uuid.UUID(str(args["destination_id"]))
        preset_id = uuid.UUID(str(args["preset_id"])) if args.get("preset_id") else None

        if session.get(Destination, destination_id) is None:
            raise KeyError("destination not found")
        if preset_id and session.get(Preset, preset_id) is None:
            raise KeyError("preset not found")
        table = session.get(Table, table_id)
        if table is None:
            raise KeyError("table not found")

        mode = Mode(str(args.get("mode") or "standard"))
        run = MaintenanceRun(
            kind="adhoc",
            preset_id=preset_id,
            trigger="mcp",
            status="queued",
        )
        session.add(run)
        session.flush()
        job = MaintenanceJob(
            run_id=run.id,
            table_id=table_id,
            destination_id=destination_id,
            mode=mode.value,
            status="queued",
        )
        session.add(job)
        session.commit()
        wiring = build_runtime_wiring()
        wiring.queue.enqueue(str(job.id), str(destination_id))
        return {
            "run_id": str(run.id),
            "job_ids": [str(job.id)],
            "status": run.status,
        }

    return build_default_mcp_server(
        get_table_health=get_table_health,
        list_stale_tables=list_stale_tables,
        list_compaction_backlog=list_compaction_backlog,
        explain_health_change=explain_tool,
        simulate_preset=simulate_preset,
        get_table_schema=get_table_schema,
        get_snapshot_history=get_snapshot_history,
        run_preset=run_preset,
    )