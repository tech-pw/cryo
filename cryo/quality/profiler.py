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
    
    table_name = f"{table.catalog}.{table.schema_name}.{table.table_name}"
    
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
    
    # Analyze column statistics from raw metadata
    if current_stats.raw and isinstance(current_stats.raw, dict):
        # Extract column stats from manifests
        # Note: This is a simplified version - real implementation would query $manifests table
        manifest_stats = current_stats.raw.get("manifest_stats", {})
        
        for col_name, col_stats in manifest_stats.items():
            if not isinstance(col_stats, dict):
                continue
            
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
                    if hist.raw and isinstance(hist.raw, dict):
                        hist_manifest = hist.raw.get("manifest_stats", {}).get(col_name, {})
                        if hist_manifest:
                            hist_null = hist_manifest.get("null_count", 0)
                            hist_val = hist_manifest.get("value_count", 1)
                            historical_null_rates.append((hist_null / hist_val) * 100 if hist_val > 0 else 0)
                
                if historical_null_rates:
                    null_rate_baseline_pct = sum(historical_null_rates) / len(historical_null_rates)
            
            # Detect null rate spike (>2x baseline)
            null_rate_spike = null_rate_pct > null_rate_baseline_pct * 2 and null_rate_pct > 10
            if null_rate_spike:
                anomalies_detected += 1
            
            # Check value bounds
            min_value = col_stats.get("lower_bound")
            max_value = col_stats.get("upper_bound")
            
            value_range_changed = False
            value_range_explanation = "Normal"
            
            # Check for bound violations (e.g., negative revenue)
            if col_name.lower() in ("revenue", "amount", "price", "cost") and min_value and min_value < 0:
                value_range_changed = True
                value_range_explanation = f"Negative values detected (min: {min_value})"
                anomalies_detected += 1
            
            # Check for extreme value changes
            if len(historical_stats) > 5:
                historical_maxes = []
                for hist in historical_stats:
                    if hist.raw and isinstance(hist.raw, dict):
                        hist_manifest = hist.raw.get("manifest_stats", {}).get(col_name, {})
                        if hist_manifest and hist_manifest.get("upper_bound"):
                            historical_maxes.append(hist_manifest["upper_bound"])
                
                if historical_maxes and max_value:
                    avg_historical_max = sum(historical_maxes) / len(historical_maxes)
                    if max_value > avg_historical_max * 10:
                        value_range_changed = True
                        value_range_explanation = f"Max value spike: {max_value} vs historical avg {avg_historical_max:.0f}"
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
                )
            )
    
    # Detect schema changes
    schema_version_changed = False
    new_columns = []
    dropped_columns = []
    
    if len(historical_stats) > 0 and current_stats.raw and isinstance(current_stats.raw, dict):
        current_columns = set(current_stats.raw.get("manifest_stats", {}).keys())
        
        # Compare with oldest historical record
        oldest = historical_stats[0]
        if oldest.raw and isinstance(oldest.raw, dict):
            historical_columns = set(oldest.raw.get("manifest_stats", {}).keys())
            
            new_columns = list(current_columns - historical_columns)
            dropped_columns = list(historical_columns - current_columns)
            
            if new_columns or dropped_columns:
                schema_version_changed = True
                anomalies_detected += len(new_columns) + len(dropped_columns)
    
    return DataQualityProfile(
        table_id=str(table_id),
        table_name=table_name,
        profiled_at=datetime.now(UTC),
        column_metrics=column_metrics,
        anomalies_detected=anomalies_detected,
        schema_version_changed=schema_version_changed,
        new_columns=new_columns,
        dropped_columns=dropped_columns,
    )
