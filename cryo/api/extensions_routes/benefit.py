"""Benefit-score and compaction-effectiveness routes (analytics tag)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from cryo.api.deps import SessionDep, require_operator, require_viewer
from cryo.api.schemas import JsonDict
from cryo.db.models import Table
from cryo.db.tenant_scope import get_scoped_resource
from cryo.maintenance.impact import ImpactWindow, table_maintenance_impact

# No prefix here: this router is included into extensions.py's `router`, which already has
# prefix="/api/v1" -- a second prefix here double-prefixed every path in this file
# (/api/v1/api/v1/tables/...), making them unreachable at the path the frontend/OpenAPI schema
# actually documents. Same bug existed identically in ai_features.py, compliance.py, qpp.py,
# and triggers.py -- fixed there too.
router = APIRouter(tags=["analytics"])


class BenefitScoreResponse(BaseModel):
    table_id: uuid.UUID
    partitions_scored: int
    top_candidates: list[JsonDict]


class EffectivenessStatsResponse(BaseModel):
    table_id: uuid.UUID
    total_input_files: int
    total_output_files: int
    run_count: int
    effectiveness_ratio: float
    effectiveness: str
    avg_duration_ms: int
    recommendation: str


class OperationImpactRead(BaseModel):
    operation: str
    ops: int
    ops_with_bytes: int
    ops_with_files: int
    bytes_freed: int
    files_in: int
    files_out: int


class ImpactWindowRead(BaseModel):
    days: int | None
    since: datetime | None
    ops: int
    ops_with_bytes: int
    ops_with_files: int
    bytes_freed: int
    files_in: int
    files_out: int
    by_operation: list[OperationImpactRead]


class MaintenanceImpactResponse(BaseModel):
    table_id: uuid.UUID
    recent: ImpactWindowRead
    lifetime: ImpactWindowRead


def _window_read(window: ImpactWindow) -> ImpactWindowRead:
    return ImpactWindowRead(
        days=window.days,
        since=window.since,
        ops=window.ops,
        ops_with_bytes=window.ops_with_bytes,
        ops_with_files=window.ops_with_files,
        bytes_freed=window.bytes_freed,
        files_in=window.files_in,
        files_out=window.files_out,
        by_operation=[OperationImpactRead(**vars(item)) for item in window.by_operation],
    )


@router.get(
    "/tables/{table_id}/maintenance-impact",
    response_model=MaintenanceImpactResponse,
    dependencies=[Depends(require_viewer)],
)
def get_table_maintenance_impact(
    table_id: uuid.UUID,
    session: SessionDep,
    window_days: int = Query(default=30, ge=1, le=365),
) -> MaintenanceImpactResponse:
    """Bytes freed and files reduced, as reported by engines on successful operations."""
    get_scoped_resource(session, Table, table_id, resource_type="table")
    recent, lifetime = table_maintenance_impact(
        session, table_id, now=datetime.now(UTC), window_days=window_days
    )
    return MaintenanceImpactResponse(
        table_id=table_id, recent=_window_read(recent), lifetime=_window_read(lifetime)
    )


@router.get(
    "/tables/{table_id}/benefit-scores",
    response_model=BenefitScoreResponse,
    dependencies=[Depends(require_viewer)],
)
def get_table_benefit_scores(
    table_id: uuid.UUID,
    session: SessionDep,
    top_n: int = 10,
) -> BenefitScoreResponse:
    """Get AutoComp benefit scores for table partitions."""
    from cryo.planner.benefit_score import get_top_compaction_candidates

    candidates = get_top_compaction_candidates(session, table_id, min_score=0.1, limit=top_n)

    return BenefitScoreResponse(
        table_id=table_id,
        partitions_scored=len(candidates),
        top_candidates=[
            {
                "partition_value": p.partition_value,
                "small_files": p.small_files_in_partition_count,
                "size_gb": p.partition_size_gb,
                "query_frequency": p.query_frequency,
                "benefit_score": p.compaction_benefit_score,
            }
            for p in candidates
        ],
    )


@router.post(
    "/tables/{table_id}/benefit-scores/update",
    response_model=BenefitScoreResponse,
    dependencies=[Depends(require_operator)],
)
def update_table_benefit_scores(
    table_id: uuid.UUID,
    session: SessionDep,
) -> BenefitScoreResponse:
    """Trigger immediate benefit score update for a table."""
    from cryo.planner.benefit_score import (
        get_top_compaction_candidates,
        update_partition_benefit_scores,
    )

    updated = update_partition_benefit_scores(session, table_id)
    candidates = get_top_compaction_candidates(session, table_id, min_score=0.1, limit=10)

    return BenefitScoreResponse(
        table_id=table_id,
        partitions_scored=updated,
        top_candidates=[
            {
                "partition_value": p.partition_value,
                "small_files": p.small_files_in_partition_count,
                "size_gb": p.partition_size_gb,
                "query_frequency": p.query_frequency,
                "benefit_score": p.compaction_benefit_score,
            }
            for p in candidates
        ],
    )


@router.get(
    "/tables/{table_id}/effectiveness",
    response_model=EffectivenessStatsResponse,
    dependencies=[Depends(require_viewer)],
)
def get_effectiveness_stats(
    table_id: uuid.UUID,
    session: SessionDep,
    lookback_days: int = 30,
) -> EffectivenessStatsResponse:
    """Get compaction effectiveness ratio for a table."""
    from cryo.planner.benefit_score import get_compaction_effectiveness_stats

    stats = get_compaction_effectiveness_stats(session, table_id, lookback_days)

    return EffectivenessStatsResponse(
        table_id=table_id,
        total_input_files=stats["total_input_files"],
        total_output_files=stats["total_output_files"],
        run_count=stats["run_count"],
        effectiveness_ratio=stats["effectiveness_ratio"],
        effectiveness=stats["effectiveness"],
        avg_duration_ms=stats["avg_duration_ms"],
        recommendation=stats["recommendation"],
    )


class AutoCompCandidateItem(BaseModel):
    partition_value: str
    small_files: int
    size_gb: float
    query_frequency: float
    benefit_score: float
    moop_score: float | None = None
    traits: JsonDict | None = None
    eligible: bool
    skip_reason: str | None = None


class AutoCompSkippedItem(BaseModel):
    partition_value: str
    skip_reason: str


class AutoCompCandidatesResponse(BaseModel):
    """AutoComp §A.5 -- fleet-ready view for Smart Maintenance's Compaction queue tab: ranked
    candidates, why anything was skipped, current effectiveness, and static-vs-AutoComp
    ordering, all in one call instead of stitching together /benefit-scores, /effectiveness,
    and /autocomp/compare separately."""

    table_id: uuid.UUID
    scan_at: str | None = None
    items: list[AutoCompCandidateItem]
    skipped: list[AutoCompSkippedItem]
    effectiveness: JsonDict | None = None
    compare: JsonDict


@router.get(
    "/tables/{table_id}/autocomp/candidates",
    response_model=AutoCompCandidatesResponse,
    dependencies=[Depends(require_viewer)],
)
def get_autocomp_candidates(
    table_id: uuid.UUID,
    session: SessionDep,
    min_score: float = 1.0,
    limit: int = 200,
) -> AutoCompCandidatesResponse:
    from sqlalchemy import func, select

    from cryo.db.models import TablePartition, TableStats
    from cryo.planner.benefit_score import (
        DEFAULT_MIN_BENEFIT_SCORE,
        compare_scheduling_strategies,
    )

    min_score = min_score if min_score is not None else DEFAULT_MIN_BENEFIT_SCORE
    latest_scan = session.scalar(
        select(func.max(TablePartition.scan_at)).where(TablePartition.table_id == table_id)
    )
    partitions: list[TablePartition] = []
    if latest_scan is not None:
        partitions = list(
            session.scalars(
                select(TablePartition)
                .where(
                    TablePartition.table_id == table_id,
                    TablePartition.scan_at == latest_scan,
                )
                .order_by(TablePartition.compaction_benefit_score.desc().nulls_last())
                .limit(max(1, min(limit, 2000)))
            )
        )

    items: list[AutoCompCandidateItem] = []
    skipped: list[AutoCompSkippedItem] = []
    for p in partitions:
        score = p.compaction_benefit_score or 0.0
        eligible = score >= min_score
        item = AutoCompCandidateItem(
            partition_value=p.partition_value or "",
            small_files=p.small_files_in_partition_count or 0,
            size_gb=p.partition_size_gb or 0.0,
            query_frequency=p.query_frequency or 0.0,
            benefit_score=score,
            moop_score=(p.autocomp_traits or {}).get("s_c") if p.autocomp_traits else None,
            traits=dict(p.autocomp_traits) if p.autocomp_traits else None,
            eligible=eligible,
            skip_reason=None if eligible else "below_min_benefit",
        )
        items.append(item)
        if not eligible:
            skipped.append(
                AutoCompSkippedItem(
                    partition_value=item.partition_value, skip_reason="below_min_benefit"
                )
            )

    stats = session.get(TableStats, table_id)
    effectiveness: dict[str, Any] | None = None
    if stats is not None and stats.raw:
        effectiveness = stats.raw.get("compaction_effectiveness")

    compare = compare_scheduling_strategies(session, table_id)

    return AutoCompCandidatesResponse(
        table_id=table_id,
        scan_at=latest_scan.isoformat() if latest_scan else None,
        items=items,
        skipped=skipped,
        effectiveness=effectiveness,
        compare=compare,
    )
