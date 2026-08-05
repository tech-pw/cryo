from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median

from cryo.intelligence.outcomes import MaintenanceOutcome, OutcomeDataset

DELETE_LIFECYCLE_EVIDENCE_SCHEMA = "delete-lifecycle-evidence/v1"
MIN_COHORT_OBSERVATIONS = 10


@dataclass(frozen=True, slots=True)
class DeleteCohortMetrics:
    cohort: str
    observations: int
    median_delete_pct_reduction: float | None
    median_duration_ms: float | None
    median_rewrite_bytes: float | None
    median_value_per_rewrite_gib: float | None
    median_p95_latency_reduction_ms: float | None


@dataclass(frozen=True, slots=True)
class DeleteLifecycleEvidence:
    schema: str
    status: str
    qualifies_policy_gate: bool
    lifecycle: DeleteCohortMetrics
    threshold_only: DeleteCohortMetrics
    matched_engines: tuple[str, ...]
    observed_value_lift: float | None
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_delete_lifecycle(
    dataset: OutcomeDataset,
    *,
    min_cohort_observations: int = MIN_COHORT_OBSERVATIONS,
) -> DeleteLifecycleEvidence:
    if min_cohort_observations < 1:
        raise ValueError("min_cohort_observations must be positive")
    lifecycle_rows = _eligible_rows(dataset, "rewrite_position_deletes")
    threshold_rows = _eligible_rows(dataset, "convert_deletes")
    lifecycle = _cohort_metrics("lifecycle", lifecycle_rows)
    threshold = _cohort_metrics("threshold_only", threshold_rows)
    matched_engines = tuple(
        sorted(
            {row.engine for row in lifecycle_rows if row.engine}
            & {row.engine for row in threshold_rows if row.engine}
        )
    )
    blockers: list[str] = []
    if len(lifecycle_rows) < min_cohort_observations:
        blockers.append("insufficient_lifecycle_observations")
    if len(threshold_rows) < min_cohort_observations:
        blockers.append("insufficient_threshold_observations")
    if not matched_engines:
        blockers.append("no_matched_engine_cohort")
    blockers.append("historical_cohorts_are_not_randomized_counterfactuals")
    lift = None
    if (
        lifecycle.median_value_per_rewrite_gib is not None
        and threshold.median_value_per_rewrite_gib is not None
    ):
        lift = round(
            lifecycle.median_value_per_rewrite_gib - threshold.median_value_per_rewrite_gib,
            6,
        )
    return DeleteLifecycleEvidence(
        schema=DELETE_LIFECYCLE_EVIDENCE_SCHEMA,
        status="advisory" if lifecycle_rows or threshold_rows else "insufficient_evidence",
        qualifies_policy_gate=False,
        lifecycle=lifecycle,
        threshold_only=threshold,
        matched_engines=matched_engines,
        observed_value_lift=lift,
        blockers=tuple(blockers),
    )


def _eligible_rows(dataset: OutcomeDataset, action: str) -> list[MaintenanceOutcome]:
    return [
        row
        for row in dataset.rows
        if row.action == action
        and row.lineage_complete
        and row.pre_delete_file_pct is not None
        and row.post_delete_file_pct is not None
        and row.duration_ms is not None
        and row.duration_ms >= 0
        and row.actual_rewrite_bytes is not None
        and row.actual_rewrite_bytes > 0
        and row.pre_p95_query_latency_ms is not None
        and row.post_p95_query_latency_ms is not None
    ]


def _cohort_metrics(
    cohort: str,
    rows: list[MaintenanceOutcome],
) -> DeleteCohortMetrics:
    if not rows:
        return DeleteCohortMetrics(cohort, 0, None, None, None, None, None)
    reductions = [
        max(0.0, (row.pre_delete_file_pct or 0.0) - (row.post_delete_file_pct or 0.0))
        for row in rows
    ]
    rewrite_bytes = [float(row.actual_rewrite_bytes or 0) for row in rows]
    value_per_gib = [
        reduction / max(rewritten / 1024**3, 1e-9)
        for reduction, rewritten in zip(reductions, rewrite_bytes, strict=True)
    ]
    latency_reductions = [
        (row.pre_p95_query_latency_ms or 0.0) - (row.post_p95_query_latency_ms or 0.0)
        for row in rows
    ]
    return DeleteCohortMetrics(
        cohort=cohort,
        observations=len(rows),
        median_delete_pct_reduction=round(median(reductions), 6),
        median_duration_ms=round(median(float(row.duration_ms or 0) for row in rows), 4),
        median_rewrite_bytes=round(median(rewrite_bytes), 4),
        median_value_per_rewrite_gib=round(median(value_per_gib), 6),
        median_p95_latency_reduction_ms=round(median(latency_reductions), 6),
    )
