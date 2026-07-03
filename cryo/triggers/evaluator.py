"""Trigger evaluator — integrates signals into the governor's admission loop."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from cryo.triggers.signals import SignalResult, check_all_signals, any_signal_triggered

logger = logging.getLogger(__name__)


class TriggerType(StrEnum):
    SCHEDULE = "schedule"          # Normal scheduled maintenance
    FILE_DEGRADATION = "file_degradation"  # Small-file ratio crossed threshold
    QUERY_LATENCY = "query_latency"        # Query latency spike detected
    COST_ANOMALY = "cost_anomaly"          # Projected savings spiked
    MANUAL = "manual"              # Operator-initiated


@dataclass
class TriggerResult:
    """Result of evaluating triggers for a table."""

    table_id: uuid.UUID
    should_run: bool
    trigger_type: TriggerType
    reason: str
    signal_results: list[SignalResult] = field(default_factory=list)
    priority_boost: float = 0.0  # Extra priority multiplier for signal-triggered runs


class TriggerEvaluator:
    """Evaluates whether a table should be admitted for maintenance.

    Integrates signal-based triggers with the normal schedule-based admission.
    A table is admitted if:
    - It is due for scheduled maintenance, OR
    - One or more signals are triggered (overrides cooldown)
    """

    def __init__(self, signal_config: dict[str, Any] | None = None):
        self.signal_config = signal_config or {}

    def evaluate(
        self,
        session: Session,
        table_id: uuid.UUID,
        is_due_for_schedule: bool,
        force_signal_check: bool = False,
    ) -> TriggerResult:
        """Evaluate whether a table should be admitted for maintenance.

        Args:
            session: Database session
            table_id: Table to evaluate
            is_due_for_schedule: Whether the table is due per normal schedule/cooldown
            force_signal_check: Always check signals, even if already scheduled

        Returns:
            TriggerResult with admission decision
        """
        # Always check signals for signal-triggered tables or when forced
        signal_results = []
        if not is_due_for_schedule or force_signal_check:
            signal_results = check_all_signals(session, table_id, self.signal_config)

        # If scheduled, admit normally
        if is_due_for_schedule:
            return TriggerResult(
                table_id=table_id,
                should_run=True,
                trigger_type=TriggerType.SCHEDULE,
                reason="Table is due for scheduled maintenance",
                signal_results=signal_results,
            )

        # Check if any signal fired — signal triggers override cooldown
        if any_signal_triggered(signal_results):
            triggered = [r for r in signal_results if r.triggered]
            primary = triggered[0]
            trigger_type = _signal_name_to_type(primary.signal_name)

            # Priority boost based on number of signals triggered
            priority_boost = len(triggered) * 0.5

            return TriggerResult(
                table_id=table_id,
                should_run=True,
                trigger_type=trigger_type,
                reason=f"Signal triggered: {primary.reason}",
                signal_results=signal_results,
                priority_boost=priority_boost,
            )

        # Nothing triggered
        return TriggerResult(
            table_id=table_id,
            should_run=False,
            trigger_type=TriggerType.SCHEDULE,
            reason="Not due for maintenance and no signals triggered",
            signal_results=signal_results,
        )

    def evaluate_batch(
        self,
        session: Session,
        table_ids: list[uuid.UUID],
        schedule_map: dict[uuid.UUID, bool],
    ) -> list[TriggerResult]:
        """Evaluate triggers for multiple tables.

        Args:
            session: Database session
            table_ids: Tables to evaluate
            schedule_map: Map of table_id → is_due_for_schedule

        Returns:
            List of TriggerResult sorted by priority (signal-triggered first)
        """
        results = []
        for table_id in table_ids:
            is_due = schedule_map.get(table_id, False)
            result = self.evaluate(session, table_id, is_due)
            if result.should_run:
                results.append(result)

        # Sort: signal-triggered with high priority boost first, then scheduled
        results.sort(
            key=lambda r: (
                0 if r.trigger_type != TriggerType.SCHEDULE else 1,
                -r.priority_boost,
            )
        )
        return results


def _signal_name_to_type(signal_name: str) -> TriggerType:
    mapping = {
        "file_degradation": TriggerType.FILE_DEGRADATION,
        "query_latency": TriggerType.QUERY_LATENCY,
        "cost_anomaly": TriggerType.COST_ANOMALY,
    }
    return mapping.get(signal_name.lower(), TriggerType.SCHEDULE)
