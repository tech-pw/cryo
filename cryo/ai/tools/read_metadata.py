"""Table metadata, commit-time buckets, and scan schema index."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.read_identity import (
    TableRefInput,
    candidate,
    clamp_limit,
    iso,
    latest_stats,
    make_envelope,
    missing_fields,
    require_table,
    scalar_count,
    sql_dt,
    table_link,
)
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.security import Role
from cryo.db.models import Table, TableColumn, TablePartition, TableStats


class CommitTimeInput(BaseModel):
    older_than_days: int = 7
    limit: int = 50
    include_inactive: bool = False


def _scan_failed() -> Any:
    return or_(Table.consecutive_scan_failures > 0, Table.last_scan_error.is_not(None))


def _base(ctx: ToolContext, *, include_inactive: bool) -> list[Any]:
    filters: list[Any] = []
    if not include_inactive:
        filters.append(Table.is_active.is_(True))
    if ctx.principal.tenant_id is not None:
        filters.append(Table.tenant_id == ctx.principal.tenant_id)
    return filters


def _count_bucket(session: Session, filters: list[Any], extra: Any) -> int:
    return scalar_count(
        session,
        select(func.count())
        .select_from(Table)
        .outerjoin(TableStats, TableStats.table_id == Table.id)
        .where(*filters, extra),
    )


def _fetch_bucket(session: Session, filters: list[Any], extra: Any, limit: int) -> list[Any]:
    return list(
        session.execute(
            select(Table, TableStats)
            .outerjoin(TableStats, TableStats.table_id == Table.id)
            .where(*filters, extra)
            .order_by(Table.schema_name, Table.table_name, Table.id)
            .limit(limit)
        )
    )


def _bucket_row(table: Table, stats: TableStats | None) -> dict[str, Any]:
    commit = stats.last_snapshot_committed_at if stats is not None else None
    scan_at = stats.scan_at if stats is not None else None
    return {
        "table_id": str(table.id),
        "schema_name": table.schema_name,
        "table_name": table.table_name,
        "scan_at": iso(scan_at),
        "last_snapshot_committed_at": iso(commit),
        "consecutive_scan_failures": table.consecutive_scan_failures,
        "last_scan_error": table.last_scan_error,
        "as_of": "scan",
    }


def list_tables_by_commit_time(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = CommitTimeInput.model_validate(parsed.model_dump())
    limit = clamp_limit(spec.limit)
    cutoff = ctx.now - timedelta(days=spec.older_than_days)
    cutoff_sql = sql_dt(ctx.session, cutoff)
    filters = _base(ctx, include_inactive=spec.include_inactive)
    failed = _scan_failed()
    never = and_(TableStats.table_id.is_(None), ~failed)
    null_commit = and_(
        TableStats.table_id.is_not(None),
        TableStats.last_snapshot_committed_at.is_(None),
        ~failed,
    )
    not_updated = and_(
        TableStats.last_snapshot_committed_at.is_not(None),
        TableStats.last_snapshot_committed_at < cutoff_sql,
        ~failed,
    )
    recent = and_(
        TableStats.last_snapshot_committed_at.is_not(None),
        TableStats.last_snapshot_committed_at >= cutoff_sql,
        ~failed,
    )
    buckets = {
        "not_updated": not_updated,
        "never_scanned": never,
        "scan_failed": failed,
        "null_commit_time": null_commit,
        "updated_since_cutoff": recent,
    }
    counts = {name: _count_bucket(ctx.session, filters, expr) for name, expr in buckets.items()}
    pages = {
        name: _fetch_bucket(ctx.session, filters, expr, limit)
        for name, expr in buckets.items()
        if name != "updated_since_cutoff"
    }
    rendered = {
        name: [_bucket_row(table, stats) for table, stats in rows] for name, rows in pages.items()
    }
    truncated = any(counts[name] > len(rendered[name]) for name in rendered)
    returned = sum(len(rows) for rows in rendered.values())
    return make_envelope(
        "list_tables_by_commit_time",
        {
            "summary": (
                "Commit times are as of the last scan. not_updated excludes never-scanned, "
                "scan-failed, and null commit time."
            ),
            "as_of": "scan",
            "cutoff": iso(cutoff),
            "not_updated": {"total": counts["not_updated"], "rows": rendered["not_updated"]},
            "unknown": {
                "never_scanned": {
                    "total": counts["never_scanned"],
                    "rows": rendered["never_scanned"],
                },
                "scan_failed": {"total": counts["scan_failed"], "rows": rendered["scan_failed"]},
                "null_commit_time": {
                    "total": counts["null_commit_time"],
                    "rows": rendered["null_commit_time"],
                },
            },
            "updated_since_cutoff": counts["updated_since_cutoff"],
        },
        ctx=ctx,
        coverage={
            "as_of": "scan",
            "cutoff": iso(cutoff),
            "limit": limit,
            "buckets": {
                name: {"total": counts[name], "returned": len(rendered.get(name, []))}
                for name in counts
            },
            "note": "Bucket totals are count queries. Times are as of scan, not a live catalog.",
        },
        warnings=[
            "Commit times are as of the last successful scan, not a live catalog read.",
            "not_updated excludes never-scanned, scan-failed, and null commit time.",
        ],
        truncated=truncated,
        row_count=returned,
        measurement_method="stored_scan",
    )


def get_table_metadata(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    table, error = require_table(ctx, parsed, "get_table_metadata")
    if error is not None or table is None:
        return error or make_envelope(
            "get_table_metadata",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    stats = latest_stats(ctx.session, table.id)
    partition = ctx.session.scalar(
        select(TablePartition)
        .where(TablePartition.table_id == table.id)
        .order_by(TablePartition.scan_at.desc())
        .limit(1)
    )
    transform = partition.transform if partition is not None else None
    partition_col = stats.partition_col if stats is not None else None
    partition_type = stats.partition_col_type if stats is not None else None
    commit = stats.last_snapshot_committed_at if stats is not None else None
    scan_at = stats.scan_at if stats is not None else None
    fields = {
        "location": table.location,
        "owner_email": table.owner_email,
        "partition_col": partition_col,
        "partition_transform": transform,
        "partition_col_type": partition_type,
        "last_snapshot_committed_at": commit,
        "scan_at": scan_at,
    }
    unknown = missing_fields(fields)
    classification = "unknown" if stats is None else "observed"
    return make_envelope(
        "get_table_metadata",
        {
            "summary": "Identity and partition metadata from the stored scan. Nulls are unknown.",
            **candidate(table),
            "location": table.location,
            "owner_email": table.owner_email,
            "partition_col": partition_col,
            "partition_transform": transform,
            "partition_col_type": partition_type,
            "partition_source": "table_partitions" if transform is not None else "table_stats",
            "last_snapshot_committed_at": iso(commit),
            "scan_at": iso(scan_at),
            "as_of": "scan",
            "unknown": unknown,
            "is_active": table.is_active,
        },
        ctx=ctx,
        classification=classification,
        observed_at=scan_at,
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        warnings=["last_snapshot_committed_at is as of the last scan, not a live commit."],
        coverage={"total": 1, "returned": 1, "as_of": "scan"},
        row_count=1,
    )


def get_schema(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    table, error = require_table(ctx, parsed, "get_schema")
    if error is not None or table is None:
        return error or make_envelope(
            "get_schema",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    column_total = scalar_count(
        ctx.session,
        select(func.count()).select_from(TableColumn).where(TableColumn.table_id == table.id),
    )
    columns = list(
        ctx.session.scalars(
            select(TableColumn)
            .where(TableColumn.table_id == table.id)
            .order_by(TableColumn.column_name)
            .limit(200)
        )
    )
    stats = latest_stats(ctx.session, table.id)
    raw = dict(stats.raw or {}) if stats is not None else {}
    scan_fields = (
        raw.get("partition_fields") if isinstance(raw.get("partition_fields"), list) else None
    )
    column_quality = (
        raw.get("column_quality") if isinstance(raw.get("column_quality"), list) else None
    )
    if columns:
        source = "table_columns"
    elif scan_fields or column_quality:
        source = "scan_raw"
    else:
        source = "unavailable"
    return make_envelope(
        "get_schema",
        {
            "summary": "Scan column index, not a complete catalog schema.",
            "table_id": str(table.id),
            "schema_name": table.schema_name,
            "table_name": table.table_name,
            "complete_catalog_schema": False,
            "source": source,
            "columns": [
                {
                    "column_name": column.column_name,
                    "data_type": column.data_type,
                    "description": column.description,
                    "source": "table_columns",
                }
                for column in columns
            ],
            "partition_fields": scan_fields,
            "column_quality": column_quality,
            "format_version": raw.get("format_version"),
            "column_total": column_total,
        },
        ctx=ctx,
        classification="observed" if source != "unavailable" else "unknown",
        observed_at=stats.scan_at if stats is not None else None,
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        unsupported_fields=["field_id", "nested_type", "current_schema_id"],
        warnings=["This is a scan summary or column index, not a complete Iceberg catalog schema."],
        coverage={"total": column_total, "returned": len(columns), "source": source},
        truncated=column_total > len(columns),
        row_count=len(columns),
    )


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_table_metadata",
        family="metadata",
        description="Location, owner, partition transform, and last commit as of the last scan.",
        input_model=TableRefInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_table_metadata,
    ),
    ToolSpec(
        name="list_tables_by_commit_time",
        family="metadata",
        description=(
            "Tables whose scan-time commit is older than a cutoff. Never-scanned, scan-failed, "
            "and null commit times are separate unknown buckets."
        ),
        input_model=CommitTimeInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=list_tables_by_commit_time,
        max_rows=800,
    ),
    ToolSpec(
        name="get_schema",
        family="metadata",
        description="Scan column index. Not a complete catalog schema.",
        input_model=TableRefInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_schema,
    ),
)
