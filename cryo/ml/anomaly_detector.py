"""AI-powered anomaly detection for table health metrics.

Uses simple statistical methods (Z-score, exponential smoothing) to detect
anomalies in table metrics without requiring heavy ML dependencies.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.model_utils import qualified_table_name
from cryo.db.models import Table, TableStatsHistory


@dataclass
class AnomalyAlert:
    """An detected anomaly in table metrics."""

    table_id: str
    table_name: str
    metric_name: str
    current_value: float
    expected_value: float
    deviation_sigma: float
    severity: str  # "low", "medium", "high"
    description: str
    detected_at: datetime


def calculate_statistics(values: Sequence[float]) -> tuple[float, float]:
    """
    Calculate mean and standard deviation.

    Args:
        values: List of numeric values

    Returns:
        Tuple of (mean, std_dev)
    """
    if not values:
        return 0.0, 0.0

    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    std_dev = math.sqrt(variance)

    return mean, std_dev


def detect_anomaly_zscore(
    current_value: float,
    historical_values: Sequence[float],
    threshold_sigma: float = 3.0,
) -> tuple[bool, float, float]:
    """
    Detect anomaly using Z-score method.

    Args:
        current_value: Current value to check
        historical_values: Historical values for baseline
        threshold_sigma: Number of standard deviations for anomaly threshold

    Returns:
        Tuple of (is_anomaly, expected_value, deviation_sigma)
    """
    if len(historical_values) < 5:
        return False, current_value, 0.0

    mean, std_dev = calculate_statistics(historical_values)

    if std_dev == 0:
        return False, mean, 0.0

    deviation_sigma = abs(current_value - mean) / std_dev
    is_anomaly = deviation_sigma > threshold_sigma

    return is_anomaly, mean, deviation_sigma


def detect_table_anomalies(
    session: Session,
    table_id: str,
    lookback_days: int = 30,
    threshold_sigma: float = 3.0,
) -> list[AnomalyAlert]:
    """
    Detect anomalies in table metrics using historical data.

    Args:
        session: Database session
        table_id: Table UUID
        lookback_days: Days of historical data for baseline
        threshold_sigma: Z-score threshold for anomalies

    Returns:
        List of detected anomalies
    """
    table = session.get(Table, table_id)
    if not table:
        raise ValueError(f"Table {table_id} not found")

    table_name = qualified_table_name(table)

    # Get historical stats
    since = datetime.now(UTC) - timedelta(days=lookback_days)
    stmt = (
        select(TableStatsHistory)
        .where(TableStatsHistory.table_id == table_id)
        .where(TableStatsHistory.scan_at >= since)
        .order_by(TableStatsHistory.scan_at)
    )

    history = list(session.execute(stmt).scalars().all())

    if len(history) < 5:
        return []  # Not enough history for anomaly detection

    # Get current (latest) stats
    current = history[-1]

    # Historical values (all except current)
    historical = history[:-1]

    anomalies = []

    # 1. Small files anomaly
    small_files_history = [
        h.small_files_count for h in historical if h.small_files_count is not None
    ]
    if small_files_history and current.small_files_count is not None:
        is_anomaly, expected, deviation = detect_anomaly_zscore(
            current.small_files_count,
            small_files_history,
            threshold_sigma,
        )

        if is_anomaly:
            severity = (
                "high"
                if deviation > 5.0
                else "medium"
                if deviation > threshold_sigma * 1.5
                else "low"
            )
            description = (
                f"Small file count spike: {current.small_files_count:,.0f} "
                f"(expected: {expected:,.0f}, {deviation:.1f}σ)"
            )
            anomalies.append(
                AnomalyAlert(
                    table_id=str(table_id),
                    table_name=table_name,
                    metric_name="small_files_count",
                    current_value=current.small_files_count,
                    expected_value=expected,
                    deviation_sigma=deviation,
                    severity=severity,
                    description=description,
                    detected_at=current.scan_at,
                )
            )

    # 2. Storage growth anomaly
    size_history = [h.total_size_gb for h in historical if h.total_size_gb is not None]
    if size_history and current.total_size_gb is not None:
        is_anomaly, expected, deviation = detect_anomaly_zscore(
            current.total_size_gb,
            size_history,
            threshold_sigma,
        )

        if is_anomaly and current.total_size_gb > expected:  # Only alert on growth spikes
            severity = "high" if deviation > 5.0 else "medium"
            description = (
                f"Storage growth spike: {current.total_size_gb:.1f} GB "
                f"(expected: {expected:.1f} GB, {deviation:.1f}σ). "
                "Investigate data ingestion."
            )
            anomalies.append(
                AnomalyAlert(
                    table_id=str(table_id),
                    table_name=table_name,
                    metric_name="total_size_gb",
                    current_value=current.total_size_gb,
                    expected_value=expected,
                    deviation_sigma=deviation,
                    severity=severity,
                    description=description,
                    detected_at=current.scan_at,
                )
            )

    # 3. Health score degradation
    score_history = [h.overall_score for h in historical if h.overall_score is not None]
    if score_history and current.overall_score is not None:
        is_anomaly, expected, deviation = detect_anomaly_zscore(
            current.overall_score,
            score_history,
            2.5,  # Lower threshold for score degradation
        )

        if is_anomaly and current.overall_score < expected:  # Only alert on score drops
            severity = "high" if current.overall_score < 50 else "medium"
            description = (
                f"Health score drop: {current.overall_score:.1f} "
                f"(expected: {expected:.1f}, {deviation:.1f}σ). "
                "Table may need maintenance."
            )
            anomalies.append(
                AnomalyAlert(
                    table_id=str(table_id),
                    table_name=table_name,
                    metric_name="health_score",
                    current_value=current.overall_score,
                    expected_value=expected,
                    deviation_sigma=deviation,
                    severity=severity,
                    description=description,
                    detected_at=current.scan_at,
                )
            )

    # 4. Query latency spike (if we have query metrics in raw stats)
    if current.raw and isinstance(current.raw, dict):
        current_p95 = current.raw.get("p95_query_latency_ms")
        if current_p95:
            p95_history = []
            for h in historical:
                if h.raw and isinstance(h.raw, dict):
                    p95 = h.raw.get("p95_query_latency_ms")
                    if p95:
                        p95_history.append(p95)

            if p95_history:
                is_anomaly, expected, deviation = detect_anomaly_zscore(
                    current_p95,
                    p95_history,
                    2.5,
                )

                if is_anomaly and current_p95 > expected:
                    severity = "high" if deviation > 4.0 else "medium"
                    description = (
                        f"Query latency spike: P95 {current_p95:.0f}ms "
                        f"(expected: {expected:.0f}ms, {deviation:.1f}σ). "
                        "Check for data skew."
                    )
                    anomalies.append(
                        AnomalyAlert(
                            table_id=str(table_id),
                            table_name=table_name,
                            metric_name="p95_query_latency_ms",
                            current_value=current_p95,
                            expected_value=expected,
                            deviation_sigma=deviation,
                            severity=severity,
                            description=description,
                            detected_at=current.scan_at,
                        )
                    )

    return anomalies


def detect_all_anomalies(
    session: Session,
    min_severity: str = "medium",
    lookback_days: int = 30,
) -> list[AnomalyAlert]:
    """
    Detect anomalies across all monitored tables.

    Args:
        session: Database session
        min_severity: Minimum severity to return ("low", "medium", "high")
        lookback_days: Days of historical data

    Returns:
        List of detected anomalies sorted by severity
    """
    # Get all tables with recent stats
    since = datetime.now(UTC) - timedelta(days=1)
    stmt = (
        select(Table.id)
        .join(TableStatsHistory)
        .where(TableStatsHistory.scan_at >= since)
        .distinct()
    )

    table_ids = session.execute(stmt).scalars().all()

    all_anomalies = []
    for table_id in table_ids:
        try:
            anomalies = detect_table_anomalies(session, str(table_id), lookback_days)
            all_anomalies.extend(anomalies)
        except Exception:
            continue

    # Filter by severity
    severity_order = {"low": 0, "medium": 1, "high": 2}
    min_level = severity_order.get(min_severity, 1)

    filtered = [a for a in all_anomalies if severity_order.get(a.severity, 0) >= min_level]

    # Sort by severity (high first) then deviation
    filtered.sort(
        key=lambda a: (severity_order.get(a.severity, 0), a.deviation_sigma), reverse=True
    )

    return filtered
