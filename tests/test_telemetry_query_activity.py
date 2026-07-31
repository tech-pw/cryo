"""TableQueryActivity + refresh_query_count_30d -- the fix for TableStats.query_count_30d
being stuck at 0 for every table fleet-wide. query_patterns is aggregated per (table, column),
not a query-level counter, so nothing could answer "how many queries touched this table" from
it alone; these are the write- and read-side of a real day-bucketed per-table query counter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Source, Table, TableQueryActivity, TableStats
from cryo.telemetry.patterns import record_table_query_activity, refresh_query_count_30d


def _seed_table_with_stats(session, *, query_count_30d: int = 0) -> Table:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(id=uuid4(), source_id=source.id, schema_name="ns", table_name="events")
    session.add_all([source, table])
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="C",
            overall_score=65,
            total_files=100,
            small_files_count=10,
            total_size_gb=5.0,
            query_count_30d=query_count_30d,
        )
    )
    session.commit()
    return table


def test_record_table_query_activity_is_additive_within_a_day(session) -> None:
    """The Iceberg read-through path can record the same day across multiple windows/ticks --
    each call only knows about the queries *it* just saw, so recording must add, not replace."""
    table = _seed_table_with_stats(session)
    today = datetime.now(UTC).date()

    record_table_query_activity(session, {table.id: 5}, day=today)
    record_table_query_activity(session, {table.id: 3}, day=today)

    row = session.query(TableQueryActivity).filter_by(table_id=table.id, day=today).one()
    assert row.query_count == 8


def test_record_table_query_activity_skips_non_positive_counts(session) -> None:
    table = _seed_table_with_stats(session)
    record_table_query_activity(session, {table.id: 0}, day=datetime.now(UTC).date())
    assert session.query(TableQueryActivity).filter_by(table_id=table.id).count() == 0


def test_refresh_query_count_30d_sums_within_window_and_ignores_older(session) -> None:
    table = _seed_table_with_stats(session, query_count_30d=999)
    now = datetime.now(UTC)
    session.add_all(
        [
            TableQueryActivity(
                table_id=table.id, day=(now - timedelta(days=5)).date(), query_count=10
            ),
            TableQueryActivity(
                table_id=table.id, day=(now - timedelta(days=29)).date(), query_count=7
            ),
            # Outside the 30-day window -- must not be counted.
            TableQueryActivity(
                table_id=table.id, day=(now - timedelta(days=45)).date(), query_count=1000
            ),
        ]
    )
    session.commit()

    updated = refresh_query_count_30d(session, {table.id}, now=now)

    assert updated == 1
    stats = session.get(TableStats, table.id)
    assert stats.query_count_30d == 17


def test_refresh_query_count_30d_zeroes_out_when_no_recent_activity(session) -> None:
    """A table with stale query_count_30d from a previous window that has since fully aged out
    must be reset to 0, not left stuck at its last nonzero value."""
    table = _seed_table_with_stats(session, query_count_30d=42)
    now = datetime.now(UTC)
    session.add(
        TableQueryActivity(table_id=table.id, day=(now - timedelta(days=40)).date(), query_count=42)
    )
    session.commit()

    refresh_query_count_30d(session, {table.id}, now=now)

    assert session.get(TableStats, table.id).query_count_30d == 0


def test_refresh_query_count_30d_skips_tables_without_a_stats_row(session) -> None:
    """A table that's never been scanned has no TableStats row yet -- must not raise, and
    should not be counted as updated."""
    table_id = uuid4()
    updated = refresh_query_count_30d(session, {table_id})
    assert updated == 0
