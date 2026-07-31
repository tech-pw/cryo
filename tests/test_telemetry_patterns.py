from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from cryo.telemetry.patterns import aggregate_query_patterns


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
