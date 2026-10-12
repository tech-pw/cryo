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
            "ALTER TABLE sample.reports.t EXECUTE optimize(file_size_threshold => '256MB')",
        )
    ]
    state = find_maintenance_query_state(
        [coord],
        "sample.reports.t",
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
            "ALTER TABLE sample.reports.t EXECUTE optimize(file_size_threshold => '256MB')",
        )
    ]
    state = find_maintenance_query_state(
        [coord],
        "sample.reports.missing",
        OperationType.OPTIMIZE,
        connector=lambda _c: _Conn(rows),
    )
    assert state is EngineOpState.UNCERTAIN


class _RaisingConn:
    """Fake connector whose cursor().execute() raises -- same connectivity-failure shape as a
    real unreachable/timed-out Trino coordinator."""

    def cursor(self):
        return self

    def execute(self, *_a, **_k) -> None:
        raise RuntimeError("connection reset by peer")

    def fetchone(self):
        raise AssertionError("must not be called after execute raises")

    def fetchall(self):
        raise AssertionError("must not be called after execute raises")

    def close(self) -> None:
        return None


class TestQueryStatusProbeFailureVisibility:
    """Root-cause sweep, 2026-09-16: get_query_state/_probe_one used to log connectivity
    failures at logger.debug (invisible in prod -- configure_logging sets INFO there), the same
    silent-failure pattern that caused the native watcher incident. The return value here was
    already safe (UNCERTAIN, which orphan_recovery.py's quiet-window gates correctly hold on),
    so this fix is purely about visibility: a sustained run of Trino-connectivity failures must
    be loggable at warning and countable via a metric, not silently indistinguishable from a
    healthy probe that legitimately found nothing."""

    def test_get_query_state_failure_logs_at_warning_not_debug(self) -> None:
        from unittest.mock import patch

        coord = TrinoCoordinator(host="h")
        with patch("cryo.engines.trino.query_status.logger.warning") as mock_warning:
            state = get_query_state(coord, "20260916_1_abc", connector=lambda _c: _RaisingConn())
        assert state is EngineOpState.UNCERTAIN
        assert mock_warning.called

    def test_get_query_state_failure_increments_the_metric(self) -> None:
        from cryo.observability.metrics import trino_query_status_probe_failures_total

        def _sample() -> float:
            try:
                return trino_query_status_probe_failures_total.labels(
                    probe="get_query_state"
                )._value.get()
            except Exception:
                return 0.0

        before = _sample()
        coord = TrinoCoordinator(host="h")
        get_query_state(coord, "20260916_1_abc", connector=lambda _c: _RaisingConn())
        assert _sample() == before + 1.0

    def test_probe_one_failure_logs_at_warning_and_still_returns_uncertain(self) -> None:
        from unittest.mock import patch

        coord = TrinoCoordinator(host="h")
        with patch("cryo.engines.trino.query_status.logger.warning") as mock_warning:
            state = find_maintenance_query_state(
                [coord],
                "sample.reports.t",
                OperationType.OPTIMIZE,
                connector=lambda _c: _RaisingConn(),
            )
        assert state is EngineOpState.UNCERTAIN
        assert mock_warning.called
