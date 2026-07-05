"""Signal-driven maintenance triggers.

Detects three signals that indicate a table needs immediate maintenance:
1. File degradation ratio spike (too many small files accumulating)
2. Query latency spike (queries getting slower, likely due to file bloat)
3. Cost anomaly spike (storage or query costs increasing abnormally)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, QueryLog, Table, TableStats, TableStatsHistory

logger = logging.getLogger(__name__)


@dataclass
class MaintenanceSignal:
    """A detected signal indicating a table needs maintenance."""
    
    table_id: UUID
    table_name: str
    signal_type: str  # "file_degradation", "latency_spike", "cost_spike"
    severity: str  # "warning", "critical"
    current_value: float
    baseline_value: float
    threshold: float
    description: str
    detected_at: datetime


def detect_file_degradation_signal(
    session: Session,
    table_id: UUID,
    lookback_days: int = 7,
    warning_ratio: float = 2.0,
    critical_ratio: float = 3.0,
) -> MaintenanceSignal | None:
    """Detect if small file count is growing faster than expected.
    
    Signal triggers when:
    - Current small_files / baseline small_files > threshold
    
    Args:
        session: Database session
        table_id: Table to analyze
        lookback_days: Days of history to compute baseline
        warning_ratio: Ratio threshold for warning (e.g., 2.0 = 2x baseline)
        critical_ratio: Ratio threshold for critical alert
    
    Returns:
        Signal if detected, else None
    """
    since = datetime.now(UTC) - timedelta(days=lookback_days)
    
    # Get current stats
    current_stats = session.scalar(
        select(TableStats).where(TableStats.table_id == table_id)
    )
    if not current_stats:
        return None
    
    current_small_files = current_stats.small_files_count or 0
    if current_small_files == 0:
        return None
    
    # Get historical average
    history = session.execute(
        select(func.avg(TableStatsHistory.small_files_count))
        .where(
            TableStatsHistory.table_id == table_id,
            TableStatsHistory.scan_at >= since,
            TableStatsHistory.scan_at < current_stats.updated_at,
        )
    ).scalar()
    
    baseline_small_files = float(history or current_small_files)
    if baseline_small_files == 0:
        baseline_small_files = 1.0  # avoid div by zero
    
    ratio = current_small_files / baseline_small_files
    
    # Check thresholds
    if ratio < warning_ratio:
        return None
    
    severity = "critical" if ratio >= critical_ratio else "warning"
    
    table = session.scalar(select(Table).where(Table.id == table_id))
    table_name = table.name if table else str(table_id)
    
    return MaintenanceSignal(
        table_id=table_id,
        table_name=table_name,
        signal_type="file_degradation",
        severity=severity,
        current_value=float(current_small_files),
        baseline_value=baseline_small_files,
        threshold=critical_ratio if severity == "critical" else warning_ratio,
        description=(
            f"Small file count ({current_small_files}) is {ratio:.1f}x "
            f"baseline ({baseline_small_files:.0f}). "
            f"Compaction needed to prevent query slowdown."
        ),
        detected_at=datetime.now(UTC),
    )


def detect_latency_spike_signal(
    session: Session,
    table_id: UUID,
    lookback_days: int = 7,
    recent_hours: int = 24,
    warning_multiplier: float = 1.5,
    critical_multiplier: float = 2.0,
    min_queries: int = 10,
) -> MaintenanceSignal | None:
    """Detect if query latency is spiking compared to baseline.
    
    Signal triggers when:
    - Recent p95 latency > baseline p95 * threshold
    
    Args:
        session: Database session
        table_id: Table to analyze
        lookback_days: Days of history for baseline
        recent_hours: Hours for recent window
        warning_multiplier: Multiplier for warning threshold
        critical_multiplier: Multiplier for critical threshold
        min_queries: Minimum queries required to compute signal
    
    Returns:
        Signal if detected, else None
    """
    baseline_since = datetime.now(UTC) - timedelta(days=lookback_days)
    recent_since = datetime.now(UTC) - timedelta(hours=recent_hours)
    
    # Get baseline p95 latency
    baseline_latencies = session.scalars(
        select(QueryLog.execution_time_ms)
        .where(
            QueryLog.table_id == table_id,
            QueryLog.executed_at >= baseline_since,
            QueryLog.executed_at < recent_since,
            QueryLog.execution_time_ms.isnot(None),
        )
        .limit(10000)
    ).all()
    
    if len(baseline_latencies) < min_queries:
        return None
    
    # Get recent p95 latency
    recent_latencies = session.scalars(
        select(QueryLog.execution_time_ms)
        .where(
            QueryLog.table_id == table_id,
            QueryLog.executed_at >= recent_since,
            QueryLog.execution_time_ms.isnot(None),
        )
        .limit(10000)
    ).all()
    
    if len(recent_latencies) < min_queries:
        return None
    
    # Compute p95
    baseline_sorted = sorted(baseline_latencies)
    recent_sorted = sorted(recent_latencies)
    
    baseline_p95_idx = int(len(baseline_sorted) * 0.95)
    recent_p95_idx = int(len(recent_sorted) * 0.95)
    
    baseline_p95 = baseline_sorted[baseline_p95_idx]
    recent_p95 = recent_sorted[recent_p95_idx]
    
    if baseline_p95 == 0:
        baseline_p95 = 1.0
    
    ratio = recent_p95 / baseline_p95
    
    if ratio < warning_multiplier:
        return None
    
    severity = "critical" if ratio >= critical_multiplier else "warning"
    
    table = session.scalar(select(Table).where(Table.id == table_id))
    table_name = table.name if table else str(table_id)
    
    return MaintenanceSignal(
        table_id=table_id,
        table_name=table_name,
        signal_type="latency_spike",
        severity=severity,
        current_value=recent_p95,
        baseline_value=baseline_p95,
        threshold=critical_multiplier if severity == "critical" else warning_multiplier,
        description=(
            f"Query p95 latency ({recent_p95:.0f}ms) is {ratio:.1f}x "
            f"baseline ({baseline_p95:.0f}ms). "
            f"File bloat may be degrading query performance."
        ),
        detected_at=datetime.now(UTC),
    )


def detect_cost_spike_signal(
    session: Session,
    table_id: UUID,
    lookback_days: int = 30,
    recent_days: int = 7,
    warning_multiplier: float = 1.3,
    critical_multiplier: float = 1.5,
) -> MaintenanceSignal | None:
    """Detect if storage costs are spiking compared to baseline.
    
    Signal triggers when:
    - Recent storage size > baseline storage size * threshold
    
    Args:
        session: Database session
        table_id: Table to analyze
        lookback_days: Days of history for baseline
        recent_days: Days for recent window
        warning_multiplier: Multiplier for warning threshold
        critical_multiplier: Multiplier for critical threshold
    
    Returns:
        Signal if detected, else None
    """
    baseline_since = datetime.now(UTC) - timedelta(days=lookback_days)
    recent_since = datetime.now(UTC) - timedelta(days=recent_days)
    
    # Get baseline average size
    baseline_size = session.scalar(
        select(func.avg(TableStatsHistory.total_size_gb))
        .where(
            TableStatsHistory.table_id == table_id,
            TableStatsHistory.scan_at >= baseline_since,
            TableStatsHistory.scan_at < recent_since,
        )
    )
    
    if not baseline_size or baseline_size == 0:
        return None
    
    # Get recent average size
    recent_size = session.scalar(
        select(func.avg(TableStatsHistory.total_size_gb))
        .where(
            TableStatsHistory.table_id == table_id,
            TableStatsHistory.scan_at >= recent_since,
        )
    )
    
    if not recent_size or recent_size == 0:
        return None
    
    ratio = recent_size / baseline_size
    
    if ratio < warning_multiplier:
        return None
    
    severity = "critical" if ratio >= critical_multiplier else "warning"
    
    table = session.scalar(select(Table).where(Table.id == table_id))
    table_name = table.name if table else str(table_id)
    
    # Estimate monthly cost increase
    cost_increase_gb = recent_size - baseline_size
    # S3 Standard = ~$0.023/GB/month
    monthly_cost_increase = cost_increase_gb * 0.023
    
    return MaintenanceSignal(
        table_id=table_id,
        table_name=table_name,
        signal_type="cost_spike",
        severity=severity,
        current_value=recent_size,
        baseline_value=baseline_size,
        threshold=critical_multiplier if severity == "critical" else warning_multiplier,
        description=(
            f"Storage size ({recent_size:.1f}GB) is {ratio:.1f}x "
            f"baseline ({baseline_size:.1f}GB). "
            f"Estimated cost increase: ${monthly_cost_increase:.2f}/month. "
            f"Orphan file cleanup may reduce costs."
        ),
        detected_at=datetime.now(UTC),
    )


def detect_all_signals(
    session: Session,
    table_id: UUID | None = None,
) -> list[MaintenanceSignal]:
    """Detect all maintenance signals for a table or all tables.
    
    Args:
        session: Database session
        table_id: Specific table to check, or None for all active tables
    
    Returns:
        List of detected signals, sorted by severity
    """
    signals: list[MaintenanceSignal] = []
    
    if table_id:
        table_ids = [table_id]
    else:
        tables = session.scalars(
            select(Table.id).where(Table.is_active.is_(True))
        ).all()
        table_ids = list(tables)
    
    for tid in table_ids:
        # Check all three signal types
        if signal := detect_file_degradation_signal(session, tid):
            signals.append(signal)
        
        if signal := detect_latency_spike_signal(session, tid):
            signals.append(signal)
        
        if signal := detect_cost_spike_signal(session, tid):
            signals.append(signal)
    
    # Sort by severity (critical first) then by current/baseline ratio descending
    def sort_key(s: MaintenanceSignal) -> tuple[int, float]:
        severity_priority = 0 if s.severity == "critical" else 1
        ratio = s.current_value / max(s.baseline_value, 0.001)
        return (severity_priority, -ratio)
    
    signals.sort(key=sort_key)
    
    return signals


def should_trigger_maintenance(signal: MaintenanceSignal) -> bool:
    """Check if a signal warrants triggering immediate maintenance.
    
    Only critical signals with significant degradation trigger immediate action.
    Warnings are reported but don't auto-trigger.
    """
    if signal.severity != "critical":
        return False
    
    # Additional guard: ratio must be substantially above threshold
    ratio = signal.current_value / max(signal.baseline_value, 0.001)
    
    # For file degradation and latency, trigger if 2x+ threshold
    if signal.signal_type in ("file_degradation", "latency_spike"):
        return ratio >= (signal.threshold * 1.5)
    
    # For cost spikes, trigger if 1.5x+ threshold
    if signal.signal_type == "cost_spike":
        return ratio >= (signal.threshold * 1.2)
    
    return False
