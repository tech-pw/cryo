from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import LayoutRecommendation, QueryPattern
from cryo.optimizer.pto.candidate_space import PtoPattern


@dataclass(frozen=True, slots=True)
class PtoDriftResult:
    recommendation_id: uuid.UUID | None
    status: str
    distance: float | None
    threshold: float
    abstention_reason: str | None = None


def assess_pto_drift(
    session: Session,
    table_id: uuid.UUID,
    *,
    threshold: float = 0.35,
) -> PtoDriftResult:
    if not 0 < threshold <= 1:
        raise ValueError("PTO drift threshold must be in (0, 1]")
    recommendation = session.scalar(
        select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
    )
    if recommendation is None or (recommendation.detail or {}).get("source") != "pto":
        return _abstain("missing_pto_recommendation", threshold)
    baseline = _distribution((recommendation.detail or {}).get("pattern_distribution"))
    if not baseline:
        return _abstain("missing_baseline_pattern_distribution", threshold, recommendation.id)

    rows = list(
        session.scalars(
            select(QueryPattern).where(
                QueryPattern.table_id == table_id,
                QueryPattern.filter_count > 0,
            )
        )
    )
    current = _current_distribution(rows)
    if not current:
        return _abstain("missing_current_query_patterns", threshold, recommendation.id)
    columns = set(baseline) | set(current)
    distance = 0.5 * sum(
        abs(baseline.get(column, 0.0) - current.get(column, 0.0)) for column in columns
    )
    return PtoDriftResult(
        recommendation_id=recommendation.id,
        status="drifted" if distance >= threshold else "stable",
        distance=round(distance, 6),
        threshold=threshold,
    )


def _current_distribution(rows: list[QueryPattern]) -> dict[str, float]:
    values = {
        row.column_name: PtoPattern(
            row.column_name,
            row.filter_count,
            row.equality_filter_count,
            row.range_filter_count,
            row.avg_selectivity,
        ).pruning_value
        for row in rows
    }
    total = sum(values.values())
    if total <= 0:
        return {}
    return {column: value / total for column, value in values.items()}


def _distribution(value: object) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {
        key: float(item)
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, int | float) and not isinstance(item, bool)
    }


def _abstain(
    reason: str,
    threshold: float,
    recommendation_id: uuid.UUID | None = None,
) -> PtoDriftResult:
    return PtoDriftResult(
        recommendation_id=recommendation_id,
        status="abstained",
        distance=None,
        threshold=threshold,
        abstention_reason=reason,
    )
