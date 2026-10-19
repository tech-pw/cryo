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
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as SessionCls

from cryo.config import Settings
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableCost,
    TableStats,
    TableStatsHistory,
)
from cryo.db.repositories.datalake import (
    _sql_aggregate_cost_totals,
    dashboard_datalake,
    recently_improved_tables,
)

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


def test_dashboard_freshness_buckets_are_exclusive_and_reconcile(session) -> None:
    source = _seed_source(session)
    as_of = datetime(2026, 8, 4, 12, tzinfo=UTC)
    cases = [
        ("fresh", timedelta(hours=2), 0, "within_24h"),
        ("day-boundary", timedelta(days=1), 0, "1_3d"),
        ("three-day-boundary", timedelta(days=3), 0, "3_7d"),
        ("week", timedelta(days=6), 0, "3_7d"),
        ("two-weeks", timedelta(days=14), 0, "7_15d"),
        ("month", timedelta(days=29), 0, "15_30d"),
        ("quarter", timedelta(days=89), 0, "1_3m"),
        ("half-year", timedelta(days=179), 0, "3_6m"),
        ("year", timedelta(days=364), 0, "6_12m"),
        ("old", timedelta(days=400), 0, "over_1y"),
        ("failed-fresh", timedelta(hours=1), 2, "failed"),
    ]
    for name, age, failures, _bucket in cases:
        table_id = uuid.uuid4()
        session.add(
            Table(
                id=table_id,
                source_id=source.id,
                schema_name="freshness",
                table_name=name,
                first_seen_at=as_of,
                is_active=True,
                consecutive_scan_failures=failures,
            )
        )
        session.add(
            TableStats(
                table_id=table_id,
                scan_at=as_of,
                last_snapshot_committed_at=as_of - age,
                needs_compaction=False,
                compaction_priority="LOW",
                grade="B",
                overall_score=80,
                total_files=1,
                small_files_count=0,
                total_size_gb=1,
                query_count_30d=0,
            )
        )
    unknown_id = uuid.uuid4()
    session.add(
        Table(
            id=unknown_id,
            source_id=source.id,
            schema_name="freshness",
            table_name="unknown",
            first_seen_at=as_of,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=unknown_id,
            scan_at=as_of,
            last_snapshot_committed_at=None,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=0,
            small_files_count=0,
            total_size_gb=0,
            query_count_30d=0,
        )
    )
    session.commit()

    payload = dashboard_datalake(session, now=as_of)
    counts = {item["key"]: item["count"] for item in payload["freshness_distribution"]}

    assert counts == {
        "within_24h": 1,
        "1_3d": 1,
        "3_7d": 2,
        "7_15d": 1,
        "15_30d": 1,
        "1_3m": 1,
        "3_6m": 1,
        "6_12m": 1,
        "over_1y": 1,
        "unknown": 1,
        "failed": 1,
    }
    assert sum(counts.values()) == payload["totals"]["active_tables"]


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
    # A stored 0 is a real zero, not "no figure".
    assert payload["totals"]["query_request_savings_usd_month"] == 0.0
    assert payload["totals"]["query_request_savings_tables"] == 1
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


def test_dashboard_cost_total_realized_savings_is_all_time_not_trend_bounded(
    session, monkeypatch
) -> None:
    from cryo.config import Settings
    from cryo.db.repositories import datalake as datalake_repo
    from cryo.db.repositories.datalake import dashboard_cost

    # date_trunc is Postgres-only -- _cost_trend (and its trend_limit bound) is unrelated
    # to what this test checks, so it's stubbed out same as the schema-compat test above.
    monkeypatch.setattr(datalake_repo, "_cost_trend", lambda *args, **kwargs: [])

    source = _seed_source(session)
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="billing",
            table_name="invoices",
            first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
            is_active=True,
        )
    )
    # One row inside a typical trend window, one row 200 days back -- well outside any
    # trend_limit that would ever be requested. total_realized_savings_usd must include both.
    session.add_all(
        [
            AuditLog(
                table_id=table_id,
                ts=datetime(2026, 7, 10, tzinfo=UTC),
                action="optimize",
                status="OK",
                actual_bytes_freed=1024**3,  # 1 GiB
            ),
            AuditLog(
                table_id=table_id,
                ts=datetime(2026, 1, 5, tzinfo=UTC),
                action="optimize",
                status="OK",
                actual_bytes_freed=2 * 1024**3,  # 2 GiB
            ),
        ]
    )
    session.commit()

    settings = Settings(_env_file=None)
    payload = dashboard_cost(session, trend_limit=5, settings=settings)

    expected_usd = round(3 * settings.cost_price_per_gb_month, 4)
    assert payload["totals"]["total_realized_savings_usd"] == expected_usd


def test_dashboard_cost_request_savings_sum_only_tables_with_a_cost_row(
    session, monkeypatch
) -> None:
    """A table with stats but no TableCost must not turn a partial sum into a fleet $0."""
    from cryo.db.repositories import datalake as datalake_repo
    from cryo.db.repositories.datalake import dashboard_cost

    monkeypatch.setattr(datalake_repo, "_cost_trend", lambda *args, **kwargs: [])

    source = _seed_source(session)
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    measured_id = uuid.uuid4()
    unmeasured_id = uuid.uuid4()
    session.add_all(
        [
            Table(
                id=measured_id,
                source_id=source.id,
                schema_name="billing",
                table_name="measured",
                first_seen_at=scan_at,
                is_active=True,
            ),
            Table(
                id=unmeasured_id,
                source_id=source.id,
                schema_name="billing",
                table_name="unmeasured",
                first_seen_at=scan_at,
                is_active=True,
            ),
        ]
    )
    session.add_all(
        [
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
                query_count_30d=4,
                estimated_monthly_s3_cost_usd=1.0,
            )
            for table_id in (measured_id, unmeasured_id)
        ]
    )
    session.add(
        TableCost(
            table_id=measured_id,
            scan_at=scan_at,
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=1000,
            dead_bytes=0,
            metadata_bytes=10,
            orphan_bytes=0,
            listed_bytes=1010,
            current_monthly_cost_usd=2.5,
            projected_monthly_cost_usd=2.0,
            projected_savings_usd_month=0.5,
            query_request_savings_usd_month=1.25,
        )
    )
    session.commit()

    payload = dashboard_cost(session, top_limit=5, trend_limit=5)
    assert payload["totals"]["query_request_savings_usd_month"] == 1.25
    assert payload["totals"]["query_request_savings_tables"] == 1


def _seed_graded_table(
    session,
    source: Source,
    *,
    name: str,
    grade: str,
    score: int,
    queries: int,
    scan_at: datetime,
) -> uuid.UUID:
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="analytics",
            table_name=name,
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=grade in {"D", "F"},
            compaction_priority="HIGH",
            grade=grade,
            overall_score=score,
            total_files=100,
            small_files_count=50,
            total_size_gb=10.0,
            query_count_30d=queries,
        )
    )
    return table_id


def test_hot_at_risk_ranks_queried_df_tables_and_skips_cold_ones(session) -> None:
    source = _seed_source(session)
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    _seed_graded_table(
        session, source, name="cold_f", grade="F", score=20, queries=0, scan_at=scan_at
    )
    _seed_graded_table(
        session, source, name="warm_d", grade="D", score=50, queries=40, scan_at=scan_at
    )
    _seed_graded_table(
        session, source, name="hot_f", grade="F", score=28, queries=900, scan_at=scan_at
    )
    _seed_graded_table(
        session, source, name="hot_b", grade="B", score=80, queries=5000, scan_at=scan_at
    )
    session.commit()

    payload = dashboard_datalake(session, hot_limit=5)

    assert [row["table_name"] for row in payload["hot_at_risk"]] == ["hot_f", "warm_d"]
    assert payload["hot_at_risk_count"] == 2


def _seed_job(
    session, table_id: uuid.UUID, *, status: str, started: datetime, finished: datetime
) -> None:
    destination = Destination(
        id=uuid.uuid4(), name=f"dest-{uuid.uuid4()}", engine="trino", config={}
    )
    run = MaintenanceRun(
        id=uuid.uuid4(), kind="maintenance", status="succeeded", created_at=started, summary={}
    )
    session.add_all([destination, run])
    session.flush()
    session.add(
        MaintenanceJob(
            id=uuid.uuid4(),
            run_id=run.id,
            table_id=table_id,
            destination_id=destination.id,
            mode="standard",
            status=status,
            started_at=started,
            finished_at=finished,
        )
    )


def test_recently_improved_claims_only_grades_raised_after_a_succeeded_job(session) -> None:
    source = _seed_source(session)
    now = datetime(2026, 7, 10, 6, tzinfo=UTC)
    started, finished = now - timedelta(hours=4), now - timedelta(hours=3)
    rescanned = now - timedelta(hours=1)

    improved = _seed_graded_table(
        session, source, name="fixed", grade="B", score=80, queries=1, scan_at=rescanned
    )
    session.add(
        TableStatsHistory(
            table_id=improved, scan_at=now - timedelta(hours=10), grade="D", overall_score=52
        )
    )
    _seed_job(session, improved, status="succeeded", started=started, finished=finished)

    failed = _seed_graded_table(
        session, source, name="failed_job", grade="B", score=80, queries=1, scan_at=rescanned
    )
    session.add(
        TableStatsHistory(
            table_id=failed, scan_at=now - timedelta(hours=10), grade="D", overall_score=52
        )
    )
    _seed_job(session, failed, status="failed", started=started, finished=finished)

    stale = _seed_graded_table(
        session,
        source,
        name="not_rescanned",
        grade="B",
        score=80,
        queries=1,
        scan_at=started - timedelta(minutes=5),
    )
    session.add(
        TableStatsHistory(
            table_id=stale, scan_at=now - timedelta(hours=10), grade="D", overall_score=52
        )
    )
    _seed_job(session, stale, status="succeeded", started=started, finished=finished)

    same = _seed_graded_table(
        session, source, name="unchanged", grade="C", score=64, queries=1, scan_at=rescanned
    )
    session.add(
        TableStatsHistory(
            table_id=same, scan_at=now - timedelta(hours=10), grade="C", overall_score=60
        )
    )
    _seed_job(session, same, status="succeeded", started=started, finished=finished)
    session.commit()

    rows, count = recently_improved_tables(session, now=now)

    assert count == 1
    assert rows[0]["table_name"] == "fixed"
    assert (rows[0]["previous_grade"], rows[0]["grade"]) == ("D", "B")
    assert (rows[0]["previous_score"], rows[0]["overall_score"]) == (52, 80)


def test_recently_improved_is_empty_without_recent_runs(session) -> None:
    assert recently_improved_tables(session, now=datetime(2026, 7, 10, tzinfo=UTC)) == ([], 0)
