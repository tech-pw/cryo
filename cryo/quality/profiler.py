"""Zero-scan data quality profiling using Iceberg manifest statistics.

Leverages manifest-level min/max bounds, null counts, and NaN counts
to detect data quality issues without scanning data files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.model_utils import qualified_table_name
from cryo.db.models import Table, TableStats, TableStatsHistory


@dataclass
class ColumnQualityMetrics:
    """Quality metrics for a single column."""

    column_name: str
    null_rate_pct: float
    null_rate_baseline_pct: float
    null_rate_spike: bool
    min_value: Any
    max_value: Any
    value_range_changed: bool
    value_range_explanation: str
    column_type: str | None = None


@dataclass
class DataQualityProfile:
    """Data quality profile for a table."""

    table_id: str
    table_name: str
    profiled_at: datetime
    column_metrics: list[ColumnQualityMetrics]
    anomalies_detected: int
    schema_version_changed: bool
    new_columns: list[str]
    dropped_columns: list[str]
    commit_signals: dict[str, Any] | None = None


def _column_quality_by_name(raw: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Index a TableStats/TableStatsHistory ``raw["column_quality"]`` list by column name.

    The scanner (``cryo/scanner/quality.py``) persists this as a list of dicts (one per column,
    each carrying ``field_id``, ``column_name``, ``null_count``, ``value_count``, ``lower_bound``,
    ``upper_bound``) since field ids can collide across independently-scanned tables in ways column
    names don't for this profiler's purposes — re-keying by name here is just for lookup
    convenience.
    """
    if not raw or not isinstance(raw, dict):
        return {}
    entries = raw.get("column_quality") or []
    if not isinstance(entries, list):
        return {}
    return {
        str(entry["column_name"]): entry
        for entry in entries
        if isinstance(entry, dict) and "column_name" in entry
    }


def profile_table_quality(
    session: Session,
    table_id: str,
    lookback_days: int = 30,
) -> DataQualityProfile:
    """
    Profile data quality for a table using manifest-level statistics.

    Args:
        session: Database session
        table_id: Table UUID
        lookback_days: Days to look back for baseline comparison

    Returns:
        Data quality profile with anomaly detection
    """
    table = session.get(Table, table_id)
    if not table:
        raise ValueError(f"Table {table_id} not found")

    table_name = qualified_table_name(table)

    # Get current stats
    current_stats = session.get(TableStats, table_id)
    if not current_stats:
        raise ValueError(f"No stats found for table {table_id}")

    # Get historical stats for baseline
    since = datetime.now(UTC) - timedelta(days=lookback_days)
    history_stmt = (
        select(TableStatsHistory)
        .where(TableStatsHistory.table_id == table_id)
        .where(TableStatsHistory.scan_at >= since)
        .order_by(TableStatsHistory.scan_at)
    )
    historical_stats = session.execute(history_stmt).scalars().all()

    column_metrics: list[ColumnQualityMetrics] = []
    anomalies_detected = 0

    # Column stats come from the scanner's zero-scan manifest walk (cryo/scanner/quality.py),
    # persisted as a list under raw["column_quality"] — see cryo/scanner/analyze.py. Keyed here by
    # column name for convenient lookup against historical scans.
    current_columns_stats = _column_quality_by_name(current_stats.raw)

    for col_name, col_stats in current_columns_stats.items():
        # Current null rate
        null_count = col_stats.get("null_count", 0)
        value_count = col_stats.get("value_count", 1)
        null_rate_pct = (null_count / value_count) * 100 if value_count > 0 else 0

        # Calculate baseline null rate from history
        null_rate_baseline_pct = null_rate_pct  # Default to current
        if len(historical_stats) > 5:
            # Average null rate over history
            historical_null_rates = []
            for hist in historical_stats:
                hist_col = _column_quality_by_name(hist.raw).get(col_name)
                if hist_col:
                    hist_null = hist_col.get("null_count", 0)
                    hist_val = hist_col.get("value_count", 1)
                    historical_null_rates.append(
                        (hist_null / hist_val) * 100 if hist_val > 0 else 0
                    )

            if historical_null_rates:
                null_rate_baseline_pct = sum(historical_null_rates) / len(historical_null_rates)

        # Detect null rate spike (>2x baseline)
        null_rate_spike = null_rate_pct > null_rate_baseline_pct * 2 and null_rate_pct > 10
        if null_rate_spike:
            anomalies_detected += 1

        # Check value bounds (decoded from manifest lower/upper bounds by the scanner)
        min_value = col_stats.get("lower_bound")
        max_value = col_stats.get("upper_bound")

        value_range_changed = False
        value_range_explanation = "Normal"

        # Check for bound violations (e.g., negative revenue)
        if (
            col_name.lower() in ("revenue", "amount", "price", "cost")
            and min_value is not None
            and isinstance(min_value, int | float)
            and min_value < 0
        ):
            value_range_changed = True
            value_range_explanation = f"Negative values detected (min: {min_value})"
            anomalies_detected += 1

        # Check for extreme value changes
        if len(historical_stats) > 5:
            historical_maxes = []
            for hist in historical_stats:
                hist_col = _column_quality_by_name(hist.raw).get(col_name)
                hist_max = hist_col.get("upper_bound") if hist_col else None
                if isinstance(hist_max, int | float):
                    historical_maxes.append(hist_max)

            if historical_maxes and isinstance(max_value, int | float):
                avg_historical_max = sum(historical_maxes) / len(historical_maxes)
                if avg_historical_max and max_value > avg_historical_max * 10:
                    value_range_changed = True
                    value_range_explanation = (
                        f"Max value spike: {max_value} vs historical avg {avg_historical_max:.0f}"
                    )
                    anomalies_detected += 1

        column_metrics.append(
            ColumnQualityMetrics(
                column_name=col_name,
                null_rate_pct=null_rate_pct,
                null_rate_baseline_pct=null_rate_baseline_pct,
                null_rate_spike=null_rate_spike,
                min_value=min_value,
                max_value=max_value,
                value_range_changed=value_range_changed,
                value_range_explanation=value_range_explanation,
                column_type=col_stats.get("column_type"),
            )
        )

    # Detect schema changes
    schema_version_changed = False
    new_columns = []
    dropped_columns = []

    if len(historical_stats) > 0:
        current_columns = set(current_columns_stats.keys())

        # Compare with oldest historical record
        oldest = historical_stats[0]
        historical_columns = set(_column_quality_by_name(oldest.raw).keys())

        new_columns = list(current_columns - historical_columns)
        dropped_columns = list(historical_columns - current_columns)

        if new_columns or dropped_columns:
            schema_version_changed = True
            anomalies_detected += len(new_columns) + len(dropped_columns)

    raw = current_stats.raw if isinstance(current_stats.raw, dict) else {}
    commit_signals = raw.get("commit_signals")
    if not isinstance(commit_signals, dict):
        commit_signals = None
    elif commit_signals.get("arrival_overdue") or commit_signals.get("row_count_anomaly"):
        anomalies_detected += bool(commit_signals.get("arrival_overdue")) + bool(
            commit_signals.get("row_count_anomaly")
        )

    return DataQualityProfile(
        table_id=str(table_id),
        table_name=table_name,
        profiled_at=datetime.now(UTC),
        column_metrics=column_metrics,
        anomalies_detected=anomalies_detected,
        schema_version_changed=schema_version_changed,
        new_columns=new_columns,
        dropped_columns=dropped_columns,
        commit_signals=commit_signals,
    )
