from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from cryo.telemetry.patterns import ColumnPatternStats, aggregate_query_patterns


class _ScalarResult:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _NullQuery:
    """Stands in for session.query(TableQueryActivity).filter_by(...) -- always "not found",
    matching the check-then-write pattern _replace_table_query_activity_from_query_logs and
    record_table_query_activity use (portable across sqlite/postgres, unlike the Postgres-only
    on_conflict_do_update _upsert_query_patterns still uses)."""

    def filter_by(self, **kwargs: Any) -> _NullQuery:
        return self

    def one_or_none(self) -> None:
        return None


class _PatternSession:
    def __init__(self, logs: list[Any]):
        self._logs = logs
        self.executed: list[Any] = []
        self.added: list[Any] = []
        self.committed = False

    def scalars(self, statement: Any) -> _ScalarResult:
        return _ScalarResult(self._logs)

    def execute(self, statement: Any) -> _ScalarResult:
        self.executed.append(statement)
        # refresh_query_count_30d's rolling-sum query goes through here too; empty is fine --
        # these unit tests only assert on the query_patterns upsert path, not the query-count
        # rollup (see test_telemetry_query_activity.py for that, against a real session).
        return _ScalarResult([])

    def query(self, model: Any) -> _NullQuery:
        return _NullQuery()

    def add(self, instance: Any) -> None:
        self.added.append(instance)

    def get(self, model: Any, table_id: Any) -> None:
        # No persisted TableStats row for this fake table -- refresh_query_count_30d is
        # documented to skip tables without one, so this must stay a no-op, not raise.
        return None

    def commit(self) -> None:
        self.committed = True


def test_aggregate_query_patterns_inserts_column_stats() -> None:
    table_id = uuid.uuid4()
    session = _PatternSession(
        [
            SimpleNamespace(
                table_id=table_id,
                executed_at=datetime(2026, 7, 31, tzinfo=UTC),
                filter_columns=[{"column": "event_date", "is_range": True}],
                join_columns=[{"column": "customer_id"}],
                projection_columns=[{"column": "amount"}],
            )
        ]
    )

    updated = aggregate_query_patterns(session, table_id=table_id)  # type: ignore[arg-type]

    assert updated == 3
    # 3 query_patterns upserts (event_date, customer_id, amount) via session.execute, +
    # 1 refresh_query_count_30d rolling-sum SELECT. table_query_activity's own upsert goes
    # through query()/add(), not execute() -- see session.added.
    assert len(session.executed) == 4
    assert len(session.added) == 1
    assert session.committed is True


def test_aggregate_query_patterns_computes_selectivity_from_files_read_pruned() -> None:
    """A QueryLog row with real files_read/files_pruned should populate avg_selectivity on its
    filter columns -- this is the fix for the 2026-08-14 finding that every query_patterns row
    had avg_selectivity=NULL because nothing in the aggregation pipeline ever set it, which
    silently forced every PTO layout recommendation onto the 0.5 "no information" fallback."""
    table_id = uuid.uuid4()
    session = _PatternSession(
        [
            SimpleNamespace(
                table_id=table_id,
                executed_at=datetime(2026, 7, 31, tzinfo=UTC),
                filter_columns=[{"column": "event_date", "is_range": True}],
                join_columns=[],
                projection_columns=[],
                files_read=10,
                files_pruned=90,  # 10 of 100 candidate files actually read -> selectivity 0.1
            )
        ]
    )

    aggregate_query_patterns(session, table_id=table_id)  # type: ignore[arg-type]

    upsert_stmt = session.executed[0]
    inserted_values = upsert_stmt.compile().params
    assert inserted_values["avg_selectivity"] == pytest.approx(0.1)


def test_aggregate_query_patterns_leaves_selectivity_none_without_file_counts() -> None:
    """No files_read/files_pruned on the log (e.g. a non-Trino engine, or an older row) must
    not fabricate a selectivity value -- None is the correct "no information" signal, not 0.0
    (which would look like perfect pruning that was never actually observed)."""
    table_id = uuid.uuid4()
    session = _PatternSession(
        [
            SimpleNamespace(
                table_id=table_id,
                executed_at=datetime(2026, 7, 31, tzinfo=UTC),
                filter_columns=[{"column": "event_date", "is_range": True}],
                join_columns=[],
                projection_columns=[],
                files_read=None,
                files_pruned=None,
            )
        ]
    )

    aggregate_query_patterns(session, table_id=table_id)  # type: ignore[arg-type]

    upsert_stmt = session.executed[0]
    inserted_values = upsert_stmt.compile().params
    assert inserted_values["avg_selectivity"] is None


class TestColumnPatternStatsSelectivity:
    """Unit coverage for the running-mean accumulator itself, independent of any aggregation
    pipeline -- both the query_logs path and the Iceberg read-through path fold samples through
    this same method."""

    def test_first_sample_sets_the_value_directly(self) -> None:
        stats = ColumnPatternStats(column_name="event_date")
        stats.record_selectivity_sample(0.2)
        assert stats.avg_selectivity == pytest.approx(0.2)

    def test_second_sample_averages_with_the_first(self) -> None:
        stats = ColumnPatternStats(column_name="event_date")
        stats.record_selectivity_sample(0.2)
        stats.record_selectivity_sample(0.6)
        assert stats.avg_selectivity == pytest.approx(0.4)

    def test_running_mean_matches_simple_average_over_several_samples(self) -> None:
        stats = ColumnPatternStats(column_name="event_date")
        samples = [0.1, 0.5, 0.9, 0.3]
        for s in samples:
            stats.record_selectivity_sample(s)
        assert stats.avg_selectivity == pytest.approx(sum(samples) / len(samples))

    def test_out_of_range_values_are_clamped(self) -> None:
        stats = ColumnPatternStats(column_name="event_date")
        stats.record_selectivity_sample(-0.5)
        assert stats.avg_selectivity == pytest.approx(0.0)
        stats2 = ColumnPatternStats(column_name="event_date")
        stats2.record_selectivity_sample(1.5)
        assert stats2.avg_selectivity == pytest.approx(1.0)
