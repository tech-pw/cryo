"""FinOps predictive budgets and cost forecasting.

Predicts next month's data lake costs based on growth trends
to enable proactive budget management.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, QueryLog, TableStatsHistory


@dataclass
class CostForecast:
    """Cost forecast for next month."""

    forecast_month: str
    current_month_storage_cost_usd: float
    forecast_storage_cost_usd: float
    storage_growth_rate_pct: float
    
    current_month_compute_cost_usd: float
    forecast_compute_cost_usd: float
    query_growth_rate_pct: float
    
    current_month_maintenance_cost_usd: float
    forecast_maintenance_cost_usd: float
    
    total_current_month_usd: float
    total_forecast_usd: float
    total_growth_rate_pct: float
    
    budget_limit_usd: float | None
    budget_exceeded_by_usd: float | None
    alert_level: str  # "green", "yellow", "red"


def calculate_storage_growth_rate(
    session: Session,
    lookback_days: int = 30,
) -> float:
    """
    Calculate storage growth rate based on historical data.
    
    Args:
        session: Database session
        lookback_days: Days to look back for growth calculation
    
    Returns:
        Daily growth rate as percentage
    """
    since = datetime.now(UTC) - timedelta(days=lookback_days)
    
    # Get storage size over time from TableStatsHistory
    stmt = (
        select(
            TableStatsHistory.scan_at,
            func.sum(TableStatsHistory.total_size_gb).label("total_size"),
        )
        .where(TableStatsHistory.scan_at >= since)
        .group_by(TableStatsHistory.scan_at)
        .order_by(TableStatsHistory.scan_at)
    )
    
    results = session.execute(stmt).all()
    
    if len(results) < 2:
        return 0.0  # Not enough data
    
    # Calculate linear growth rate
    first_size = results[0].total_size
    last_size = results[-1].total_size
    days_elapsed = (results[-1].scan_at - results[0].scan_at).days
    
    if days_elapsed == 0 or first_size == 0:
        return 0.0
    
    # Daily growth rate
    daily_growth_rate = ((last_size - first_size) / first_size) / days_elapsed
    
    return daily_growth_rate * 100  # Convert to percentage


def forecast_next_month_cost(
    session: Session,
    budget_limit_usd: float | None = None,
    s3_price_per_gb: float = 0.023,
    compute_hour_cost: float = 50.0,  # Average Trino cluster cost per hour
    lookback_days: int = 30,
) -> CostForecast:
    """
    Forecast next month's data lake costs.
    
    Args:
        session: Database session
        budget_limit_usd: Optional budget limit for alerts
        s3_price_per_gb: S3 storage price per GB/month
        compute_hour_cost: Average compute cost per hour
        lookback_days: Days to look back for trend analysis
    
    Returns:
        Cost forecast for next month
    """
    now = datetime.now(UTC)
    current_month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    
    # Next month
    if now.month == 12:
        next_month_start = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        next_month_start = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    
    forecast_month = next_month_start.strftime("%Y-%m")
    
    # 1. Storage Cost Forecast
    current_storage_gb = session.execute(
        select(func.sum(TableStatsHistory.total_size_gb))
        .where(TableStatsHistory.scan_at >= current_month_start)
        .order_by(TableStatsHistory.scan_at.desc())
        .limit(1)
    ).scalar() or 0.0
    
    storage_growth_rate_pct = calculate_storage_growth_rate(session, lookback_days)
    
    # Assume 30 days of growth
    forecast_storage_gb = current_storage_gb * (1 + (storage_growth_rate_pct / 100) * 30)
    
    current_month_storage_cost = current_storage_gb * s3_price_per_gb
    forecast_storage_cost = forecast_storage_gb * s3_price_per_gb
    
    # 2. Compute Cost Forecast
    # Count queries in current month
    current_month_queries = session.execute(
        select(func.count(QueryLog.id))
        .where(QueryLog.executed_at >= current_month_start)
    ).scalar() or 0
    
    # Historical query count (previous month)
    prev_month_start = current_month_start - timedelta(days=30)
    prev_month_queries = session.execute(
        select(func.count(QueryLog.id))
        .where(QueryLog.executed_at >= prev_month_start)
        .where(QueryLog.executed_at < current_month_start)
    ).scalar() or 1
    
    query_growth_rate_pct = ((current_month_queries - prev_month_queries) / prev_month_queries) * 100
    
    # Estimate compute hours from query count (rough: 1 query = 0.01 hours avg)
    current_compute_hours = current_month_queries * 0.01
    forecast_compute_hours = current_compute_hours * (1 + query_growth_rate_pct / 100)
    
    current_month_compute_cost = current_compute_hours * compute_hour_cost
    forecast_compute_cost = forecast_compute_hours * compute_hour_cost
    
    # 3. Maintenance Cost Forecast
    # Count maintenance operations this month
    current_month_maintenance = session.execute(
        select(func.count(AuditLog.id))
        .where(AuditLog.started_at >= current_month_start)
    ).scalar() or 0
    
    # Estimate maintenance cost (rough: 1 optimize = 0.5 hours avg)
    current_maintenance_hours = current_month_maintenance * 0.5
    forecast_maintenance_hours = current_maintenance_hours  # Assume constant
    
    current_month_maintenance_cost = current_maintenance_hours * compute_hour_cost
    forecast_maintenance_cost = forecast_maintenance_hours * compute_hour_cost
    
    # Total costs
    total_current = current_month_storage_cost + current_month_compute_cost + current_month_maintenance_cost
    total_forecast = forecast_storage_cost + forecast_compute_cost + forecast_maintenance_cost
    
    total_growth_rate_pct = ((total_forecast - total_current) / total_current) * 100 if total_current > 0 else 0
    
    # Budget alerts
    budget_exceeded_by = None
    alert_level = "green"
    
    if budget_limit_usd:
        if total_forecast > budget_limit_usd:
            budget_exceeded_by = total_forecast - budget_limit_usd
            alert_level = "red"
        elif total_forecast > budget_limit_usd * 0.85:
            alert_level = "yellow"
    
    return CostForecast(
        forecast_month=forecast_month,
        current_month_storage_cost_usd=current_month_storage_cost,
        forecast_storage_cost_usd=forecast_storage_cost,
        storage_growth_rate_pct=storage_growth_rate_pct,
        current_month_compute_cost_usd=current_month_compute_cost,
        forecast_compute_cost_usd=forecast_compute_cost,
        query_growth_rate_pct=query_growth_rate_pct,
        current_month_maintenance_cost_usd=current_month_maintenance_cost,
        forecast_maintenance_cost_usd=forecast_maintenance_cost,
        total_current_month_usd=total_current,
        total_forecast_usd=total_forecast,
        total_growth_rate_pct=total_growth_rate_pct,
        budget_limit_usd=budget_limit_usd,
        budget_exceeded_by_usd=budget_exceeded_by,
        alert_level=alert_level,
    )
