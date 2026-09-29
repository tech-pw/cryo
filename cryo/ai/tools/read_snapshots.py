"""Snapshot tools. Count history is not snapshot history, and eligible is not deleted."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.read_identity import (
    TableRefInput,
    candidate,
    iso,
    latest_stats,
    make_envelope,
    relation_exists,
    require_table,
    scalar_count,
    table_link,
)
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.security import Role
from cryo.db.models import ProtectedSnapshot, TableStatsHistory


class CompareSnapshotsInput(TableRefInput):
    left_snapshot_id: str | None = None
    right_snapshot_id: str | None = None
    left_scan_at: str | None = None
    right_scan_at: str | None = None


class ExpiryInput(TableRefInput):
    retain_last: int | None = None
    older_than_days: int | None = None


_SNAPSHOT_UNSUPPORTED = ["snapshot_id", "parent_id", "refs"]
_ABSTAIN = "iceberg snapshot catalog table does not exist"


def _abstain(
    ctx: ToolContext, tool_name: str, table_id: str, extra: dict[str, Any]
) -> EvidenceEnvelope:
    return make_envelope(
        tool_name,
        {
            "summary": "Count history is not snapshot history. Snapshot ids were not loaded.",
            "table_id": table_id,
            "snapshots": [],
            "abstained": True,
            "abstain_reason": _ABSTAIN,
            **extra,
        },
        ctx=ctx,
        classification="unknown",
        error_category="unsupported",
        unsupported_fields=list(_SNAPSHOT_UNSUPPORTED),
        warnings=[
            "Count history is not snapshot history. "
            "No snapshot ids were invented from TableStatsHistory."
        ],
        measurement_method="unavailable",
        source_record_ids=[{"table_id": table_id}],
    )


def list_snapshots(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    table, error = require_table(ctx, parsed, "list_snapshots")
    if error is not None or table is None:
        return error or make_envelope(
            "list_snapshots",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    stats = latest_stats(ctx.session, table.id)
    count = stats.total_snapshots if stats is not None else None
    history_rows = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(TableStatsHistory)
        .where(TableStatsHistory.table_id == table.id),
    )
    catalog_present = relation_exists(ctx.session, "iceberg_snapshots")
    return _abstain(
        ctx,
        "list_snapshots",
        str(table.id),
        {
            **candidate(table),
            "scan_snapshot_count": count,
            "scan_count_source": "table_stats.total_snapshots",
            "stats_history_rows": history_rows,
            "catalog_loaded": catalog_present,
            "as_of": "scan",
        },
    )


def get_snapshot_refs(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    table, error = require_table(ctx, parsed, "get_snapshot_refs")
    if error is not None or table is None:
        return error or make_envelope(
            "get_snapshot_refs",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    total = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(ProtectedSnapshot)
        .where(ProtectedSnapshot.table_id == table.id),
    )
    rows = list(
        ctx.session.scalars(
            select(ProtectedSnapshot)
            .where(ProtectedSnapshot.table_id == table.id)
            .order_by(ProtectedSnapshot.protected_at.desc())
            .limit(200)
        )
    )
    refs = [
        {
            "snapshot_id": str(row.snapshot_id),
            "label": row.label,
            "protected_at": iso(row.protected_at),
            "source": "protected_snapshots",
        }
        for row in rows
    ]
    classification = "observed" if refs else "unknown"
    return make_envelope(
        "get_snapshot_refs",
        {
            "summary": "Protected snapshot ids only. Catalog branches and tags were not loaded.",
            **candidate(table),
            "refs": refs,
            "total": total,
            "catalog_refs_loaded": False,
        },
        ctx=ctx,
        classification=classification,
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        unsupported_fields=["branch", "tag", "parent_id"],
        warnings=["These ids are protected-snapshot records, not a live catalog ref listing."],
        coverage={"total": total, "returned": len(refs), "source": "protected_snapshots"},
        truncated=total > len(refs),
        row_count=len(refs),
    )


def compare_snapshots(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = CompareSnapshotsInput.model_validate(parsed.model_dump())
    table, error = require_table(ctx, spec, "compare_snapshots")
    if error is not None or table is None:
        return error or make_envelope(
            "compare_snapshots",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    if spec.left_snapshot_id or spec.right_snapshot_id:
        return _abstain(
            ctx,
            "compare_snapshots",
            str(table.id),
            {
                **candidate(table),
                "left_snapshot_id": spec.left_snapshot_id,
                "right_snapshot_id": spec.right_snapshot_id,
                "comparable": False,
            },
        )
    return _abstain(
        ctx,
        "compare_snapshots",
        str(table.id),
        {
            **candidate(table),
            "comparable": False,
            "note": "Scan-count rows are not snapshot ids and were not compared as snapshots.",
        },
    )


def preview_expiry(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = ExpiryInput.model_validate(parsed.model_dump())
    table, error = require_table(ctx, spec, "preview_expiry")
    if error is not None or table is None:
        return error or make_envelope(
            "preview_expiry",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    stats = latest_stats(ctx.session, table.id)
    total = stats.total_snapshots if stats is not None else None
    estimate = None
    unknown = []
    if total is None:
        unknown.append("total_snapshots")
    if spec.retain_last is None:
        unknown.append("retain_last")
    if total is not None and spec.retain_last is not None:
        estimate = total - spec.retain_last if total >= spec.retain_last else 0
    classification = "modeled" if estimate is not None else "unknown"
    return make_envelope(
        "preview_expiry",
        {
            "summary": "Eligible is not deleted. No expiry was executed.",
            **candidate(table),
            "eligible_estimate": estimate,
            "eligible_snapshot_ids": None,
            "deleted": False,
            "executed": False,
            "deleted_count": None,
            "total_snapshots": total,
            "retain_last": spec.retain_last,
            "older_than_days": spec.older_than_days,
            "unknown": unknown,
            "as_of": "scan",
        },
        ctx=ctx,
        classification=classification,
        observed_at=stats.scan_at if stats is not None else None,
        units={"eligible_estimate": "snapshots", "total_snapshots": "snapshots"},
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        unsupported_fields=["snapshot_id"],
        warnings=["Eligible is not deleted. This estimate lists no snapshot ids."],
        measurement_method="modeled_from_scan_count",
        coverage={"as_of": "scan", "executed": False},
    )


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_snapshots",
        family="snapshots",
        description="Does not invent snapshot ids from scan-count history.",
        input_model=TableRefInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=list_snapshots,
    ),
    ToolSpec(
        name="get_snapshot_refs",
        family="snapshots",
        description="Protected snapshot ids actually stored by Cryo. Catalog refs are unsupported.",
        input_model=TableRefInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_snapshot_refs,
    ),
    ToolSpec(
        name="compare_snapshots",
        family="snapshots",
        description="Refuses to compare snapshot ids that were not loaded from a catalog.",
        input_model=CompareSnapshotsInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=compare_snapshots,
    ),
    ToolSpec(
        name="preview_expiry",
        family="snapshots",
        description="Modeled eligibility from a scan count. Eligible is not deleted.",
        input_model=ExpiryInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=preview_expiry,
    ),
)
