"""Probe Trino for maintenance query state (unit, fake connectors)."""

from __future__ import annotations

from cryo.domain.enums import OperationType
from cryo.engines.trino.client import TrinoCoordinator
from cryo.engines.trino.query_status import (
    EngineOpState,
    find_maintenance_query_state,
    get_query_state,
)


class _Cursor:
    def __init__(self, rows: list) -> None:
        self._rows = rows
        self._one = rows[0] if rows else None

    def execute(self, *_a, **_k) -> None:
        return None

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._rows)

    def close(self) -> None:
        return None


class _Conn:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def cursor(self) -> _Cursor:
        return _Cursor(self._rows)

    def close(self) -> None:
        return None


def test_get_query_state_running() -> None:
    coord = TrinoCoordinator(host="h")
    state = get_query_state(
        coord,
        "20260727_1_abc",
        connector=lambda _c: _Conn([("RUNNING",)]),
    )
    assert state is EngineOpState.RUNNING


def test_find_maintenance_by_table_and_action() -> None:
    coord = TrinoCoordinator(host="h")
    rows = [
        (
            "q1",
            "RUNNING",
            "ALTER TABLE cdp.reports.t EXECUTE optimize(file_size_threshold => '256MB')",
        )
    ]
    state = find_maintenance_query_state(
        [coord],
        "cdp.reports.t",
        OperationType.OPTIMIZE,
        connector=lambda _c: _Conn(rows),
    )
    assert state is EngineOpState.RUNNING


def test_no_match_is_uncertain() -> None:
    coord = TrinoCoordinator(host="h")
    rows = [
        (
            "q1",
            "RUNNING",
            "ALTER TABLE cdp.reports.t EXECUTE optimize(file_size_threshold => '256MB')",
        )
    ]
    state = find_maintenance_query_state(
        [coord],
        "cdp.reports.missing",
        OperationType.OPTIMIZE,
        connector=lambda _c: _Conn(rows),
    )
    assert state is EngineOpState.UNCERTAIN
