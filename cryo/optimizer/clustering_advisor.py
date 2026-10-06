"""End-to-end clustering advice: baseline, candidates, simulation, economic gate.

This is the Phase 2 entry point. It composes the pieces:

    baseline_replay      what the current layout actually costs (exact, Phase 1)
    candidate_generation a bounded candidate set from workload signals
    layout_simulation    predicted cost per candidate, with a confidence bound
    -> here             the economic gate, and abstention

**Shadow-only.** The result is a recommendation with its evidence attached, never an applied
change. The research review is unambiguous (§6.3): auto-apply stays off until prediction
calibration and differential correctness gates pass, and every recommendation needs a canary
(Phase 3) before a full rewrite. Snowflake reports realized clustering cost differing from
estimate by 100%+; AutoComp measured 19% compute underestimation and 28% file-reduction
overestimation in production. A simulation is a prediction, and this module says so in its
output rather than in a footnote.

Abstention is a successful outcome. A table with a thin workload, weak signals, or no candidate
that repays its rewrite should produce a *reason*, not a recommendation.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cryo.optimizer.baseline_replay import BaselineReport, ReplayQuery, build_baseline
from cryo.optimizer.candidate_generation import (
    ColumnSignal,
    generate_candidates,
    screen_columns,
)
from cryo.optimizer.layout_simulation import (
    LayoutCandidate,
    SimulationResult,
    simulate_candidates,
)

logger = logging.getLogger(__name__)

# Initial gates from research note §6.3. Defaults to validate, not universal optima.
MIN_QUERY_OBSERVATIONS = 200
MIN_PARSER_COVERAGE = 0.80
MIN_BYTES_REDUCTION = 0.10
MIN_DAILY_BYTES_SAVED = 10 * 1024**3
MAX_PAYBACK_DAYS = 30


class AdviceOutcome(StrEnum):
    """What the advisor concluded."""

    RECOMMEND = "recommend"
    ABSTAIN_NO_BASELINE = "abstain_no_baseline"
    ABSTAIN_NO_ELIGIBLE_COLUMNS = "abstain_no_eligible_columns"
    ABSTAIN_NO_CANDIDATES = "abstain_no_candidates"
    ABSTAIN_NO_SAMPLE = "abstain_no_row_sample"
    ABSTAIN_BELOW_BENEFIT_THRESHOLD = "abstain_below_benefit_threshold"
    ABSTAIN_PAYBACK_TOO_LONG = "abstain_payback_too_long"
    ABSTAIN_CURRENT_LAYOUT_WINS = "abstain_current_layout_already_best"


@dataclass
class ClusteringAdvice:
    """A recommendation, or a reasoned abstention, with its evidence attached."""

    outcome: AdviceOutcome
    table_ref: str = ""
    winner: SimulationResult | None = None
    baseline: BaselineReport | None = None
    candidates_evaluated: int = 0
    rejected_columns: dict[str, str] = field(default_factory=dict)
    # Economics
    predicted_bytes_saved_per_day: float = 0.0
    rewrite_cost_usd: float = 0.0
    payback_days: float | None = None
    # Always true at Phase 2: no canary has run.
    requires_canary: bool = True
    detail: str = ""

    @property
    def recommended(self) -> bool:
        return self.outcome is AdviceOutcome.RECOMMEND

    def evidence_summary(self) -> dict[str, Any]:
        """Machine-readable evidence for the audit log and UI.

        Everything a reviewer needs to disagree with the recommendation: what it compared,
        what it predicted, what it rejected, and that it is a prediction.
        """
        return {
            "outcome": self.outcome.value,
            "table": self.table_ref,
            "winner": self.winner.candidate.name if self.winner else None,
            "predicted_file_reduction": (
                round(self.winner.conservative_file_reduction, 4) if self.winner else None
            ),
            "baseline_mean_files": (
                round(self.baseline.mean_files_touched, 2) if self.baseline else None
            ),
            "parser_coverage": (round(self.baseline.parser_coverage, 4) if self.baseline else None),
            "candidates_evaluated": self.candidates_evaluated,
            "rejected_columns": self.rejected_columns,
            "payback_days": self.payback_days,
            "is_prediction": True,
            "requires_canary": self.requires_canary,
            "detail": self.detail,
        }


def advise(
    *,
    table_ref: str,
    queries: Sequence[ReplayQuery],
    files: Sequence[Mapping[str, Any]],
    row_sample: Sequence[Mapping[str, Any]],
    column_signals: Sequence[ColumnSignal],
    cooccurrence: Mapping[tuple[str, str], float] | None = None,
    correlations: Mapping[tuple[str, str], float] | None = None,
    hilbert_available: bool = False,
    table_size_bytes: int = 0,
    storage_cost_per_gb_month: float = 0.023,
    rewrite_cost_per_gb: float = 0.0,
    min_observations: int = MIN_QUERY_OBSERVATIONS,
    min_parser_coverage: float = MIN_PARSER_COVERAGE,
    min_bytes_reduction: float = MIN_BYTES_REDUCTION,
    max_payback_days: int = MAX_PAYBACK_DAYS,
) -> ClusteringAdvice:
    """Produce clustering advice for one table, or abstain with a reason.

    Ordered cheapest-gate-first: a table that cannot clear the workload floor never reaches
    candidate simulation, which is the expensive step.
    """
    baseline = build_baseline(
        queries,
        files,
        min_observations=min_observations,
        min_parser_coverage=min_parser_coverage,
    )
    if baseline.abstained is not None:
        return ClusteringAdvice(
            outcome=AdviceOutcome.ABSTAIN_NO_BASELINE,
            table_ref=table_ref,
            baseline=baseline,
            detail=f"no usable baseline: {baseline.abstained.value}",
        )

    screened = screen_columns(column_signals)
    rejected = {name: reason.value for name, reason in screened.rejected.items()}
    if not screened.eligible:
        return ClusteringAdvice(
            outcome=AdviceOutcome.ABSTAIN_NO_ELIGIBLE_COLUMNS,
            table_ref=table_ref,
            baseline=baseline,
            rejected_columns=rejected,
            detail="every filtered column was screened out as a clustering dimension",
        )

    if not row_sample:
        return ClusteringAdvice(
            outcome=AdviceOutcome.ABSTAIN_NO_SAMPLE,
            table_ref=table_ref,
            baseline=baseline,
            rejected_columns=rejected,
            detail=(
                "curve layouts have no validated closed-form predictor; a joint row sample is "
                "required to simulate them"
            ),
        )

    candidates: list[LayoutCandidate] = generate_candidates(
        column_signals,
        cooccurrence=cooccurrence,
        correlations=correlations,
        hilbert_available=hilbert_available,
    )
    # 'current' alone is not a candidate set worth simulating.
    if len([c for c in candidates if c.strategy != "current"]) == 0:
        return ClusteringAdvice(
            outcome=AdviceOutcome.ABSTAIN_NO_CANDIDATES,
            table_ref=table_ref,
            baseline=baseline,
            rejected_columns=rejected,
            detail="no candidate layout was worth simulating for this workload",
        )

    report = simulate_candidates(
        [c for c in candidates if c.strategy != "current"],
        rows=row_sample,
        queries=queries,
        baseline_files=baseline.mean_files_touched,
        target_files=max(1, baseline.total_files),
    )
    winner = report.winner
    if winner is None:
        return ClusteringAdvice(
            outcome=AdviceOutcome.ABSTAIN_NO_CANDIDATES,
            table_ref=table_ref,
            baseline=baseline,
            rejected_columns=rejected,
            candidates_evaluated=0,
            detail="simulation produced no result",
        )

    advice = ClusteringAdvice(
        outcome=AdviceOutcome.RECOMMEND,
        table_ref=table_ref,
        winner=winner,
        baseline=baseline,
        candidates_evaluated=len(report.results),
        rejected_columns=rejected,
    )

    if winner.conservative_file_reduction <= 0:
        advice.outcome = AdviceOutcome.ABSTAIN_CURRENT_LAYOUT_WINS
        advice.detail = "no candidate beat the current layout on conservative predicted pruning"
        return advice

    if winner.conservative_file_reduction < min_bytes_reduction:
        advice.outcome = AdviceOutcome.ABSTAIN_BELOW_BENEFIT_THRESHOLD
        advice.detail = (
            f"best conservative reduction {winner.conservative_file_reduction:.1%} is below the "
            f"{min_bytes_reduction:.0%} floor"
        )
        return advice

    # Economics: does the predicted query saving repay the rewrite within the horizon?
    size_gb = table_size_bytes / 1024**3
    advice.rewrite_cost_usd = size_gb * rewrite_cost_per_gb
    daily_bytes_saved = (
        table_size_bytes * winner.conservative_file_reduction * baseline.weighted_query_count
    )
    advice.predicted_bytes_saved_per_day = daily_bytes_saved

    daily_value = (daily_bytes_saved / 1024**3) * (storage_cost_per_gb_month / 30.0)
    if daily_value > 0:
        advice.payback_days = round(advice.rewrite_cost_usd / daily_value, 1)
        if advice.payback_days > max_payback_days:
            advice.outcome = AdviceOutcome.ABSTAIN_PAYBACK_TOO_LONG
            advice.detail = (
                f"payback {advice.payback_days} days exceeds the {max_payback_days}-day horizon"
            )
            return advice

    advice.detail = (
        f"{winner.candidate.name} predicted to cut files touched by "
        f"{winner.conservative_file_reduction:.1%} (conservative bound). Prediction only -- "
        "validate with a canary rewrite before applying."
    )
    return advice
