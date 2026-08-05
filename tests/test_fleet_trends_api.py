"""Tests for GET /api/v1/dashboard/fleet-trends aggregation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from cryo.api.dashboard_routes import (
    aggregate_fleet_cost_trend,
    aggregate_fleet_health_trend,
)
from cryo.db.models import Source, Table, TableCostHistory, TableStatsHistory
from tests.auth_helpers import VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb


def _seed_source(session) -> Source:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4().hex[:8]}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(session, source: Source, *, schema_name: str, table_name: str) -> Table:
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name=schema_name,
            table_name=table_name,
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.flush()
    table = session.get(Table, table_id)
    assert table is not None
    return table


def test_aggregate_fleet_health_trend_groups_by_day(session) -> None:
    from cryo.db.tenant_scope import require_tenant

    source = _seed_source(session)
    table_a = _seed_table(session, source, schema_name="analytics", table_name="orders")
    table_b = _seed_table(session, source, schema_name="analytics", table_name="users")
    day_one = datetime(2026, 7, 8, 10, tzinfo=UTC)
    day_two = datetime(2026, 7, 9, 10, tzinfo=UTC)
    session.add_all(
        [
            TableStatsHistory(
                table_id=table_a.id,
                scan_at=day_one,
                grade="B",
                overall_score=80,
            ),
            TableStatsHistory(
                table_id=table_b.id,
                scan_at=day_one,
                grade="D",
                overall_score=40,
            ),
            TableStatsHistory(
                table_id=table_a.id,
                scan_at=day_two,
                grade="F",
                overall_score=20,
            ),
        ]
    )
    session.flush()

    points = aggregate_fleet_health_trend(
        session,
        since=datetime.now(UTC) - timedelta(days=30),
        tenant_id=require_tenant(session),
    )
    assert len(points) == 2
    assert points[0].day.isoformat() == "2026-07-08"
    assert points[0].avg_health_score == 60.0
    assert points[0].tables_at_grade_df == 1
    assert points[1].day.isoformat() == "2026-07-09"
    assert points[1].avg_health_score == 20.0
    assert points[1].tables_at_grade_df == 1


def test_aggregate_fleet_cost_trend_sums_daily_totals(session) -> None:
    from cryo.db.tenant_scope import require_tenant

    source = _seed_source(session)
    table_a = _seed_table(session, source, schema_name="billing", table_name="invoices")
    table_b = _seed_table(session, source, schema_name="billing", table_name="payments")
    scan_day = datetime(2026, 7, 8, 15, tzinfo=UTC)
    session.add_all(
        [
            TableCostHistory(
                table_id=table_a.id,
                scan_at=scan_day,
                current_monthly_cost_usd=12.5,
                projected_savings_usd_month=2.0,
            ),
            TableCostHistory(
                table_id=table_b.id,
                scan_at=scan_day,
                current_monthly_cost_usd=7.5,
                projected_savings_usd_month=1.5,
            ),
        ]
    )
    session.flush()

    points = aggregate_fleet_cost_trend(
        session,
        since=datetime.now(UTC) - timedelta(days=30),
        tenant_id=require_tenant(session),
    )
    assert len(points) == 1
    assert points[0].day.isoformat() == "2026-07-08"
    assert points[0].total_monthly_cost_usd == 20.0
    assert points[0].projected_savings_usd_month == 3.5


def test_fleet_trends_endpoint_returns_grouped_series(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    source = _seed_source(session)
    table = _seed_table(session, source, schema_name="analytics", table_name="events")
    scan_day = datetime.now(UTC) - timedelta(days=2)
    session.add(
        TableStatsHistory(
            table_id=table.id,
            scan_at=scan_day,
            grade="C",
            overall_score=65,
        )
    )
    session.add(
        TableCostHistory(
            table_id=table.id,
            scan_at=scan_day,
            current_monthly_cost_usd=4.25,
            projected_savings_usd_month=0.75,
        )
    )
    session.commit()
    session.close()

    response = api_db.client.get("/api/v1/dashboard/fleet-trends?days=30", headers=VIEWER_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 30
    assert len(payload["health_trend"]) >= 1
    assert len(payload["cost_trend"]) >= 1
    assert payload["health_trend"][0]["avg_health_score"] == 65.0
    assert payload["cost_trend"][0]["total_monthly_cost_usd"] == 4.25
