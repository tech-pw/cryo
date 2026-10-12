"""Tests for cryo.reporting.trino_hot_cost_signal.compute_trino_hot_cost_signal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import (
    Destination,
    Preset,
    QueryCostAttributionDaily,
    QueryLog,
    Source,
    Table,
    TableAssignment,
)
from cryo.reporting.trino_hot_cost_signal import compute_trino_hot_cost_signal

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(session, source: Source, *, name: str) -> Table:
    table = Table(id=uuid4(), source_id=source.id, schema_name="s", table_name=name, is_active=True)
    session.add(table)
    session.flush()
    return table


def _seed_preset(session, *, name: str = "daily-trino-hot-tables") -> Preset:
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="spark_emr", config={})
    session.add(destination)
    session.flush()
    preset = Preset(
        id=uuid4(),
        name=name,
        destination_id=destination.id,
        mode="standard",
        operations=["optimize"],
        params={},
        guardrails={},
        enabled=True,
    )
    session.add(preset)
    session.flush()
    return preset


def _pin(session, preset: Preset, table: Table) -> None:
    session.add(
        TableAssignment(table_id=table.id, preset_id=preset.id, assigned_by="test", pinned=True)
    )


def _log_query(session, *, source: Source, table: Table, when: datetime, bytes_read: int) -> None:
    session.add(
        QueryLog(
            source_id=source.id,
            table_id=table.id,
            query_id=f"q-{uuid4()}",
            engine="trino",
            executed_at=when,
            bytes_read=bytes_read,
        )
    )


def test_returns_none_when_preset_does_not_exist(session):
    signal = compute_trino_hot_cost_signal(session, preset_name="nonexistent", now=NOW)
    assert signal is None


def test_returns_none_when_cohort_is_empty(session):
    _seed_preset(session)
    session.commit()

    signal = compute_trino_hot_cost_signal(session, preset_name="daily-trino-hot-tables", now=NOW)
    assert signal is None


def test_unknown_when_no_trino_traffic_for_cohort_yesterday(session):
    source = _seed_source(session)
    preset = _seed_preset(session)
    table = _seed_table(session, source, name="t")
    _pin(session, preset, table)
    session.commit()

    signal = compute_trino_hot_cost_signal(session, preset_name=preset.name, now=NOW)

    assert signal is not None
    assert signal.trend == "unknown"
    assert signal.today.trino_bytes_scanned == 0


def test_improving_when_bytes_scanned_drop_significantly_dod(session):
    source = _seed_source(session)
    preset = _seed_preset(session)
    table = _seed_table(session, source, name="t")
    _pin(session, preset, table)

    # DoD baseline (2 days ago): 100 GiB scanned.
    _log_query(
        session, source=source, table=table, when=NOW - timedelta(days=2), bytes_read=100 * 1024**3
    )
    # Yesterday: 20 GiB -- an 80% drop, well past the default 15% DoD threshold.
    _log_query(
        session, source=source, table=table, when=NOW - timedelta(days=1), bytes_read=20 * 1024**3
    )
    session.commit()

    signal = compute_trino_hot_cost_signal(session, preset_name=preset.name, now=NOW)

    assert signal.trend == "improving"
    assert signal.dod_bytes_pct == -80.0


def test_worsening_when_bytes_scanned_rise_significantly_dod(session):
    source = _seed_source(session)
    preset = _seed_preset(session)
    table = _seed_table(session, source, name="t")
    _pin(session, preset, table)

    _log_query(
        session, source=source, table=table, when=NOW - timedelta(days=2), bytes_read=10 * 1024**3
    )
    _log_query(
        session, source=source, table=table, when=NOW - timedelta(days=1), bytes_read=50 * 1024**3
    )
    session.commit()

    signal = compute_trino_hot_cost_signal(session, preset_name=preset.name, now=NOW)

    assert signal.trend == "worsening"
    assert signal.dod_bytes_pct == 400.0


def test_flat_within_threshold_band(session):
    source = _seed_source(session)
    preset = _seed_preset(session)
    table = _seed_table(session, source, name="t")
    _pin(session, preset, table)

    _log_query(
        session, source=source, table=table, when=NOW - timedelta(days=2), bytes_read=100 * 1024**3
    )
    _log_query(
        session, source=source, table=table, when=NOW - timedelta(days=1), bytes_read=105 * 1024**3
    )
    session.commit()

    signal = compute_trino_hot_cost_signal(session, preset_name=preset.name, now=NOW)

    assert signal.trend == "flat"


def test_cost_usd_is_none_when_pricing_is_unavailable(session):
    source = _seed_source(session)
    preset = _seed_preset(session)
    table = _seed_table(session, source, name="t")
    _pin(session, preset, table)
    _log_query(
        session, source=source, table=table, when=NOW - timedelta(days=1), bytes_read=10 * 1024**3
    )
    session.commit()

    signal = compute_trino_hot_cost_signal(session, preset_name=preset.name, now=NOW)

    assert signal.today.estimated_scan_cost_usd is None


def test_cost_usd_is_populated_when_pricing_is_available(session):
    source = _seed_source(session)
    preset = _seed_preset(session)
    table = _seed_table(session, source, name="t")
    _pin(session, preset, table)
    yesterday_start, _ = (
        datetime(NOW.year, NOW.month, NOW.day, tzinfo=UTC) - timedelta(days=1),
        None,
    )
    _log_query(
        session, source=source, table=table, when=NOW - timedelta(days=1), bytes_read=10 * 1024**3
    )
    # $2 for 10 GiB priced that day -> $0.20/GiB; cohort scanned 10 GiB -> $2.00 estimated cost.
    session.add(
        QueryCostAttributionDaily(
            source_id=source.id,
            day=yesterday_start.date(),
            client="any",
            schema_name="s",
            query_count=1,
            physical_bytes_scanned=10 * 1024**3,
            estimated_scan_cost_usd=2.0,
        )
    )
    session.commit()

    signal = compute_trino_hot_cost_signal(session, preset_name=preset.name, now=NOW)

    assert signal.today.estimated_scan_cost_usd == 2.0


def test_only_counts_pinned_tables_not_the_whole_fleet(session):
    """A table NOT pinned to this preset must not contribute to the cohort's scan totals, even
    if it's queried heavily on Trino -- the signal is specifically about this preset's cohort."""
    source = _seed_source(session)
    preset = _seed_preset(session)
    pinned = _seed_table(session, source, name="pinned")
    not_pinned = _seed_table(session, source, name="not_pinned")
    _pin(session, preset, pinned)

    _log_query(
        session, source=source, table=pinned, when=NOW - timedelta(days=1), bytes_read=1 * 1024**3
    )
    _log_query(
        session,
        source=source,
        table=not_pinned,
        when=NOW - timedelta(days=1),
        bytes_read=999 * 1024**3,
    )
    session.commit()

    signal = compute_trino_hot_cost_signal(session, preset_name=preset.name, now=NOW)

    assert signal.today.trino_bytes_scanned == 1 * 1024**3
