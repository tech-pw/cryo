"""Signal-driven maintenance triggers.

Beyond schedule + cooldown, these triggers fire maintenance based on
real table health signals: file degradation rate, query latency spikes,
and cost anomalies.
"""

from cryo.triggers.evaluator import TriggerEvaluator, TriggerResult, TriggerType
from cryo.triggers.signals import (
    FileDegradationSignal,
    QueryLatencySignal,
    CostAnomalySignal,
    check_all_signals,
)

__all__ = [
    "CostAnomalySignal",
    "FileDegradationSignal",
    "QueryLatencySignal",
    "TriggerEvaluator",
    "TriggerResult",
    "TriggerType",
    "check_all_signals",
]
