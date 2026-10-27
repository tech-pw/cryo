"""#259: fleet-wide anomalies and signals use set-based queries and a short-lived cache."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event

from cryo.api import advanced_routes
from cryo.db.models import QueryLog, Source, Table, TableStats, TableStatsHistory
from cryo.ml.anomaly_detector import detect_all_anomalies, detect_table_anomalies
from cryo.signals import (
    detect_all_signals,
    detect_cost_spike_signal,
    detect_file_degradation_signal,
    detect_latency_spike_signal,
)


def _table(session, name: str) -> Table:
    source = Source(id=uuid4(), name=f"src-{uuid4()}", type="rest", config={"catalog_name": "lake"})
    session.add(source)
    session.flush()
    table = Table(id=uuid4(), source_id=source.id, schema_name="s", table_name=name, is_active=True)
    session.add(table)
    session.flush()
    return table


def _count_queries(session):
    counter = {"n": 0}

    def _before(*_args, **_kwargs):
        counter["n"] += 1

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", _before)
    return counter, lambda: event.remove(engine, "before_cursor_execute", _before)


def _seed_fleet(session, now: datetime) -> list[Table]:
    tables = []
    for i in range(12):
        table = _table(session, f"t{i}")
        tables.append(table)
        spike = i % 3 == 0
        session.add(
            TableStats(
                table_id=table.id,
                scan_at=now,
                needs_compaction=spike,
                compaction_priority="HIGH",
                grade="C",
                overall_score=60,
                total_files=1000,
                small_files_count=900 if spike else 100,
                total_size_gb=80.0 if i % 4 == 0 else 50.0,
            )
        )
        for days_ago in range(1, 29):
            session.add(
                TableStatsHistory(
                    table_id=table.id,
                    scan_at=now - timedelta(days=days_ago),
                    small_files_count=100,
                    total_size_gb=(80.0 if i % 4 == 0 and days_ago < 7 else 50.0),
                    overall_score=80,
                )
            )
        if i == 1:
            # Busy table whose recent queries are 3x slower than its baseline.
            for k in range(12):
                for executed_at, duration in (
                    (now - timedelta(days=3, minutes=k), 100),
                    (now - timedelta(hours=2, minutes=k), 300),
                ):
                    session.add(
                        QueryLog(
                            source_id=table.source_id,
                            table_id=table.id,
                            query_id=str(uuid.uuid4()),
                            engine="trino",
                            query_type="SELECT",
                            executed_at=executed_at,
                            duration_ms=duration,
                        )
                    )
    session.commit()
    return tables


def _key(signal):
    return (
        signal.table_id,
        signal.signal_type,
        signal.severity,
        signal.current_value,
        signal.baseline_value,
    )


def test_fleet_signals_match_the_per_table_detectors(session):
    now = datetime.now(UTC)
    tables = _seed_fleet(session, now)

    expected = []
    for table in tables:
        for detect in (
            detect_file_degradation_signal,
            detect_latency_spike_signal,
            detect_cost_spike_signal,
        ):
            if signal := detect(session, table.id):
                expected.append(signal)

    fleet = detect_all_signals(session)

    assert {_key(s) for s in fleet} == {_key(s) for s in expected}
    assert {s.signal_type for s in fleet} == {"file_degradation", "latency_spike", "cost_spike"}
    by_type = {s.signal_type: s for s in fleet}
    assert by_type["latency_spike"].table_name == "lake.s.t1"
    assert all(s.table_name.startswith("lake.s.") for s in fleet)


def test_fleet_signals_query_count_does_not_grow_with_the_fleet(session):
    _seed_fleet(session, datetime.now(UTC))
    counter, stop = _count_queries(session)
    try:
        detect_all_signals(session)
    finally:
        stop()
    # 12 tables; the old loop issued 5+ queries per table (60+).
    assert counter["n"] <= 12


def test_fleet_anomalies_match_per_table_and_batch_the_history(session):
    now = datetime.now(UTC)
    tables = []
    for i in range(8):
        table = _table(session, f"a{i}")
        tables.append(table)
        values = [50, 52, 48, 51, 49, 50, 53, 2000 if i % 2 == 0 else 50]
        for offset, small in enumerate(values):
            session.add(
                TableStatsHistory(
                    table_id=table.id,
                    scan_at=now - timedelta(hours=len(values) - offset),
                    small_files_count=small,
                    total_size_gb=10.0,
                    overall_score=80,
                )
            )
    session.commit()

    expected = {
        (a.table_id, a.metric_name, a.severity, a.current_value)
        for table in tables
        for a in detect_table_anomalies(session, table.id, lookback_days=30)
    }
    counter, stop = _count_queries(session)
    try:
        fleet = detect_all_anomalies(session, min_severity="low", lookback_days=30)
    finally:
        stop()

    assert {(a.table_id, a.metric_name, a.severity, a.current_value) for a in fleet} == expected
    assert len({a.table_id for a in fleet}) == 4
    assert counter["n"] <= 6


@pytest.fixture(autouse=True)
def _clear_cache():
    advanced_routes.clear_fleet_insights_cache()
    yield
    advanced_routes.clear_fleet_insights_cache()


def test_fleet_cache_computes_once_per_ttl(monkeypatch):
    calls = []

    def compute():
        calls.append(1)
        return [len(calls)]

    first, at1 = advanced_routes._cached_fleet_result(("signals",), compute)
    second, at2 = advanced_routes._cached_fleet_result(("signals",), compute)
    assert first == second == [1]
    assert at1 == at2
    assert len(calls) == 1

    other, _ = advanced_routes._cached_fleet_result(("anomalies", 30, 3.0), compute)
    assert other == [2]

    monkeypatch.setattr(advanced_routes, "FLEET_INSIGHTS_TTL_SECONDS", 0.0)
    third, _ = advanced_routes._cached_fleet_result(("signals",), compute)
    assert third == [3]


def test_history_straddling_midnight_still_scores_the_latest_scan():
    """Regression: 3-4 same-weekday points used to replace the whole baseline, and the detector
    then refused to score fewer than 5, so nothing was flagged. Hit daily between ~04:00 and
    ~06:00 UTC by the hourly fixture above."""
    from types import SimpleNamespace
    from uuid import uuid4

    from cryo.ml.anomaly_detector import _anomalies_from_history

    midnight = datetime(2026, 10, 27, tzinfo=UTC)
    values = [50, 52, 48, 51, 49, 50, 53, 2000]
    # Three points on Monday evening, the latest five (including the spike) on Tuesday.
    history = [
        SimpleNamespace(
            scan_at=midnight + timedelta(hours=offset - 3),
            small_files_count=small,
            total_size_gb=10.0,
            overall_score=80,
            p95_query_latency_ms=None,
        )
        for offset, small in enumerate(values)
    ]
    same_day = [h for h in history[:-1] if h.scan_at.weekday() == history[-1].scan_at.weekday()]
    assert len(same_day) == 4

    anomalies = _anomalies_from_history(uuid4(), "t", history, threshold_sigma=3.0)
    assert any(a.metric_name == "small_files_count" for a in anomalies)
