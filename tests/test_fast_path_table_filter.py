"""A table-detail fast-path read must not sweep the fleet."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.scanner.fast_path_check import FastPathFinding, FastPathTableNotFound, load_fast_path_findings


class _Session:
    def __init__(self, table: object | None) -> None:
        self._table = table

    def get(self, _model: object, ident: object) -> object | None:
        if self._table is None:
            return None
        if getattr(self._table, "id", None) == ident:
            return self._table
        return None


def test_table_id_evaluates_one_table_and_does_not_sweep(monkeypatch) -> None:
    table_id = uuid.uuid4()
    table = type("Table", (), {"id": table_id, "schema_name": "sales", "table_name": "orders"})()
    calls = {"sweep": 0, "one": 0}

    def sweep(*_args, **_kwargs):
        calls["sweep"] += 1
        return []

    def one(_session, found, _now):
        calls["one"] += 1
        assert found.id == table_id
        return [
            FastPathFinding(
                table_id=table_id,
                table_name="sales.orders",
                finding_type="small_files",
                reason="too many small files",
                severity="warning",
                metric_value=12.0,
                detected_at=datetime.now(UTC),
            )
        ]

    monkeypatch.setattr("cryo.scanner.fast_path_check.run_fast_path_check", sweep)
    monkeypatch.setattr("cryo.scanner.fast_path_check._evaluate_table", one)
    findings = load_fast_path_findings(_Session(table), table_id=table_id)
    assert calls == {"sweep": 0, "one": 1}
    assert findings[0].table_id == table_id


def test_missing_table_is_not_a_fleet_sweep(monkeypatch) -> None:
    def sweep(*_args, **_kwargs):
        raise AssertionError("fleet sweep")

    monkeypatch.setattr("cryo.scanner.fast_path_check.run_fast_path_check", sweep)
    try:
        load_fast_path_findings(_Session(None), table_id=uuid.uuid4())
    except FastPathTableNotFound:
        return
    raise AssertionError("expected FastPathTableNotFound")
