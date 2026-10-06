"""Signal implementations for maintenance triggers.

Each signal evaluates table/partition health against a threshold
and returns whether maintenance should be triggered.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import QueryLog, TableStats, TableStatsHistory

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """Result from a single signal evaluation."""

    triggered: bool
    signal_name: str
    reason: str
    severity: str = "info"  # info, warning, critical
    metric_value: float | None = None
    threshold: float | None = None


class MaintenanceSignal(Protocol):
    def evaluate(self, session: Session, table_id: uuid.UUID) -> SignalResult: ...


@dataclass
class FileDegradationSignal:
    """Triggers when the small-file ratio crosses a threshold.

    E.g.: if >40% of files are small, trigger compaction regardless of schedule.
    """

    threshold_ratio: float = 0.40  # 40% small files = trigger
    min_total_files: int = 20  # Don't trigger on tiny tables

    def evaluate(self, session: Session, table_id: uuid.UUID) -> SignalResult:
        stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
        if stats is None:
            return SignalResult(
                triggered=False, signal_name="file_degradation", reason="No stats available"
            )

        total = stats.total_files or 0
        small = stats.small_files_count or 0

        if total < self.min_total_files:
            return SignalResult(
                triggered=False,
                signal_name="file_degradation",
                reason=f"Table too small ({total} files < {self.min_total_files} threshold)",
            )

        ratio = small / max(total, 1)
        triggered = ratio >= self.threshold_ratio

        return SignalResult(
            triggered=triggered,
            signal_name="file_degradation",
            reason=(
                f"Small file ratio {ratio:.1%} {'≥' if triggered else '<'} "
                f"threshold {self.threshold_ratio:.0%} ({small}/{total} files)"
            ),
            severity="warning" if ratio >= 0.6 else "info",
            metric_value=ratio,
            threshold=self.threshold_ratio,
        )


@dataclass
class QueryLatencySignal:
    """Triggers when table scan latency increases significantly.

    Compares average query duration over the last 24h vs the prior 7-day baseline.
    If P95 latency spikes >30% above baseline, it suggests data layout degradation.
    """

    spike_threshold_pct: float = 30.0  # % increase to trigger
    min_recent_queries: int = 10  # Need enough queries to be statistically meaningful
    recent_window_hours: int = 24
    baseline_window_hours: int = 168  # 7 days

    def evaluate(self, session: Session, table_id: uuid.UUID) -> SignalResult:
        now = datetime.now(UTC)
        recent_start = now - timedelta(hours=self.recent_window_hours)
        baseline_start = now - timedelta(hours=self.baseline_window_hours)

        # Recent average duration
        recent = (
            session.execute(
                select(
                    func.avg(QueryLog.duration_ms).label("avg_ms"),
                    func.count(QueryLog.id).label("count"),
                ).where(
                    QueryLog.table_id == table_id,
                    QueryLog.executed_at >= recent_start,
                    QueryLog.duration_ms.isnot(None),
                )
            )
            .mappings()
            .one()
        )

        recent_count = int(recent["count"] or 0)
        if recent_count < self.min_recent_queries:
            return SignalResult(
                triggered=False,
                signal_name="query_latency",
                reason=f"Not enough recent queries ({recent_count} < {self.min_recent_queries})",
            )

        # Baseline average duration (prior period, not including recent)
        baseline = (
            session.execute(
                select(func.avg(QueryLog.duration_ms).label("avg_ms")).where(
                    QueryLog.table_id == table_id,
                    QueryLog.executed_at >= baseline_start,
                    QueryLog.executed_at < recent_start,
                    QueryLog.duration_ms.isnot(None),
                )
            )
            .mappings()
            .one()
        )

        recent_avg = float(recent["avg_ms"] or 0)
        baseline_avg = float(baseline["avg_ms"] or 0)

        if baseline_avg <= 0:
            return SignalResult(
                triggered=False,
                signal_name="query_latency",
                reason="No baseline query latency data available",
            )

        pct_increase = ((recent_avg - baseline_avg) / baseline_avg) * 100
        triggered = pct_increase >= self.spike_threshold_pct

        return SignalResult(
            triggered=triggered,
            signal_name="query_latency",
            reason=(
                f"Query latency {pct_increase:+.0f}% vs baseline "
                f"({recent_avg:.0f}ms recent vs {baseline_avg:.0f}ms baseline). "
                f"Threshold: +{self.spike_threshold_pct:.0f}%"
            ),
            severity="warning" if pct_increase >= 50 else "info",
            metric_value=pct_increase,
            threshold=self.spike_threshold_pct,
        )


@dataclass
class CostAnomalySignal:
    """Triggers when projected savings exceed a threshold.

    If a table's projected_savings_usd_month rises above a threshold,
    it means the table has become expensive to query and maintenance
    is overdue.
    """

    min_savings_usd: float = 50.0  # Trigger if savings > $50/month
    growth_threshold_pct: float = 50.0  # Or if savings grew >50% vs last week

    def evaluate(self, session: Session, table_id: uuid.UUID) -> SignalResult:
        from cryo.db.models import TableCost

        cost = session.scalar(select(TableCost).where(TableCost.table_id == table_id))
        if cost is None:
            return SignalResult(
                triggered=False,
                signal_name="cost_anomaly",
                reason="No cost data available",
            )

        savings = cost.projected_savings_usd_month or 0
        triggered_by_abs = savings >= self.min_savings_usd

        # Check for grade degradation as proxy for cost growth
        # (TableStatsHistory doesn't track projected_savings yet)
        now = datetime.now(UTC)
        week_ago = now - timedelta(days=7)
        old_stats = session.scalar(
            select(TableStatsHistory)
            .where(
                TableStatsHistory.table_id == table_id,
                TableStatsHistory.scan_at >= week_ago,
            )
            .order_by(TableStatsHistory.scan_at.asc())
            .limit(1)
        )

        triggered_by_degradation = False
        if old_stats and old_stats.grade:
            # Grade degradation: A=4, B=3, C=2, D=1, F=0
            grade_map = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
            old_grade_val = grade_map.get(old_stats.grade, 0)
            # Get current grade from TableStats
            current_stats = session.scalar(
                select(TableStats).where(TableStats.table_id == table_id)
            )
            if current_stats and current_stats.grade:
                curr_grade_val = grade_map.get(current_stats.grade, 0)
                # Degraded by 2+ grades (e.g., A → C or B → D)
                triggered_by_degradation = (old_grade_val - curr_grade_val) >= 2

        triggered = triggered_by_abs or triggered_by_degradation

        return SignalResult(
            triggered=triggered,
            signal_name="cost_anomaly",
            reason=(
                f"Projected savings ${savings:.2f}/month "
                f"{'≥' if triggered_by_abs else '<'} "
                f"${self.min_savings_usd:.0f} threshold"
            ),
            severity="warning" if savings >= self.min_savings_usd * 2 else "info",
            metric_value=savings,
            threshold=self.min_savings_usd,
        )


@dataclass
class AnomalySignal:
    high_severity_threshold: int = 1
    medium_severity_threshold: int = 3

    def evaluate(self, session: Session, table_id: uuid.UUID) -> SignalResult:
        from cryo.ml.anomaly_detector import detect_table_anomalies

        alerts = detect_table_anomalies(session, table_id)
        critical = [a for a in alerts if str(a.severity).lower() == "high"]
        warning = [a for a in alerts if str(a.severity).lower() in {"medium", "low"}]
        triggered = bool(critical) or len(warning) >= self.medium_severity_threshold
        if critical:
            reason = critical[0].description
            severity = "critical"
            metric = float(len(critical))
        else:
            reason = f"{len(warning)} warning-level anomalies"
            severity = "warning" if triggered else "info"
            metric = float(len(warning))
        return SignalResult(
            triggered=triggered,
            signal_name="anomaly",
            reason=reason,
            severity=severity,
            metric_value=metric,
            threshold=float(self.high_severity_threshold),
        )


def check_all_signals(
    session: Session,
    table_id: uuid.UUID,
    config: dict[str, Any] | None = None,
) -> list[SignalResult]:
    """Evaluate all signals for a table.

    Args:
        session: Database session
        table_id: Table to check
        config: Optional signal configuration overrides

    Returns:
        List of SignalResult for each evaluated signal
    """
    config = config or {}

    signals: list[MaintenanceSignal] = [
        FileDegradationSignal(
            threshold_ratio=config.get("file_degradation_threshold", 0.40),
            min_total_files=config.get("min_total_files", 20),
        ),
        QueryLatencySignal(
            spike_threshold_pct=config.get("latency_spike_threshold_pct", 30.0),
            min_recent_queries=config.get("min_recent_queries", 10),
        ),
        CostAnomalySignal(
            min_savings_usd=config.get("cost_anomaly_min_savings_usd", 50.0),
        ),
        AnomalySignal(
            high_severity_threshold=config.get("anomaly_high_severity_threshold", 1),
            medium_severity_threshold=config.get("anomaly_medium_severity_threshold", 3),
        ),
    ]

    results = []
    for signal in signals:
        try:
            result = signal.evaluate(session, table_id)
            results.append(result)
        except Exception as e:
            logger.warning(f"Signal {type(signal).__name__} failed for table {table_id}: {e}")
            results.append(
                SignalResult(
                    triggered=False,
                    signal_name=type(signal).__name__.lower(),
                    reason=f"Signal evaluation failed: {e}",
                )
            )

    return results


def any_signal_triggered(results: list[SignalResult]) -> bool:
    """Check if any signal in the list was triggered."""
    return any(r.triggered for r in results)
