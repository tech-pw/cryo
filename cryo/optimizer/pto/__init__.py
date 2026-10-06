"""Shadow-only layout recommender over PTO's parameter space.

Meduri, Kreismann, Barber, Reinwald, "PTO: A Workload-driven Predictive Table Optimizer for
Lakehouse Systems", SIGMOD 2026, DOI 10.1145/3786681. The search space (partition column,
target file size, row-group size, binpack/sort/zorder) and its workload-driven pruning follow
the paper. The scorer does not: PTO rewrites table samples per candidate and fits gradient
boosted trees to measured query latencies, whereas ``score_heuristic`` is a fixed weighted
estimate (``PTO_MODEL = "heuristic_v1"``), so recommendations stay shadow-only.
"""

from cryo.optimizer.pto.counterfactual import (
    CounterfactualArm,
    CounterfactualExperiment,
    CounterfactualReport,
    evaluate_counterfactual_experiment,
    record_counterfactual_experiment,
)
from cryo.optimizer.pto.drift import PtoDriftResult, assess_pto_drift
from cryo.optimizer.pto.holdout import PtoHoldoutResult, evaluate_pto_holdout, record_pto_holdout
from cryo.optimizer.pto.recommend import PtoRecommendationResult, generate_pto_recommendation
from cryo.optimizer.pto.simulate import PtoSimulationResult, simulate_pto_recommendation

__all__ = [
    "PtoDriftResult",
    "PtoHoldoutResult",
    "PtoRecommendationResult",
    "PtoSimulationResult",
    "CounterfactualArm",
    "CounterfactualExperiment",
    "CounterfactualReport",
    "assess_pto_drift",
    "evaluate_pto_holdout",
    "evaluate_counterfactual_experiment",
    "generate_pto_recommendation",
    "record_pto_holdout",
    "record_counterfactual_experiment",
    "simulate_pto_recommendation",
]
