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
    # Cost/latency calibration: a genuinely separate signal from the byte-yield fields above --
    # a table can predict its reclaimed bytes accurately while its real Trino cost or query
    # latency does something else entirely (e.g. traffic pattern shifted, or the derived Trino
    # $/GiB price itself moved that week for reasons unrelated to this table's compaction). See
    # cost_latency_calibration_row() and update_table_effectiveness's wiring for how this stays
    # visibly separate rather than blended into calibration_multiplier above.
    cost_status: str
    cost_evidence_sufficient: bool
    cost_stable: bool
    cost_observations: int
    realized_cost_ratio: float | None
    cost_calibration_multiplier: float
    cost_abstention_reason: str | None
    latency_status: str
    latency_evidence_sufficient: bool
    latency_stable: bool
    latency_observations: int
    realized_latency_ratio: float | None
    latency_calibration_multiplier: float
    latency_abstention_reason: str | None

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

    cost_calib = _reduction_ratio_calibration(
        rows,
        pre_field=lambda row: row.pre_estimated_cost_usd,
        post_field=lambda row: row.post_estimated_cost_usd,
        min_observations=min_observations,
    )
    latency_calib = _reduction_ratio_calibration(
        rows,
        pre_field=lambda row: row.pre_p95_query_latency_ms,
        post_field=lambda row: row.post_p95_query_latency_ms,
        min_observations=min_observations,
    )

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
        cost_status=cost_calib.status,
        cost_evidence_sufficient=cost_calib.evidence_sufficient,
        cost_stable=cost_calib.stable,
        cost_observations=cost_calib.observations,
        realized_cost_ratio=cost_calib.ratio,
        cost_calibration_multiplier=cost_calib.multiplier,
        cost_abstention_reason=cost_calib.abstention_reason,
        latency_status=latency_calib.status,
        latency_evidence_sufficient=latency_calib.evidence_sufficient,
        latency_stable=latency_calib.stable,
        latency_observations=latency_calib.observations,
        realized_latency_ratio=latency_calib.ratio,
        latency_calibration_multiplier=latency_calib.multiplier,
        latency_abstention_reason=latency_calib.abstention_reason,
    )


@dataclass(frozen=True, slots=True)
class _ReductionRatioCalibration:
    status: str
    evidence_sufficient: bool
    stable: bool
    observations: int
    ratio: float | None
    multiplier: float
    abstention_reason: str | None


def _reduction_ratio_calibration(
    rows: list[Any],
    *,
    pre_field: Any,
    post_field: Any,
    min_observations: int,
) -> _ReductionRatioCalibration:
    """Shared shape for cost and latency calibration: post/pre ratio per observation (lower is
    better -- did the real number actually go down after compaction), same
    evidence-sufficiency/drift-stability gate as the byte-yield calibration above, but a
    genuinely separate signal -- kept as its own multiplier rather than blended into
    calibration_multiplier, since a table can predict bytes accurately while its real cost or
    latency outcome does something else entirely (traffic pattern shift, Trino price moving
    for unrelated reasons that week, etc).

    Multiplier direction is inverted relative to the byte-yield one: a ratio *below* 1.0 (real
    cost/latency dropped) means compaction is demonstrably paying off here, so the multiplier
    should be >1 (reward it), not <1. ``2.0 - median(ratio)`` maps a 0.7 ratio (30% reduction)
    to a 1.3 multiplier and a 1.3 ratio (30% *increase*, compaction not helping) to 0.7,
    clamped to the same [0.5, 1.5] band the byte-yield multiplier uses so neither signal can
    swing scoring further than the other.
    """
    pairs = [
        (pre_field(row), post_field(row))
        for row in rows
        if pre_field(row) is not None and post_field(row) is not None and pre_field(row) > 0
    ]
    ratios = [post / pre for pre, post in pairs]
    observations = len(ratios)
    if not ratios:
        return _ReductionRatioCalibration(
            status="abstained",
            evidence_sufficient=False,
            stable=False,
            observations=0,
            ratio=None,
            multiplier=1.0,
            abstention_reason="missing_cost_or_latency_pairs",
        )
    split = observations // 2
    earlier = median(ratios[:split]) if split else None
    recent = median(ratios[split:]) if ratios[split:] else None
    drift = abs(recent - earlier) if earlier is not None and recent is not None else None
    sufficient = observations >= min_observations
    stable = drift is not None and drift <= MAX_RATIO_DRIFT
    if not sufficient:
        reason = "insufficient_outcome_evidence"
    elif not stable:
        reason = "realized_value_drift"
    else:
        reason = None
    aggregate_ratio = round(median(ratios), 4)
    multiplier = (
        round(min(1.5, max(0.5, 2.0 - aggregate_ratio)), 4) if sufficient and stable else 1.0
    )
    return _ReductionRatioCalibration(
        status="qualified" if reason is None else "abstained",
        evidence_sufficient=sufficient,
        stable=stable,
        observations=observations,
        ratio=aggregate_ratio,
        multiplier=multiplier,
        abstention_reason=reason,
    )


def _qualified_multiplier(
    raw: Mapping[str, Any] | None,
    *,
    status_key: str,
    evidence_key: str,
    stable_key: str,
    multiplier_key: str,
) -> float:
    if not isinstance(raw, Mapping):
        return 1.0
    feedback = raw.get("autocomp_realized_value")
    if not isinstance(feedback, Mapping):
        return 1.0
    if (
        feedback.get("schema") != AUTOCOMP_FEEDBACK_SCHEMA_VERSION
        or feedback.get(evidence_key) is not True
        or feedback.get(stable_key) is not True
        or feedback.get(status_key) != "qualified"
    ):
        return 1.0
    value = feedback.get(multiplier_key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    return min(1.5, max(0.5, float(value)))


def realized_value_multiplier(raw: Mapping[str, Any] | None) -> float:
    return _qualified_multiplier(
        raw,
        status_key="status",
        evidence_key="evidence_sufficient",
        stable_key="stable",
        multiplier_key="calibration_multiplier",
    )


def realized_cost_multiplier(raw: Mapping[str, Any] | None) -> float:
    """Real-cost-outcome multiplier -- qualified separately from realized_value_multiplier
    (byte-yield). See _reduction_ratio_calibration's docstring for why these stay independent
    rather than one blended number."""
    return _qualified_multiplier(
        raw,
        status_key="cost_status",
        evidence_key="cost_evidence_sufficient",
        stable_key="cost_stable",
        multiplier_key="cost_calibration_multiplier",
    )


def realized_latency_multiplier(raw: Mapping[str, Any] | None) -> float:
    """Real-latency-outcome multiplier -- qualified separately from realized_value_multiplier
    (byte-yield) and realized_cost_multiplier. See _reduction_ratio_calibration's docstring."""
    return _qualified_multiplier(
        raw,
        status_key="latency_status",
        evidence_key="latency_evidence_sufficient",
        stable_key="latency_stable",
        multiplier_key="latency_calibration_multiplier",
    )
