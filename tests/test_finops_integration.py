"""Integration tests for FinOps cost forecasting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from cryo.db.models import AuditLog, QueryLog, Source, Table, TableStatsHistory
from cryo.finops.forecast import CostForecast, forecast_next_month_cost


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={},
    )
    session.add(source)
    session.flush()
    return source


def _seed_table(session) -> Table:
    source = _seed_source(session)
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="finops",
        table_name="cost_table",
        is_active=True,
    )
    session.add(table)
    session.flush()
    return table


def test_forecast_next_month_cost_returns_valid_structure(session):
    """forecast_next_month_cost should return a populated CostForecast dataclass."""
    table = _seed_table(session)
    now = datetime.now(UTC)

    for day_offset in range(10):
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=now - timedelta(days=10 - day_offset),
                total_size_gb=100.0 + day_offset * 5,
                small_files_count=100,
            )
        )
        session.add(
            QueryLog(
                source_id=table.source_id,
                table_id=table.id,
                query_id=f"q-{day_offset}",
                engine="trino",
                query_text="SELECT 1",
                executed_at=now - timedelta(days=10 - day_offset),
                duration_ms=500,
            )
        )
    session.add(
        AuditLog(
            table_id=table.id,
            action="optimize",
            status="ok",
            ts=now - timedelta(days=1),
            duration_ms=60000,
        )
    )
    session.commit()

    storage_series = [
        (now - timedelta(days=9), 100.0),
        (now - timedelta(days=5), 120.0),
        (now, 150.0),
    ]
    query_series = [
        (now - timedelta(days=9), 50),
        (now - timedelta(days=5), 60),
        (now, 80),
    ]

    with (
        patch(
            "cryo.finops.forecast._fetch_daily_storage_totals",
            return_value=storage_series,
        ),
        patch(
            "cryo.finops.forecast._fetch_daily_query_counts",
            return_value=query_series,
        ),
        patch(
            "cryo.finops.forecast.calculate_storage_growth_rate",
            return_value=1.5,
        ),
    ):
        forecast = forecast_next_month_cost(
            session,
            budget_limit_usd=10_000.0,
            s3_price_per_gb=0.023,
            compute_hour_cost=50.0,
        )

    assert isinstance(forecast, CostForecast)
    assert forecast.forecast_month
    assert forecast.current_month_storage_cost_usd > 0
    assert forecast.forecast_storage_cost_usd > 0
    assert forecast.total_current_month_usd > 0
    assert forecast.total_forecast_usd > 0
    assert forecast.alert_level in ("green", "yellow", "red")


def test_forecast_next_month_cost_budget_alert_red(session):
    """Forecast exceeding budget should set alert_level to red."""
    with (
        patch(
            "cryo.finops.forecast._fetch_daily_storage_totals",
            return_value=[(datetime.now(UTC), 5000.0)],
        ),
        patch(
            "cryo.finops.forecast._fetch_daily_query_counts",
            return_value=[(datetime.now(UTC), 1000)],
        ),
        patch("cryo.finops.forecast.calculate_storage_growth_rate", return_value=5.0),
    ):
        forecast = forecast_next_month_cost(session, budget_limit_usd=100.0)

    assert forecast.alert_level == "red"
    assert forecast.budget_exceeded_by_usd is not None
    assert forecast.budget_exceeded_by_usd > 0


def test_forecast_next_month_cost_growth_rates_are_numeric(session):
    """Growth rate fields should be finite floats even with sparse data."""
    with (
        patch("cryo.finops.forecast._fetch_daily_storage_totals", return_value=[]),
        patch("cryo.finops.forecast._fetch_daily_query_counts", return_value=[]),
        patch("cryo.finops.forecast.calculate_storage_growth_rate", return_value=0.0),
    ):
        forecast = forecast_next_month_cost(session)

    assert isinstance(forecast.storage_growth_rate_pct, (int, float))
    assert isinstance(forecast.query_growth_rate_pct, (int, float))
    assert isinstance(forecast.total_growth_rate_pct, (int, float))
    assert forecast.total_current_month_usd >= 0
