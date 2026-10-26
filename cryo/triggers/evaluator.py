"""Trigger evaluator — integrates signals into the governor's admission loop."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.triggers.signals import SignalResult, any_signal_triggered, check_all_signals

logger = logging.getLogger(__name__)


class TriggerType(StrEnum):
    SCHEDULE = "schedule"  # Normal scheduled maintenance
    FILE_DEGRADATION = "file_degradation"  # Small-file ratio crossed threshold
    QUERY_LATENCY = "query_latency"  # Query latency spike detected
    COST_ANOMALY = "cost_anomaly"  # Projected savings spiked
    MANUAL = "manual"  # Operator-initiated


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
    - One or more signals are triggered (overrides cooldown), provided a scan newer than the
      table's last reclaim backs the signal (see _stale_signal_reason)
    """

    def __init__(self, signal_config: dict[str, Any] | None = None):
        self.signal_config = signal_config or {}
        self._manual_overrides: dict[uuid.UUID, str] = {}

    def request_manual_trigger(self, table_id: uuid.UUID, reason: str = "operator") -> None:
        """Mark a table as manually triggered (always admitted on next eval)."""
        self._manual_overrides[table_id] = reason

    def clear_manual_trigger(self, table_id: uuid.UUID) -> None:
        self._manual_overrides.pop(table_id, None)

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
        # Manual override always wins.
        manual_reason = self._manual_overrides.get(table_id)
        if manual_reason is not None:
            return TriggerResult(
                table_id=table_id,
                should_run=True,
                trigger_type=TriggerType.MANUAL,
                reason=f"Manual trigger: {manual_reason}",
                priority_boost=1.0,
            )

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

        # Check if any signal fired — signal triggers override cooldown, unless the evidence
        # behind them predates the table's last reclaim (see _stale_signal_reason).
        if any_signal_triggered(signal_results):
            stale_reason = _stale_signal_reason(
                session,
                table_id,
                min_interval=timedelta(
                    hours=float(self.signal_config.get("signal_min_interval_hours", 2.0))
                ),
            )
            if stale_reason is not None:
                return TriggerResult(
                    table_id=table_id,
                    should_run=False,
                    trigger_type=TriggerType.SCHEDULE,
                    reason=stale_reason,
                    signal_results=signal_results,
                )
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


def _stale_signal_reason(
    session: Session,
    table_id: uuid.UUID,
    *,
    min_interval: timedelta,
    now: datetime | None = None,
) -> str | None:
    """Why a fired signal must not bypass the cooldown, or None if it may.

    Signals read scanner-refreshed stats (small-file ratio, cost, anomalies), and nothing
    refreshes those after a compaction. A table compacted an hour ago still shows its
    pre-compaction ratio until the next scan, so the signal fired again and re-compacted it:
    470 repeat optimizes inside a 24h cooldown in one week (#178). A signal may only override
    the cooldown once a scan newer than the last reclaim backs it, and never within
    `min_interval` of that reclaim.
    """
    from cryo.db.models import AuditLog, TableStats
    from cryo.reporting.impact_signals import _RECLAIM_ACTIONS

    try:
        last_reclaim = session.scalar(
            select(func.max(AuditLog.ts)).where(
                AuditLog.table_id == table_id,
                AuditLog.status == "OK",
                AuditLog.action.in_(_RECLAIM_ACTIONS),
            )
        )
        last_reclaim_at = _aware(last_reclaim)
        if last_reclaim_at is None:
            return None
        current = now or datetime.now(UTC)
        if current - last_reclaim_at < min_interval:
            return (
                f"Signal ignored: table was maintained {last_reclaim_at.isoformat()}, "
                f"inside the {min_interval} minimum interval for signal triggers"
            )
        scan_at = _aware(
            session.scalar(select(TableStats.scan_at).where(TableStats.table_id == table_id))
        )
        if scan_at is None or scan_at < last_reclaim_at:
            return (
                "Signal ignored: its stats predate the last maintenance "
                f"({last_reclaim_at.isoformat()}); waiting for a fresh scan"
            )
    except Exception as exc:  # noqa: BLE001 - a failed check must not block signal triggers
        logger.debug("Signal freshness check failed for table %s: %s", table_id, exc)
    return None


def _aware(value: object) -> datetime | None:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _signal_name_to_type(signal_name: str) -> TriggerType:
    mapping = {
        "file_degradation": TriggerType.FILE_DEGRADATION,
        "query_latency": TriggerType.QUERY_LATENCY,
        "cost_anomaly": TriggerType.COST_ANOMALY,
    }
    return mapping.get(signal_name.lower(), TriggerType.SCHEDULE)
