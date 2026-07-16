"""Integration tests for cryo/db/repositories/datalake.py's dashboard_datalake().

Regression coverage for a real production bug: the Health dashboard's "Monthly savings" could
show larger than "S3 monthly now" itself, because the totals mixed TableStats.
estimated_monthly_s3_cost_usd (a cheap size-only heuristic refreshed on every scan) with
TableStats.projected_savings_usd_month (computed by project_savings() against a *different*,
more complete deep-tier cost baseline that was never itself surfaced). dashboard_datalake now
aggregates TableCost-preferred totals in SQL (falling back to TableStats columns only when no
TableCost row exists), which reconciles by construction and avoids materializing every
TableStats.raw / TableCost.raw JSONB blob into the api process -- that fleet-wide ORM load
OOMKilled cryo-api at 6Gi on a ~13k-table catalog. This also covers the "Cost by schema"
panel, which was always empty because dashboard_datalake never returned a cost_by_schema key
at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as SessionCls

from cryo.config import Settings
from cryo.db.models import Source, Table, TableCost, TableStats
from cryo.db.repositories.datalake import _sql_aggregate_cost_totals, dashboard_datalake

_PG_URL = "postgresql+psycopg://cryo:cryo@localhost:5432/cryo"


def _seed_source(session) -> Source:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table_with_cost(session, *, schema_name: str, precision: str) -> uuid.UUID:
    source = _seed_source(session)
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name=schema_name,
            table_name="t",
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=1,
            total_size_gb=1.0,
            query_count_30d=0,
            estimated_monthly_s3_cost_usd=1.0,
        )
    )
    session.add(
        TableCost(
            table_id=table_id,
            scan_at=scan_at,
            precision=precision,
            price_per_gb_month=0.023,
            live_bytes=100,
            dead_bytes=0,
            metadata_bytes=1,
            orphan_bytes=0,
            listed_bytes=101,
            current_monthly_cost_usd=1.0,
            projected_monthly_cost_usd=1.0,
            projected_savings_usd_month=0.0,
            query_request_savings_usd_month=0.0,
        )
    )
    return table_id


@pytest.fixture()
def postgres_session():
    """Real Postgres session -- SQLite doesn't enforce strict GROUP BY validation, so
    Postgres-only bugs (e.g. a SELECT expression Postgres can't prove matches a GROUP BY
    expression) are invisible to the rest of this suite, which runs on in-memory SQLite.
    Skips cleanly if no local Postgres is available (e.g. `docker compose up -d postgres`
    + `alembic upgrade head`, per the Makefile's `migrate` target).
    """
    try:
        engine = create_engine(_PG_URL)
        connection = engine.connect()
    except OperationalError:
        pytest.skip("local Postgres not available at localhost:5432 (see Makefile: migrate)")
    transaction = connection.begin()
    session = SessionCls(bind=connection, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_precision_coverage_group_by_matches_select_on_postgres(postgres_session) -> None:
    # Regression test: _sql_aggregate_cost_totals built func.coalesce(TableCost.precision,
    # HEURISTIC) as two separately-constructed expressions (once in the SELECT list, once in
    # GROUP BY). Postgres rejected this with "column table_cost.precision must appear in the
    # GROUP BY clause or be used in an aggregate function" on any catalog with a TableCost
    # row -- a real production 500 on GET /dashboards/datalake. SQLite never enforces this.
    session = postgres_session
    session.execute(text("SET search_path TO cryo, public"))
    _seed_table_with_cost(session, schema_name="analytics", precision="metadata")
    _seed_table_with_cost(session, schema_name="analytics", precision="heuristic")
    session.flush()

    totals = _sql_aggregate_cost_totals(session, settings=Settings())

    coverage = totals["precision_coverage"]
    assert coverage == {"metadata": 1, "heuristic": 1}


def test_dashboard_datalake_cost_totals_reconcile_when_table_cost_exists(session) -> None:
    source = _seed_source(session)
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    table = Table(
        id=table_id,
        source_id=source.id,
        schema_name="analytics",
        table_name="events",
        first_seen_at=scan_at,
        is_active=True,
    )
    session.add(table)
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=100,
            small_files_count=5,
            total_size_gb=50.0,
            query_count_30d=0,
            # Deliberately mismatched heuristic vs. the TableCost row below -- this is the
            # exact data shape that used to leak into the dashboard totals and make "Monthly
            # savings" exceed "S3 monthly now".
            estimated_monthly_s3_cost_usd=10.0,
            reclaimable_bytes=1000,
        )
    )
    session.add(
        TableCost(
            table_id=table_id,
            scan_at=scan_at,
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=1000,
            dead_bytes=500,
            metadata_bytes=50,
            orphan_bytes=0,
            listed_bytes=1550,
            reclaim_expire_bytes=100,
            reclaim_orphan_bytes=0,
            reclaim_optimize_bytes=0,
            reclaim_manifests_bytes=0,
            current_monthly_cost_usd=100.0,
            projected_monthly_cost_usd=55.0,
            projected_savings_usd_month=45.0,
            query_request_savings_usd_month=0.0,
        )
    )
    session.commit()

    result = dashboard_datalake(session)

    totals = result["totals"]
    # The TableCost row's self-consistent triple wins, not the mismatched TableStats heuristic.
    assert totals["estimated_monthly_s3_cost_usd"] == 100.0
    assert totals["projected_monthly_s3_cost_usd"] == 55.0
    assert totals["projected_savings_usd_month"] == 45.0
    # Savings must never exceed the current cost it's supposedly netting against.
    assert totals["projected_savings_usd_month"] <= totals["estimated_monthly_s3_cost_usd"]
    assert (
        totals["estimated_monthly_s3_cost_usd"] - totals["projected_monthly_s3_cost_usd"]
        == totals["projected_savings_usd_month"]
    )


def test_dashboard_datalake_populates_cost_by_schema(session) -> None:
    source = _seed_source(session)
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    for schema_name, current, projected, savings in [
        ("analytics", 80.0, 40.0, 40.0),
        ("billing", 20.0, 15.0, 5.0),
    ]:
        table_id = uuid.uuid4()
        session.add(
            Table(
                id=table_id,
                source_id=source.id,
                schema_name=schema_name,
                table_name="t",
                first_seen_at=scan_at,
                is_active=True,
            )
        )
        session.add(
            TableCost(
                table_id=table_id,
                scan_at=scan_at,
                precision="metadata",
                price_per_gb_month=0.023,
                live_bytes=1000,
                dead_bytes=0,
                metadata_bytes=0,
                orphan_bytes=0,
                listed_bytes=1000,
                reclaim_expire_bytes=0,
                reclaim_orphan_bytes=0,
                reclaim_optimize_bytes=0,
                reclaim_manifests_bytes=0,
                current_monthly_cost_usd=current,
                projected_monthly_cost_usd=projected,
                projected_savings_usd_month=savings,
                query_request_savings_usd_month=0.0,
            )
        )
    session.commit()

    result = dashboard_datalake(session)

    by_schema = {row["schema_name"]: row for row in result["cost_by_schema"]}
    assert by_schema["analytics"]["current_usd"] == 80.0
    assert by_schema["billing"]["savings_usd"] == 5.0


def test_dashboard_datalake_falls_back_to_table_stats_without_table_cost(session) -> None:
    """A table that's never had a deep-tier cost scan (no TableCost row yet) must still
    contribute something reasonable, not silently drop out of the totals."""
    source = _seed_source(session)
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="demo",
            table_name="orders",
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=1,
            total_size_gb=5.0,
            query_count_30d=0,
            estimated_monthly_s3_cost_usd=12.0,
            reclaimable_bytes=0,
        )
    )
    session.commit()

    result = dashboard_datalake(session)

    assert result["totals"]["estimated_monthly_s3_cost_usd"] == 12.0


def test_dashboard_datalake_response_matches_api_schema(session) -> None:
    """Web/client normalizeDashboard + DatalakeDashboardResponse must accept SQL output."""
    from cryo.api.schemas import DatalakeDashboardResponse

    source = _seed_source(session)
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="analytics",
            table_name="events",
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="C",
            overall_score=60,
            total_files=100,
            small_files_count=40,
            total_size_gb=12.0,
            query_count_30d=3,
            estimated_monthly_s3_cost_usd=9.0,
            projected_monthly_s3_cost_usd=5.0,
            projected_savings_usd_month=4.0,
            reclaimable_bytes=100,
        )
    )
    session.add(
        TableCost(
            table_id=table_id,
            scan_at=scan_at,
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=1000,
            dead_bytes=100,
            metadata_bytes=10,
            orphan_bytes=0,
            listed_bytes=1110,
            reclaim_expire_bytes=50,
            reclaim_orphan_bytes=0,
            reclaim_optimize_bytes=25,
            reclaim_manifests_bytes=0,
            current_monthly_cost_usd=20.0,
            projected_monthly_cost_usd=12.0,
            projected_savings_usd_month=8.0,
            query_request_savings_usd_month=0.0,
        )
    )
    session.commit()

    payload = dashboard_datalake(session, top_limit=5, drift_limit=5)
    DatalakeDashboardResponse.model_validate(payload)
    assert payload["totals"]["estimated_monthly_s3_cost_usd"] == 20.0
    assert payload["cost_by_schema"][0]["schema_name"] == "analytics"


def test_dashboard_cost_response_matches_api_schema(session, monkeypatch) -> None:
    """Cost page + CostDashboardResponse must accept SQL-aggregated dashboard_cost output."""
    from cryo.api.schemas import CostDashboardResponse
    from cryo.db.repositories import datalake as datalake_repo
    from cryo.db.repositories.datalake import dashboard_cost
    from cryo.domain.enums import OperationType

    # date_trunc is Postgres-only; unit tests use SQLite. Trend is unchanged by this fix.
    monkeypatch.setattr(datalake_repo, "_cost_trend", lambda *args, **kwargs: [])

    source = _seed_source(session)
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="billing",
            table_name="invoices",
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=20,
            small_files_count=2,
            total_size_gb=3.0,
            query_count_30d=1,
            estimated_monthly_s3_cost_usd=1.0,
            projected_savings_usd_month=0.0,
            reclaimable_bytes=0,
        )
    )
    session.add(
        TableCost(
            table_id=table_id,
            scan_at=scan_at,
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=2000,
            dead_bytes=500,
            metadata_bytes=20,
            orphan_bytes=0,
            listed_bytes=2520,
            reclaim_expire_bytes=400,
            reclaim_orphan_bytes=0,
            reclaim_optimize_bytes=100,
            reclaim_manifests_bytes=0,
            current_monthly_cost_usd=30.0,
            projected_monthly_cost_usd=18.0,
            projected_savings_usd_month=12.0,
            query_request_savings_usd_month=0.0,
        )
    )
    session.commit()

    payload = dashboard_cost(session, top_limit=5, trend_limit=5)
    CostDashboardResponse.model_validate(payload)
    assert payload["totals"]["current_monthly_cost_usd"] == 30.0
    assert payload["totals"]["projected_savings_usd_month"] == 12.0
    assert payload["cost_by_schema"][0]["schema_name"] == "billing"
    assert len(payload["top_opportunities"]) == 1
    top = payload["top_opportunities"][0]
    top_id = top.id if hasattr(top, "id") else top["id"]
    assert top_id == table_id
    ops = {
        item["operation"] if isinstance(item, dict) else item.operation
        for item in payload["savings_by_operation"]
    }
    assert ops <= {
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
        OperationType.OPTIMIZE,
        OperationType.OPTIMIZE_MANIFESTS,
    }
