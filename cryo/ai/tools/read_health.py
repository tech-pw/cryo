"""Fleet and table health from stored scans. Missing scans stay unknown, not zero."""

from __future__ import annotations

import uuid
from typing import Any

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
    require_table,
    scalar_count,
    table_link,
)
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.security import Role
from cryo.db.models import Source, Table, TableStats


class LakeHealthInput(BaseModel):
    limit: int = 10


class CompareHealthInput(BaseModel):
    left_table_id: uuid.UUID | None = None
    right_table_id: uuid.UUID | None = None
    left_schema: str | None = None
    left_table: str | None = None
    right_schema: str | None = None
    right_table: str | None = None


def _scope_filters(ctx: ToolContext) -> list[Any]:
    filters: list[Any] = [Table.is_active.is_(True)]
    if ctx.principal.tenant_id is not None:
        filters.append(Table.tenant_id == ctx.principal.tenant_id)
    return filters


def _health_row(table: Table, stats: TableStats) -> dict[str, Any]:
    fields = {
        "grade": stats.grade,
        "overall_score": stats.overall_score,
        "small_files_count": stats.small_files_count,
        "needs_compaction": stats.needs_compaction,
        "scan_at": iso(stats.scan_at),
    }
    return {
        "schema_name": table.schema_name,
        "table_name": table.table_name,
        "table_id": str(table.id),
        **fields,
        "unknown": missing_fields(
            {
                "grade": stats.grade,
                "overall_score": stats.overall_score,
                "small_files_count": stats.small_files_count,
                "scan_at": stats.scan_at,
            }
        ),
    }


def get_lake_health(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = LakeHealthInput.model_validate(parsed.model_dump())
    limit = clamp_limit(spec.limit, default=10)
    filters = _scope_filters(ctx)
    active_total = scalar_count(
        ctx.session, select(func.count()).select_from(Table).where(*filters)
    )
    scanned = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(Table)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(*filters),
    )
    missing_scan = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(Table)
        .outerjoin(TableStats, TableStats.table_id == Table.id)
        .where(*filters, TableStats.table_id.is_(None)),
    )
    missing_score = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(Table)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(*filters, TableStats.overall_score.is_(None)),
    )
    grade_rows = ctx.session.execute(
        select(TableStats.grade, func.count())
        .join(Table, Table.id == TableStats.table_id)
        .where(*filters, TableStats.grade.is_not(None))
        .group_by(TableStats.grade)
    ).all()
    grade_counts = {str(grade): int(count) for grade, count in grade_rows}
    scored_total = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(Table)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(*filters, TableStats.overall_score.is_not(None)),
    )
    lowest = ctx.session.execute(
        select(Table, TableStats)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(*filters, TableStats.overall_score.is_not(None))
        .order_by(TableStats.overall_score.asc(), Table.schema_name, Table.table_name)
        .limit(limit)
    ).all()
    compaction_total = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(Table)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(*filters, TableStats.needs_compaction.is_(True)),
    )
    compaction = ctx.session.execute(
        select(Table, TableStats)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(*filters, TableStats.needs_compaction.is_(True))
        .order_by(TableStats.small_files_count.desc(), Table.schema_name, Table.table_name)
        .limit(limit)
    ).all()
    scan_failed = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(Table)
        .where(
            *filters,
            (Table.consecutive_scan_failures > 0) | (Table.last_scan_error.is_not(None)),
        ),
    )
    sources_enabled = scalar_count(
        ctx.session,
        select(func.count()).select_from(Source).where(Source.enabled.is_(True)),
    )
    sources_disabled = scalar_count(
        ctx.session,
        select(func.count()).select_from(Source).where(Source.enabled.is_(False)),
    )
    lowest_rows = [_health_row(table, stats) for table, stats in lowest]
    compaction_rows = [
        {
            "schema_name": table.schema_name,
            "table_name": table.table_name,
            "table_id": str(table.id),
            "small_files_count": stats.small_files_count,
            "grade": stats.grade,
            "needs_compaction": True,
        }
        for table, stats in compaction
    ]
    observed = [row.scan_at for _, row in lowest if row.scan_at is not None]
    return make_envelope(
        "get_lake_health",
        {
            "summary": (
                "Fleet health from stored scans. Tables without a scan are unknown, not score zero."
            ),
            "grade_counts": grade_counts,
            "active_tables": active_total,
            "scanned_tables": scanned,
            "unknown": {"missing_scan": missing_scan, "missing_score": missing_score},
            "scan_failures": scan_failed,
            "sources_enabled": sources_enabled,
            "sources_disabled": sources_disabled,
            "lowest_scores": {"total": scored_total, "rows": lowest_rows},
            "needs_compaction": {"total": compaction_total, "rows": compaction_rows},
            "tables": lowest_rows,
            "live_catalog_probed": False,
        },
        ctx=ctx,
        observed_at=max(observed) if observed else None,
        units={"overall_score": "points", "small_files_count": "files"},
        coverage={
            "active_tables": active_total,
            "scanned_tables": scanned,
            "lowest_returned": len(lowest_rows),
            "lowest_total": scored_total,
            "compaction_returned": len(compaction_rows),
            "compaction_total": compaction_total,
            "note": "Lists are samples. Totals are count queries.",
        },
        truncated=scored_total > len(lowest_rows) or compaction_total > len(compaction_rows),
        row_count=len(lowest_rows),
        unsupported_fields=["live_catalog"],
        warnings=["Grade counts omit tables with no stored scan. Missing is not zero."],
    )


def get_table_health(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    table, error = require_table(ctx, parsed, "get_table_health")
    if error is not None or table is None:
        return error or make_envelope(
            "get_table_health",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    stats = latest_stats(ctx.session, table.id)
    identity = candidate(table)
    if stats is None:
        return make_envelope(
            "get_table_health",
            {
                "summary": "No stored scan for this table. Health is unknown, not zero.",
                **identity,
                "grade": None,
                "overall_score": None,
                "scan_at": None,
                "unknown": ["grade", "overall_score", "scan_at", "small_files_count"],
            },
            ctx=ctx,
            classification="unknown",
            source_links=[table_link(table)],
            source_record_ids=[{"table_id": str(table.id)}],
            warnings=["No TableStats row. Null health fields are not zeroes."],
            unsupported_fields=["live_catalog"],
        )
    payload = _health_row(table, stats)
    payload["summary"] = "Table health from the latest stored scan, not a live catalog read."
    payload["unknown"] = missing_fields(
        {
            "grade": stats.grade,
            "overall_score": stats.overall_score,
            "small_files_count": stats.small_files_count,
            "total_files": stats.total_files,
            "total_size_gb": stats.total_size_gb,
            "scan_at": stats.scan_at,
        }
    )
    return make_envelope(
        "get_table_health",
        payload,
        ctx=ctx,
        observed_at=stats.scan_at,
        units={"overall_score": "points", "small_files_count": "files", "total_size_gb": "GB"},
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        coverage={"total": 1, "returned": 1, "as_of": "scan"},
        row_count=1,
    )


def _side(
    ctx: ToolContext, table_id: uuid.UUID | None, schema: str | None, name: str | None
) -> dict[str, Any]:
    ref = TableRefInput(table_id=table_id, schema_name=schema, table_name=name)
    table, error = require_table(ctx, ref, "compare_health")
    if error is not None or table is None:
        return {
            "status": error.error_category if error is not None else "not_found",
            "candidates": (error.result.get("candidates") if error is not None else []),
            "grade": None,
            "overall_score": None,
            "comparable": False,
        }
    stats = latest_stats(ctx.session, table.id)
    if stats is None:
        return {
            **candidate(table),
            "grade": None,
            "overall_score": None,
            "scan_at": None,
            "comparable": False,
            "unknown": ["grade", "overall_score", "scan_at"],
        }
    return {
        **candidate(table),
        "grade": stats.grade,
        "overall_score": stats.overall_score,
        "small_files_count": stats.small_files_count,
        "scan_at": iso(stats.scan_at),
        "comparable": stats.overall_score is not None,
    }


def compare_health(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = CompareHealthInput.model_validate(parsed.model_dump())
    left = _side(ctx, spec.left_table_id, spec.left_schema, spec.left_table)
    right = _side(ctx, spec.right_table_id, spec.right_schema, spec.right_table)
    left_score = left.get("overall_score")
    right_score = right.get("overall_score")
    delta = None
    if isinstance(left_score, int) and isinstance(right_score, int):
        delta = left_score - right_score
    comparable = delta is not None
    classification = "observed" if comparable else "unknown"
    return make_envelope(
        "compare_health",
        {
            "summary": "Score delta is omitted when either side is null. Null is not zero.",
            "left": left,
            "right": right,
            "score_delta": delta,
            "comparable": comparable,
        },
        ctx=ctx,
        classification=classification,
        units={"score_delta": "points", "overall_score": "points"},
        warnings=[]
        if comparable
        else ["One or both scores are missing, so no delta was computed."],
        coverage={"compared": 2, "comparable": comparable},
    )


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_lake_health",
        family="health",
        description="Fleet grade counts from stored scans. Missing scans are unknown.",
        input_model=LakeHealthInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_lake_health,
    ),
    ToolSpec(
        name="get_table_health",
        family="health",
        description="Latest stored health for one table. Null fields stay null.",
        input_model=TableRefInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_table_health,
    ),
    ToolSpec(
        name="compare_health",
        family="health",
        description="Compare two tables' stored scores. A missing score is not zero.",
        input_model=CompareHealthInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=compare_health,
    ),
)
