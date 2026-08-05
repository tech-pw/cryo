from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from statistics import median
from typing import Any

from cryo.intelligence.outcomes import OutcomeDataset

AUTOCOMP_FEEDBACK_SCHEMA_VERSION = "autocomp-realized-value/v1"
MIN_FEEDBACK_OBSERVATIONS = 20
MAX_RATIO_DRIFT = 0.25


@dataclass(frozen=True, slots=True)
class AutoCompRealizedValue:
    schema: str
    status: str
    evidence_sufficient: bool
    stable: bool
    eligible_outcomes: int
    complete_outcomes: int
    optimize_observations: int
    confidence: float
    predicted_bytes_freed: int
    actual_bytes_freed: int
    actual_to_predicted_ratio: float | None
    realized_reclaim_per_rewrite_byte: float | None
    calibration_multiplier: float
    earlier_ratio: float | None
    recent_ratio: float | None
    ratio_drift: float | None
    abstention_reason: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_autocomp_realized_value(
    dataset: OutcomeDataset,
    *,
    min_observations: int = MIN_FEEDBACK_OBSERVATIONS,
) -> AutoCompRealizedValue:
    if min_observations < 2:
        raise ValueError("min_observations must be at least 2")
    rows = [
        row
        for row in dataset.rows
        if row.action == "optimize"
        and row.lineage_complete
        and row.predicted_bytes_freed is not None
        and row.predicted_bytes_freed > 0
    ]
    ratios = [
        row.actual_bytes_freed / row.predicted_bytes_freed
        for row in rows
        if row.predicted_bytes_freed is not None
    ]
    predicted_total = sum(row.predicted_bytes_freed or 0 for row in rows)
    actual_total = sum(row.actual_bytes_freed for row in rows)
    rewrite_total = sum(
        row.actual_rewrite_bytes
        for row in rows
        if row.actual_rewrite_bytes is not None and row.actual_rewrite_bytes > 0
    )
    realized_yield = round(actual_total / rewrite_total, 6) if rewrite_total > 0 else None
    aggregate_ratio = round(actual_total / predicted_total, 4) if predicted_total > 0 else None
    split = len(ratios) // 2
    earlier_ratio = round(median(ratios[:split]), 4) if split else None
    recent_ratio = round(median(ratios[split:]), 4) if ratios[split:] else None
    ratio_drift = (
        round(abs(recent_ratio - earlier_ratio), 4)
        if earlier_ratio is not None and recent_ratio is not None
        else None
    )
    sufficient = len(rows) >= min_observations
    stable = ratio_drift is not None and ratio_drift <= MAX_RATIO_DRIFT
    confidence = round(
        min(1.0, len(rows) / max(min_observations * 2, 1)) * dataset.completeness,
        4,
    )
    if not rows:
        reason = "missing_lineage_complete_optimize_outcomes"
    elif not sufficient:
        reason = "insufficient_outcome_evidence"
    elif not stable:
        reason = "realized_value_drift"
    else:
        reason = None
    multiplier = round(min(1.5, max(0.5, median(ratios))), 4) if sufficient and stable else 1.0
    return AutoCompRealizedValue(
        schema=AUTOCOMP_FEEDBACK_SCHEMA_VERSION,
        status="qualified" if reason is None else "abstained",
        evidence_sufficient=sufficient,
        stable=stable,
        eligible_outcomes=dataset.eligible_count,
        complete_outcomes=dataset.complete_count,
        optimize_observations=len(rows),
        confidence=confidence,
        predicted_bytes_freed=predicted_total,
        actual_bytes_freed=actual_total,
        actual_to_predicted_ratio=aggregate_ratio,
        realized_reclaim_per_rewrite_byte=realized_yield,
        calibration_multiplier=multiplier,
        earlier_ratio=earlier_ratio,
        recent_ratio=recent_ratio,
        ratio_drift=ratio_drift,
        abstention_reason=reason,
    )


def realized_value_multiplier(raw: Mapping[str, Any] | None) -> float:
    if not isinstance(raw, Mapping):
        return 1.0
    feedback = raw.get("autocomp_realized_value")
    if not isinstance(feedback, Mapping):
        return 1.0
    if (
        feedback.get("schema") != AUTOCOMP_FEEDBACK_SCHEMA_VERSION
        or feedback.get("evidence_sufficient") is not True
        or feedback.get("stable") is not True
        or feedback.get("status") != "qualified"
    ):
        return 1.0
    value = feedback.get("calibration_multiplier")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    return min(1.5, max(0.5, float(value)))
