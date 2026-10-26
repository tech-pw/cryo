"""Dashboard drift/cost-trend points cover the fleet (issue #190); realized savings use the
impact filter, are a monthly rate and stay unknown when unmeasured (issue #189)."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as SessionCls

from cryo.config import Settings
from cryo.db.models import (
    AuditLog,
    Source,
    Table,
    TableCostHistory,
    TableStats,
    TableStatsHistory,
)
from cryo.db.repositories import datalake as datalake_repo
from tests.test_datalake_dashboard_repository import _PG_URL

DAY = datetime(2026, 7, 10, 9, 0, tzinfo=UTC)


def _table(session, *, name: str, reclaimable: int | None = None) -> uuid.UUID:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="series",
            table_name=f"{name}-{table_id}",
            first_seen_at=DAY,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=DAY,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=1,
            total_size_gb=1.0,
            query_count_30d=0,
            estimated_monthly_s3_cost_usd=1.0,
            reclaimable_bytes=reclaimable,
        )
    )
    session.flush()
    return table_id


def _history(session, table_id: uuid.UUID, at: datetime, *, size_gb: float, score: int) -> None:
    session.add(
        TableStatsHistory(
            table_id=table_id,
            scan_at=at,
            total_size_gb=size_gb,
            total_files=10,
            small_files_count=2,
            overall_score=score,
            grade="B",
        )
    )
    session.add(
        TableCostHistory(
            table_id=table_id,
            scan_at=at,
            current_monthly_cost_usd=size_gb,
            projected_savings_usd_month=0.5,
        )
    )


def _seed_two_tables_seconds_apart(session) -> None:
    first = _table(session, name="a")
    second = _table(session, name="b")
    # An early scan of the first table that the later scan the same day supersedes.
    _history(session, first, DAY - timedelta(hours=3), size_gb=99.0, score=10)
    _history(session, first, DAY, size_gb=10.0, score=60)
    _history(session, second, DAY + timedelta(seconds=7), size_gb=20.0, score=80)
    # The next day: only the first table was scanned.
    _history(session, first, DAY + timedelta(days=1), size_gb=11.0, score=70)
    session.commit()


def _assert_one_point_per_day(session) -> None:
    payload = datalake_repo.dashboard_datalake(session, settings=Settings(_env_file=None))
    drift = payload["drift"]
    assert len(drift) == 2
    first_day, second_day = drift
    assert first_day["total_size_gb"] == pytest.approx(30.0)
    assert first_day["avg_overall_score"] == pytest.approx(70.0)
    assert first_day["small_files_count"] == 4
    assert second_day["total_size_gb"] == pytest.approx(11.0)

    trend = datalake_repo._cost_trend(session, limit=30, settings=Settings(_env_file=None))
    assert [round(float(point["current_monthly_cost_usd"]), 4) for point in trend] == [30.0, 11.0]


def test_drift_and_cost_trend_take_latest_row_per_table_per_day(session) -> None:
    _seed_two_tables_seconds_apart(session)
    _assert_one_point_per_day(session)


@pytest.fixture()
def postgres_session():
    try:
        engine = create_engine(os.environ.get("CRYO_DATABASE_URL") or _PG_URL)
        connection = engine.connect()
    except OperationalError:
        pytest.skip("local Postgres not available")
    transaction = connection.begin()
    session = SessionCls(bind=connection, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_drift_series_on_postgres(postgres_session) -> None:
    # Postgres validates the window/GROUP BY shape strictly; SQLite doesn't. History is
    # cleared inside the test transaction, which is rolled back afterwards.
    postgres_session.query(TableStatsHistory).delete()
    postgres_session.query(TableCostHistory).delete()
    postgres_session.flush()
    _seed_two_tables_seconds_apart(postgres_session)
    _assert_one_point_per_day(postgres_session)


def _audit(session, table_id: uuid.UUID, action: str, status: str, freed: int | None) -> None:
    session.add(
        AuditLog(
            table_id=table_id,
            ts=DAY,
            action=action,
            status=status,
            actual_bytes_freed=freed,
        )
    )


def test_realized_savings_count_only_successful_reclaim_actions(session) -> None:
    table_id = _table(session, name="audit")
    _audit(session, table_id, "optimize", "OK", 1024**3)
    _audit(session, table_id, "remove_orphan_files", "succeeded", 1024**3)
    # Neither of these freed anything Impact would count.
    _audit(session, table_id, "optimize", "FAILED", 50 * 1024**3)
    _audit(session, table_id, "compute_statistics", "OK", 70 * 1024**3)
    session.commit()

    settings = Settings(_env_file=None)
    usd, freed = datalake_repo._total_realized_savings(session, settings=settings)
    assert freed == 2 * 1024**3
    assert usd == pytest.approx(round(2 * settings.cost_price_per_gb_month, 4))

    by_day = datalake_repo._realized_savings_by_day(session, settings=settings, limit=30)
    assert list(by_day.values()) == [pytest.approx(usd)]


def test_realized_savings_are_unknown_without_measured_actions(session) -> None:
    table_id = _table(session, name="nobytes")
    _audit(session, table_id, "optimize", "OK", None)
    _audit(session, table_id, "optimize", "FAILED", 10 * 1024**3)
    session.commit()

    usd, freed = datalake_repo._total_realized_savings(session, settings=Settings(_env_file=None))
    assert usd is None and freed is None


def test_cost_totals_reclaimable_is_none_when_unmeasured(session) -> None:
    _table(session, name="unmeasured")
    session.commit()
    totals = datalake_repo._sql_aggregate_cost_totals(session, settings=Settings(_env_file=None))
    assert totals["reclaimable_bytes"] is None
    assert totals["reclaimable_tables_measured"] == 0


def test_cost_totals_reclaimable_counts_measured_tables(session) -> None:
    _table(session, name="measured", reclaimable=500)
    _table(session, name="unmeasured")
    session.commit()
    totals = datalake_repo._sql_aggregate_cost_totals(session, settings=Settings(_env_file=None))
    assert totals["reclaimable_bytes"] == 500
    assert totals["reclaimable_tables_measured"] == 1
