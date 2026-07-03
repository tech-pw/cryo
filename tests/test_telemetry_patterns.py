from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from cryo.telemetry.patterns import aggregate_query_patterns


class _ScalarResult:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _PatternSession:
    def __init__(self, logs: list[Any]):
        self._logs = logs
        self.executed: list[Any] = []
        self.committed = False

    def scalars(self, statement: Any) -> _ScalarResult:
        return _ScalarResult(self._logs)

    def execute(self, statement: Any) -> None:
        self.executed.append(statement)

    def commit(self) -> None:
        self.committed = True


def test_aggregate_query_patterns_inserts_column_stats() -> None:
    table_id = uuid.uuid4()
    session = _PatternSession(
        [
            SimpleNamespace(
                table_id=table_id,
                filter_columns=[{"column": "event_date", "is_range": True}],
                join_columns=[{"column": "customer_id"}],
                projection_columns=[{"column": "amount"}],
            )
        ]
    )

    updated = aggregate_query_patterns(session, table_id=table_id)  # type: ignore[arg-type]

    assert updated == 3
    assert len(session.executed) == 3
    assert session.committed is True
