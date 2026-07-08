"""Signal-driven maintenance trigger routes (governance tag).

Move 18: GET /tables/{id}/triggers surfaces per-table trigger evaluation
(file_degradation_rate, query_latency_spike, cost_anomaly) so an operator
can inspect which signal would override cooldown and force a run.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from cryo.api.deps import SessionDep, require_viewer
from cryo.triggers.evaluator import TriggerEvaluator, TriggerType

router = APIRouter(prefix="/api/v1", tags=["governance"])


class SignalResultRead(BaseModel):
    triggered: bool
    signal_name: str
    reason: str
    severity: str = "info"
    metric_value: float | None = None
    threshold: float | None = None


class TriggerEvaluationResponse(BaseModel):
    table_id: uuid.UUID
    should_run: bool
    trigger_type: str
    reason: str
    priority_boost: float
    is_due_for_schedule: bool
    signals: list[SignalResultRead]


@router.get(
    "/tables/{table_id}/triggers",
    response_model=TriggerEvaluationResponse,
    dependencies=[Depends(require_viewer)],
)
def evaluate_table_triggers(
    table_id: uuid.UUID,
    session: SessionDep,
    is_due_for_schedule: bool = False,
    force_signal_check: bool = False,
) -> TriggerEvaluationResponse:
    """Evaluate signal-driven triggers for a table.

    The governor runs this same evaluation each admission cycle. This
    endpoint exposes the result so the UI can show why a table was or
    was not admitted, and what signal would override cooldown.
    """
    evaluator = TriggerEvaluator()
    result = evaluator.evaluate(
        session,
        table_id,
        is_due_for_schedule=is_due_for_schedule,
        force_signal_check=force_signal_check,
    )
    return TriggerEvaluationResponse(
        table_id=result.table_id,
        should_run=result.should_run,
        trigger_type=result.trigger_type.value,
        reason=result.reason,
        priority_boost=result.priority_boost,
        is_due_for_schedule=is_due_for_schedule,
        signals=[
            SignalResultRead(
                triggered=signal.triggered,
                signal_name=signal.signal_name,
                reason=signal.reason,
                severity=signal.severity,
                metric_value=signal.metric_value,
                threshold=signal.threshold,
            )
            for signal in result.signal_results
        ],
    )


__all__: list[Any] = [
    "router",
    "evaluate_table_triggers",
    "TriggerEvaluationResponse",
    "SignalResultRead",
    "TriggerType",
]
