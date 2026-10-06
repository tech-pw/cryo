from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import LayoutRecommendation, QueryLog


@dataclass(frozen=True, slots=True)
class PtoSimulationResult:
    recommendation_id: uuid.UUID | None
    status: str
    method: str
    observations: int
    baseline_scan_fraction: float | None = None
    candidate_scan_fraction: float | None = None
    estimated_improvement_pct: float | None = None
    improvement_interval_pct: tuple[float, float] | None = None
    abstention_reason: str | None = None


def simulate_pto_recommendation(
    session: Session,
    table_id: uuid.UUID,
    *,
    min_observations: int = 20,
    history_days: int = 30,
) -> PtoSimulationResult:
    recommendation = session.scalar(
        select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
    )
    if recommendation is None or (recommendation.detail or {}).get("source") != "pto":
        return _abstain("missing_pto_recommendation")

    detail = recommendation.detail or {}
    candidate_fraction = detail.get("estimated_scan_fraction")
    if not isinstance(candidate_fraction, int | float) or not 0 <= candidate_fraction <= 1:
        return _abstain("missing_candidate_scan_estimate", recommendation.id)

    cutoff = datetime.now(UTC) - timedelta(days=history_days)
    logs = list(
        session.scalars(
            select(QueryLog).where(
                QueryLog.table_id == table_id,
                QueryLog.executed_at >= cutoff,
                QueryLog.files_read.is_not(None),
                QueryLog.files_pruned.is_not(None),
            )
        )
    )
    observations = [
        (int(log.files_read or 0), int(log.files_pruned or 0))
        for log in logs
        if int(log.files_read or 0) + int(log.files_pruned or 0) > 0
    ]
    if len(observations) < min_observations:
        return _abstain(
            "insufficient_file_pruning_observations", recommendation.id, len(observations)
        )

    files_read = sum(read for read, _pruned in observations)
    file_opportunities = sum(read + pruned for read, pruned in observations)
    baseline_fraction = files_read / file_opportunities
    if baseline_fraction <= 0:
        return _abstain("zero_observed_scan_fraction", recommendation.id, len(observations))

    estimated_improvement = (baseline_fraction - float(candidate_fraction)) / baseline_fraction
    baseline_error = math.sqrt(baseline_fraction * (1.0 - baseline_fraction) / file_opportunities)
    combined_error = math.sqrt(baseline_error**2 + 0.15**2)
    lower_candidate = min(1.0, float(candidate_fraction) + 1.96 * combined_error)
    upper_candidate = max(0.0, float(candidate_fraction) - 1.96 * combined_error)
    interval = (
        (baseline_fraction - lower_candidate) / baseline_fraction * 100,
        (baseline_fraction - upper_candidate) / baseline_fraction * 100,
    )
    return PtoSimulationResult(
        recommendation_id=recommendation.id,
        status="simulated",
        method="observed_file_touch_heuristic_v1",
        observations=len(observations),
        baseline_scan_fraction=round(baseline_fraction, 6),
        candidate_scan_fraction=round(float(candidate_fraction), 6),
        estimated_improvement_pct=round(estimated_improvement * 100, 2),
        improvement_interval_pct=(round(interval[0], 2), round(interval[1], 2)),
    )


def _abstain(
    reason: str,
    recommendation_id: uuid.UUID | None = None,
    observations: int = 0,
) -> PtoSimulationResult:
    return PtoSimulationResult(
        recommendation_id=recommendation_id,
        status="abstained",
        method="observed_file_touch_heuristic_v1",
        observations=observations,
        abstention_reason=reason,
    )
