"""Benefit-score and compaction-effectiveness routes (analytics tag)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from cryo.api.deps import SessionDep, require_operator, require_viewer
from cryo.api.schemas import JsonDict

router = APIRouter(prefix="/api/v1", tags=["analytics"])


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
