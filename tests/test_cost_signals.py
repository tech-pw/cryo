"""Tests for cryo/reporting/cost_signals.py's DoD/WoW fleet-cost signal + RCA correlation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import AuditLog, Source, Table, TableCostHistory
from cryo.reporting.cost_signals import (
    _daily_fleet_cost,
    compute_cost_signal,
    correlate_with_maintenance,
)

NOW = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(session, source: Source, *, name: str) -> Table:
    table = Table(
        id=uuid4(), source_id=source.id, schema_name="finance", table_name=name, is_active=True
    )
    session.add(table)
    session.flush()
    return table


def _seed_cost(session, table: Table, *, scan_at: datetime, cost: float) -> None:
    session.add(
        TableCostHistory(table_id=table.id, scan_at=scan_at, current_monthly_cost_usd=cost)
    )


# ---------------------------------------------------------------------------
# _daily_fleet_cost
# ---------------------------------------------------------------------------


def test_daily_fleet_cost_none_with_no_history(session) -> None:
    assert _daily_fleet_cost(session, NOW) is None


def test_daily_fleet_cost_sums_nearest_prior_snapshot_per_table(session) -> None:
    source = _seed_source(session)
    for i in range(3):
        table = _seed_table(session, source, name=f"t{i}")
        _seed_cost(session, table, scan_at=NOW - timedelta(hours=2), cost=10.0)
        # A later row (after `as_of`) must not be picked.
        _seed_cost(session, table, scan_at=NOW + timedelta(hours=2), cost=999.0)
    session.commit()

    assert _daily_fleet_cost(session, NOW) == 30.0


# ---------------------------------------------------------------------------
# compute_cost_signal
# ---------------------------------------------------------------------------


def _seed_flat_history(session, table: Table, *, days: int, daily_cost: float) -> None:
    for d in range(days):
        _seed_cost(session, table, scan_at=NOW - timedelta(days=d, hours=1), cost=daily_cost)


def test_compute_cost_signal_stable_when_flat(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    _seed_flat_history(session, table, days=10, daily_cost=100.0)
    session.commit()

    signal = compute_cost_signal(session, now=NOW)

    assert signal.direction == "stable"
    assert signal.primary_signal is None


def test_compute_cost_signal_dod_spike(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    # yesterday (day -1) way above day -2's baseline -- default DoD spike threshold is 10%.
    _seed_cost(session, table, scan_at=NOW - timedelta(days=1, hours=1), cost=200.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=2, hours=1), cost=100.0)
    # WoW baseline (day -8) present too, so only DoD should be the driver here.
    _seed_cost(session, table, scan_at=NOW - timedelta(days=8, hours=1), cost=190.0)
    session.commit()

    signal = compute_cost_signal(session, now=NOW)

    assert signal.direction == "spike"
    assert signal.primary_signal == "dod"
    assert signal.dod_pct is not None
    assert signal.dod_pct > 0


def test_compute_cost_signal_dod_decline(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    _seed_cost(session, table, scan_at=NOW - timedelta(days=1, hours=1), cost=50.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=2, hours=1), cost=100.0)
    session.commit()

    signal = compute_cost_signal(session, now=NOW)

    assert signal.direction == "decline"
    assert signal.primary_signal == "dod"


def test_compute_cost_signal_wow_only_fires_when_dod_is_stable(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    # DoD flat (day -1 vs day -2), but well above day -8 -- WoW should catch this, DoD should not.
    _seed_cost(session, table, scan_at=NOW - timedelta(days=1, hours=1), cost=120.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=2, hours=1), cost=119.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=8, hours=1), cost=90.0)
    session.commit()

    signal = compute_cost_signal(session, now=NOW)

    assert signal.direction == "spike"
    assert signal.primary_signal == "wow"


def test_compute_cost_signal_dod_takes_priority_over_wow(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    # Both DoD and WoW would fire -- DoD must win as primary_signal.
    _seed_cost(session, table, scan_at=NOW - timedelta(days=1, hours=1), cost=200.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=2, hours=1), cost=100.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=8, hours=1), cost=100.0)
    session.commit()

    signal = compute_cost_signal(session, now=NOW)

    assert signal.primary_signal == "dod"


def test_compute_cost_signal_no_baseline_stays_stable(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    # Only "yesterday" has data -- no DoD/WoW baseline to compare against.
    _seed_cost(session, table, scan_at=NOW - timedelta(days=1, hours=1), cost=100.0)
    session.commit()

    signal = compute_cost_signal(session, now=NOW)

    assert signal.direction == "stable"
    assert signal.dod_pct is None
    assert signal.wow_pct is None


# ---------------------------------------------------------------------------
# correlate_with_maintenance
# ---------------------------------------------------------------------------


def test_correlate_returns_none_for_stable_signal(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    _seed_flat_history(session, table, days=10, daily_cost=100.0)
    session.commit()
    signal = compute_cost_signal(session, now=NOW)

    assert correlate_with_maintenance(session, signal, now=NOW) is None


def test_correlate_spike_explained_by_rewrite_activity(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    _seed_cost(session, table, scan_at=NOW - timedelta(days=1, hours=1), cost=200.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=2, hours=1), cost=100.0)
    session.add(
        AuditLog(
            ts=NOW - timedelta(hours=6),
            table_id=table.id,
            action="optimize",
            status="OK",
            actual_bytes_freed=1024**3,  # 1 GB
        )
    )
    session.commit()
    signal = compute_cost_signal(session, now=NOW)
    assert signal.direction == "spike"

    correlation = correlate_with_maintenance(session, signal, now=NOW)

    assert correlation is not None
    assert correlation.explains_change is True
    assert correlation.tables_involved == 1
    assert "rewrite" in correlation.summary


def test_correlate_spike_unexplained_when_only_cleanup_ran(session) -> None:
    """A spike explained by rewrite activity is a different finding than cleanup activity --
    cleanup only ever shrinks storage, so it can't be the cause of a spike."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    _seed_cost(session, table, scan_at=NOW - timedelta(days=1, hours=1), cost=200.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=2, hours=1), cost=100.0)
    session.add(
        AuditLog(
            ts=NOW - timedelta(hours=6),
            table_id=table.id,
            action="expire_snapshots",
            status="OK",
            actual_bytes_freed=1024**3,
        )
    )
    session.commit()
    signal = compute_cost_signal(session, now=NOW)
    assert signal.direction == "spike"

    correlation = correlate_with_maintenance(session, signal, now=NOW)

    assert correlation is not None
    assert correlation.explains_change is False
    assert "not explained" in correlation.summary


def test_correlate_decline_explained_by_cleanup_activity(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    _seed_cost(session, table, scan_at=NOW - timedelta(days=1, hours=1), cost=50.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=2, hours=1), cost=100.0)
    session.add(
        AuditLog(
            ts=NOW - timedelta(hours=6),
            table_id=table.id,
            action="remove_orphan_files",
            status="succeeded",
            actual_bytes_freed=5 * 1024**3,
        )
    )
    session.commit()
    signal = compute_cost_signal(session, now=NOW)
    assert signal.direction == "decline"

    correlation = correlate_with_maintenance(session, signal, now=NOW)

    assert correlation is not None
    assert correlation.explains_change is True
    assert correlation.bytes_freed == 5 * 1024**3
    assert "cleanup" in correlation.summary


def test_correlate_ignores_failed_maintenance_runs(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    _seed_cost(session, table, scan_at=NOW - timedelta(days=1, hours=1), cost=200.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=2, hours=1), cost=100.0)
    session.add(
        AuditLog(
            ts=NOW - timedelta(hours=6),
            table_id=table.id,
            action="optimize",
            status="FAILED",
            actual_bytes_freed=None,
        )
    )
    session.commit()
    signal = compute_cost_signal(session, now=NOW)

    correlation = correlate_with_maintenance(session, signal, now=NOW)

    assert correlation is not None
    assert correlation.explains_change is False


def test_correlate_uses_seven_day_lookback_for_wow_signal(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    # DoD flat, WoW spikes.
    _seed_cost(session, table, scan_at=NOW - timedelta(days=1, hours=1), cost=120.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=2, hours=1), cost=119.0)
    _seed_cost(session, table, scan_at=NOW - timedelta(days=8, hours=1), cost=90.0)
    # Rewrite activity 5 days ago -- inside the WoW lookback (7d), would be missed by a 1d one.
    session.add(
        AuditLog(
            ts=NOW - timedelta(days=5),
            table_id=table.id,
            action="optimize_manifests",
            status="OK",
            actual_bytes_freed=1024**2,
        )
    )
    session.commit()
    signal = compute_cost_signal(session, now=NOW)
    assert signal.primary_signal == "wow"

    correlation = correlate_with_maintenance(session, signal, now=NOW)

    assert correlation is not None
    assert correlation.explains_change is True
