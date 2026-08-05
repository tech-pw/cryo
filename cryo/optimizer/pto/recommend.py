from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import LayoutRecommendation, QueryPattern, Table, TableStats
from cryo.optimizer.pto.candidate_space import PtoPattern, build_candidate_space
from cryo.optimizer.pto.score_heuristic import score_candidates

PTO_MODEL = "heuristic_v1"


@dataclass(frozen=True, slots=True)
class PtoRecommendationResult:
    recommendation: LayoutRecommendation | None
    abstention_reason: str | None
    candidates_considered: int


def generate_pto_recommendation(
    session: Session,
    table_id: uuid.UUID,
    *,
    min_queries: int = 200,
    candidate_cap: int = 64,
) -> PtoRecommendationResult:
    table = session.get(Table, table_id)
    if table is None:
        raise ValueError(f"Table not found: {table_id}")
    stats = session.get(TableStats, table_id)
    if stats is None:
        return PtoRecommendationResult(None, "missing_table_stats", 0)

    rows = list(
        session.scalars(
            select(QueryPattern)
            .where(QueryPattern.table_id == table_id, QueryPattern.filter_count > 0)
            .order_by(QueryPattern.column_name)
        )
    )
    query_count = int(stats.query_count_30d or 0)
    if query_count < min_queries:
        return PtoRecommendationResult(None, "insufficient_query_history", 0)

    patterns = [
        PtoPattern(
            column_name=row.column_name,
            filter_count=row.filter_count,
            equality_filter_count=row.equality_filter_count,
            range_filter_count=row.range_filter_count,
            avg_selectivity=row.avg_selectivity,
        )
        for row in rows
    ]
    candidates = build_candidate_space(
        patterns,
        total_size_gb=float(stats.total_size_gb or 0.0),
        total_files=int(stats.total_files or 0),
        candidate_cap=candidate_cap,
    )
    ranked = score_candidates(
        candidates,
        patterns,
        total_files=int(stats.total_files or 0),
        total_size_gb=float(stats.total_size_gb or 0.0),
        candidate_cap=candidate_cap,
    )
    if not ranked:
        return PtoRecommendationResult(None, "empty_candidate_space", 0)

    best = ranked[0]
    candidate = best.candidate
    pattern_distribution = _pattern_distribution(patterns)
    detail = {
        "source": "pto",
        "shadow": True,
        "model": PTO_MODEL,
        "model_version": None,
        "generated_at": datetime.now(UTC).isoformat(),
        "domain": {
            "total_size_gb": float(stats.total_size_gb or 0.0),
            "total_files": int(stats.total_files or 0),
            "query_count_30d": query_count,
        },
        "uncertainty": {
            "confidence": "low",
            "calibrated": False,
            "reason": "heuristic_without_holdout_labels",
        },
        "pattern_distribution": pattern_distribution,
        "pto_params": {
            "partition_spec": candidate.partition_spec,
            "target_file_size_mb": candidate.target_file_size_mb,
            "row_group_size_mb": candidate.row_group_size_mb,
            "strategy": candidate.strategy,
            "sort_columns": list(candidate.sort_columns),
        },
        "current_params": {
            "partition_spec": (stats.raw or {}).get("partition_spec"),
            "sort_order": (stats.raw or {}).get("sort_order"),
        },
        "predicted_prune_improvement_pct": best.predicted_prune_improvement_pct,
        "estimated_rewrite_gb": candidate.estimated_rewrite_gb,
        "estimated_cost": best.estimated_cost,
        "estimated_scan_fraction": best.estimated_scan_fraction,
        "estimated_file_open_ratio": best.estimated_file_open_ratio,
        "candidates_considered": len(ranked),
        "driving_patterns": [
            {
                "column": pattern.column_name,
                "filter_count": pattern.filter_count,
                "avg_selectivity": pattern.avg_selectivity,
            }
            for pattern in sorted(
                patterns,
                key=lambda pattern: (-pattern.pruning_value, pattern.column_name),
            )[:5]
        ],
    }
    recommendation = session.scalar(
        select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
    )
    if recommendation is not None:
        existing_detail = recommendation.detail or {}
        if existing_detail.get("source") != "pto":
            return PtoRecommendationResult(
                None, "layout_recommendation_owned_by_other_analyzer", len(ranked)
            )
        if recommendation.status != "pending":
            return PtoRecommendationResult(None, "layout_recommendation_not_pending", len(ranked))
    recommended_columns = [{"column": column} for column in candidate.sort_columns]
    if recommendation is None:
        recommendation = LayoutRecommendation(
            id=uuid.uuid4(),
            table_id=table_id,
            current_sort_order=_sort_order((stats.raw or {}).get("sort_order")),
            recommended_strategy=candidate.strategy,
            recommended_columns=recommended_columns,
            estimated_prune_improvement=best.predicted_prune_improvement_pct,
            current_prune_efficiency=None,
            analysis_query_count=query_count,
            confidence=0.35,
            status="pending",
            detail=detail,
        )
        session.add(recommendation)
    else:
        recommendation.current_sort_order = _sort_order((stats.raw or {}).get("sort_order"))
        recommendation.recommended_strategy = candidate.strategy
        recommendation.recommended_columns = recommended_columns
        recommendation.estimated_prune_improvement = best.predicted_prune_improvement_pct
        recommendation.analysis_query_count = query_count
        recommendation.confidence = 0.35
        recommendation.detail = detail
    session.commit()
    session.refresh(recommendation)
    return PtoRecommendationResult(recommendation, None, len(ranked))


def _sort_order(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            result.append({"column": item})
        elif isinstance(item, dict) and isinstance(item.get("column"), str):
            result.append({"column": item["column"]})
    return result


def _pattern_distribution(patterns: list[PtoPattern]) -> dict[str, float]:
    total = sum(pattern.pruning_value for pattern in patterns)
    if total <= 0:
        return {}
    return {
        pattern.column_name: round(pattern.pruning_value / total, 6)
        for pattern in sorted(patterns, key=lambda item: item.column_name)
    }
