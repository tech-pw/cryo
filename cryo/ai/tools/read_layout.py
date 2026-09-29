"""File layout and partition candidates from stored scan rows. No $files scans."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import func, select

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.read_identity import (
    TableRefInput,
    candidate,
    clamp_limit,
    iso,
    latest_stats,
    make_envelope,
    missing_fields,
    parse_offset,
    require_table,
    scalar_count,
    table_link,
)
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.security import Role
from cryo.db.models import TablePartition


class PartitionInput(TableRefInput):
    limit: int = 50
    cursor: str | None = None


def get_file_layout(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    table, error = require_table(ctx, parsed, "get_file_layout")
    if error is not None or table is None:
        return error or make_envelope(
            "get_file_layout",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    stats = latest_stats(ctx.session, table.id)
    if stats is None:
        return make_envelope(
            "get_file_layout",
            {
                "summary": "No stored scan. File counts are unknown, not zero.",
                **candidate(table),
                "total_files": None,
                "small_files_count": None,
                "avg_file_size_mb": None,
                "target_file_size_bytes": None,
                "total_size_gb": None,
                "unknown": [
                    "total_files",
                    "small_files_count",
                    "avg_file_size_mb",
                    "target_file_size_bytes",
                    "total_size_gb",
                    "scan_at",
                ],
            },
            ctx=ctx,
            classification="unknown",
            source_links=[table_link(table)],
            source_record_ids=[{"table_id": str(table.id)}],
            warnings=["No TableStats row. Layout was not read from $files."],
            unsupported_fields=["live_files"],
        )
    fields = {
        "total_files": stats.total_files,
        "small_files_count": stats.small_files_count,
        "avg_file_size_mb": stats.avg_file_size_mb,
        "target_file_size_bytes": stats.target_file_size_bytes,
        "total_size_gb": stats.total_size_gb,
        "total_partitions": stats.total_partitions,
        "scan_at": stats.scan_at,
    }
    return make_envelope(
        "get_file_layout",
        {
            "summary": "File layout from the latest stored scan. Null sizes stay null.",
            **candidate(table),
            "total_files": stats.total_files,
            "small_files_count": stats.small_files_count,
            "avg_file_size_mb": stats.avg_file_size_mb,
            "target_file_size_bytes": stats.target_file_size_bytes,
            "total_size_gb": stats.total_size_gb,
            "total_partitions": stats.total_partitions,
            "scan_at": iso(stats.scan_at),
            "as_of": "scan",
            "unknown": missing_fields(fields),
        },
        ctx=ctx,
        observed_at=stats.scan_at,
        units={
            "avg_file_size_mb": "MB",
            "target_file_size_bytes": "bytes",
            "total_size_gb": "GB",
            "total_files": "files",
        },
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        coverage={"total": 1, "returned": 1, "as_of": "scan"},
        row_count=1,
        unsupported_fields=["live_files"],
        warnings=["Layout is from the scanner snapshot, not a live $files listing."],
    )


def list_partition_candidates(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = PartitionInput.model_validate(parsed.model_dump())
    table, error = require_table(ctx, spec, "list_partition_candidates")
    if error is not None or table is None:
        return error or make_envelope(
            "list_partition_candidates",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    offset = parse_offset(spec.cursor)
    if offset is None:
        return make_envelope(
            "list_partition_candidates",
            {"error": "invalid_cursor"},
            ctx=ctx,
            classification="unknown",
            error_category="invalid_arguments",
            warnings=["cursor must be a non-negative integer offset."],
        )
    limit = clamp_limit(spec.limit)
    known = TablePartition.small_files_in_partition_count.is_not(None)
    base = TablePartition.table_id == table.id
    total = scalar_count(
        ctx.session,
        select(func.count()).select_from(TablePartition).where(base, known),
    )
    unknown_small = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(TablePartition)
        .where(base, TablePartition.small_files_in_partition_count.is_(None)),
    )
    rows = list(
        ctx.session.scalars(
            select(TablePartition)
            .where(base, known)
            .order_by(TablePartition.small_files_in_partition_count.desc(), TablePartition.id)
            .offset(offset)
            .limit(limit)
        )
    )
    rendered = [
        {
            "partition_value": row.partition_value,
            "partition_key": row.partition_key,
            "transform": row.transform,
            "small_files_in_partition_count": row.small_files_in_partition_count,
            "files_per_partition": row.files_per_partition,
            "partition_size_gb": row.partition_size_gb,
            "scan_at": iso(row.scan_at),
            "unknown": [
                key
                for key, value in {
                    "transform": row.transform,
                    "partition_size_gb": row.partition_size_gb,
                    "files_per_partition": row.files_per_partition,
                }.items()
                if value is None
            ],
        }
        for row in rows
    ]
    truncated = offset + len(rendered) < total
    return make_envelope(
        "list_partition_candidates",
        {
            "summary": "Partitions ranked by stored small-file counts. Null counts are not zero.",
            **candidate(table),
            "rows": rendered,
            "total": total,
            "unknown": {"null_small_file_count": unknown_small},
            "cursor": str(offset + len(rendered)) if truncated else None,
            "as_of": "scan",
        },
        ctx=ctx,
        units={"small_files_in_partition_count": "files", "partition_size_gb": "GB"},
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        coverage={
            "total": total,
            "returned": len(rendered),
            "unknown_null_small_file_count": unknown_small,
            "as_of": "scan",
        },
        truncated=truncated,
        row_count=len(rendered),
        warnings=["Partition candidates come from stored scan rows, not a live $files scan."],
    )


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_file_layout",
        family="layout",
        description="Stored file counts and sizes. Nulls stay null. Does not scan $files.",
        input_model=TableRefInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_file_layout,
    ),
    ToolSpec(
        name="list_partition_candidates",
        family="layout",
        description="Stored partitions ranked by small-file count. Null counts stay unknown.",
        input_model=PartitionInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=list_partition_candidates,
    ),
)
