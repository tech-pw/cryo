"""Shared helpers for /api/v1 extension routers.

These were extracted from the formerly-monolithic ``cryo.api.extensions`` module.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from cryo.ai.explain import explain_health_change
from cryo.ai.filter_translate import (
    build_filter_chips,
    deterministic_translate,
    validate_filter_spec,
)
from cryo.api.preview_cost import savings_projection_from_stats, savings_projection_to_dict
from cryo.api.serializers import table_to_read
from cryo.config import get_settings
from cryo.db.models import (
    AuditLog,
    Table,
    TableColumn,
    TableStats,
    TableStatsHistory,
)
from cryo.mcp.server import CryoMcpServer, build_default_mcp_server
from cryo.reporting.impact_signals import _RECLAIM_ACTIONS


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
    total_tables = session.scalar(
        select(func.count()).select_from(Table).where(Table.is_active.is_(True))
    )
    unhealthy = session.execute(
        select(Table.schema_name, Table.table_name, TableStats.grade, TableStats.overall_score)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(Table.is_active.is_(True), TableStats.overall_score < 70)
        .order_by(TableStats.overall_score.asc())
        .limit(5)
    ).fetchall()
    compaction = session.execute(
        select(Table.schema_name, Table.table_name, TableStats.small_files_count)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(Table.is_active.is_(True), TableStats.needs_compaction.is_(True))
        .order_by(TableStats.small_files_count.desc())
        .limit(5)
    ).fetchall()
    return {
        "total_tables": int(total_tables or 0),
        "coverage_note": (
            "unhealthy_tables and compaction_backlog are samples, not the fleet. "
            "total_tables is COUNT(*) of active tables."
        ),
        "samples_are_not_the_fleet": True,
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
        limit = int(args.get("limit") or 20)
        offset = int(args.get("cursor") or 0)
        last_ok = (
            select(AuditLog.table_id, func.max(AuditLog.ts).label("last_ok"))
            .where(AuditLog.status == "OK", AuditLog.action.in_(_RECLAIM_ACTIONS))
            .group_by(AuditLog.table_id)
            .subquery()
        )
        stale = or_(last_ok.c.last_ok.is_(None), last_ok.c.last_ok < cutoff)
        active = Table.is_active.is_(True)
        total = int(
            session.scalar(
                select(func.count())
                .select_from(Table)
                .outerjoin(last_ok, last_ok.c.table_id == Table.id)
                .where(active, stale)
            )
            or 0
        )
        fleet_active = int(
            session.scalar(select(func.count()).select_from(Table).where(active)) or 0
        )
        rows = session.execute(
            select(Table.id, Table.schema_name, Table.table_name, last_ok.c.last_ok)
            .outerjoin(last_ok, last_ok.c.table_id == Table.id)
            .where(active, stale)
            .order_by(Table.schema_name, Table.table_name, Table.id)
            .offset(offset)
            .limit(limit)
        ).all()
        next_offset = offset + len(rows)
        return {
            "tables": [
                {
                    "table_id": str(row.id),
                    "schema_name": row.schema_name,
                    "table_name": row.table_name,
                    "last_successful_reclaim_at": row.last_ok.isoformat() if row.last_ok else None,
                }
                for row in rows
            ],
            "total": total,
            "cursor": str(next_offset) if next_offset < total else None,
            "truncated": next_offset < total,
            "coverage": {
                "matched": total,
                "returned": len(rows),
                "fleet_active": fleet_active,
                "note": "total is a count of stale tables, not a capped sample of the fleet",
            },
        }

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
            "explanation": explain_health_change(before, after, preset_name=args.get("preset_name"))
        }

    def simulate_preset(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        table = session.get(Table, table_id)
        stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
        if table is None or stats is None:
            raise KeyError("table not found")
        stats_dict = table_stats_dict(table, stats)
        projection = savings_projection_from_stats(stats_dict, settings)
        payload = savings_projection_to_dict(projection)
        if not args.get("preset_id"):
            payload["cost_model_precision"] = payload.get("precision")
            payload["precision"] = "estimate"
            payload["kind"] = "estimate"
            payload["executable_preview"] = False
            payload["is_planner_preview"] = False
            payload["warning"] = (
                "preset_id is missing. This is a cost estimate, not an executable planner preview."
            )
        return payload

    def filter_translation(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("filter_translation requires query")
        translated = deterministic_translate(query)
        validated, errors = validate_filter_spec(translated.filter)
        return {
            "query": query,
            "filter": validated,
            "chips": build_filter_chips(validated),
            "warnings": translated.warnings,
            "errors": translated.errors + errors,
            "used_llm": False,
        }

    def get_table_schema(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
        if stats is None and session.get(Table, table_id) is None:
            raise KeyError("table not found")
        raw = dict(stats.raw or {}) if stats is not None else {}
        columns = session.scalars(
            select(TableColumn)
            .where(TableColumn.table_id == table_id)
            .order_by(TableColumn.column_name)
        ).all()
        return {
            "table_id": str(table_id),
            "source": "scan_summary",
            "complete_catalog_schema": False,
            "warning": "Scan summary, not a complete catalog schema.",
            "partition_fields": raw.get("partition_fields") or [],
            "column_quality": raw.get("column_quality") or [],
            "format_version": raw.get("format_version"),
            "columns": [
                {
                    "column_name": column.column_name,
                    "data_type": column.data_type,
                    "description": column.description,
                    "source": "table_columns",
                }
                for column in columns
            ],
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
                    "total_snapshots_count": raw.get("total_snapshots"),
                    "grade": row.grade,
                    "overall_score": row.overall_score,
                }
            )
        return {
            "table_id": str(table_id),
            "kind": "scan_count_history",
            "scan_count_history": history,
            "unsupported": ["snapshot_id", "parent_id", "refs"],
            "warning": (
                "This is scan-count history from stored table stats, not Iceberg snapshot history. "
                "Snapshot ids were not loaded and are not invented from counts."
            ),
        }

    def run_preset(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return {
            "error": "not_authorized_here",
            "detail": "Use Ask Cryo action review. confirm=true is not authorization.",
        }

    run_preset_handler = run_preset

    return build_default_mcp_server(
        get_table_health=get_table_health,
        list_stale_tables=list_stale_tables,
        list_compaction_backlog=list_compaction_backlog,
        explain_health_change=explain_tool,
        simulate_preset=simulate_preset,
        filter_translation=filter_translation,
        get_table_schema=get_table_schema,
        get_snapshot_history=get_snapshot_history,
        run_preset=run_preset_handler,
    )
