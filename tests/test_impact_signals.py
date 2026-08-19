"""Tests for cryo/reporting/impact_signals.py's north-star reclaim-impact metric.

NOW is pinned to 06:00 UTC on a day so `_day_bounds`' calendar-day windows land where each
test expects: "yesterday" is the full UTC day before NOW's date, DoD baseline the day before
that, WoW baseline 7 days before "yesterday".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import AuditLog, Source, Table
from cryo.reporting.impact_signals import _daily_impact, _day_bounds, compute_impact_signal

NOW = datetime(2026, 8, 19, 6, 0, tzinfo=UTC)


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


def _log(
    session,
    table: Table,
    *,
    days_ago: int,
    action: str = "remove_orphan_files",
    status: str = "OK",
    bytes_freed: int | None = None,
    files_in: int | None = None,
    files_out: int | None = None,
) -> None:
    """days_ago=1 lands inside 'yesterday' (the target day this module treats as current)."""
    day_start, _ = _day_bounds(NOW, days_ago)
    session.add(
        AuditLog(
            ts=day_start + timedelta(hours=6),
            table_id=table.id,
            action=action,
            status=status,
            actual_bytes_freed=bytes_freed,
            input_files_count=files_in,
            output_files_count=files_out,
        )
    )


# ---------------------------------------------------------------------------
# _day_bounds
# ---------------------------------------------------------------------------


def test_day_bounds_is_a_full_utc_calendar_day() -> None:
    start, end = _day_bounds(NOW, 1)
    assert start == datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 19, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _daily_impact
# ---------------------------------------------------------------------------


def test_daily_impact_zero_with_no_audit_rows(session) -> None:
    start, end = _day_bounds(NOW, 1)
    impact = _daily_impact(session, start, end)
    assert impact.bytes_freed == 0
    assert impact.actions_succeeded == 0
    assert impact.tables_touched == 0
    assert impact.gb_freed == 0.0
    assert impact.optimize_file_reduction_pct is None


def test_daily_impact_sums_reclaim_actions_only(session) -> None:
    source = _seed_source(session)
    t1 = _seed_table(session, source, name="t1")
    t2 = _seed_table(session, source, name="t2")
    _log(session, t1, days_ago=1, action="remove_orphan_files", bytes_freed=1024**3)
    _log(session, t2, days_ago=1, action="optimize", bytes_freed=2 * 1024**3)
    # expire_snapshots frees storage indirectly (via a later remove_orphan_files) -- counting
    # its own actual_bytes_freed here would double-count against the remove_orphan_files row.
    _log(session, t1, days_ago=1, action="expire_snapshots", bytes_freed=999 * 1024**3)
    # apply_table_properties has no bytes-freed semantics; a stray non-null value must not leak in.
    _log(session, t1, days_ago=1, action="apply_table_properties", bytes_freed=1)
    session.commit()

    start, end = _day_bounds(NOW, 1)
    impact = _daily_impact(session, start, end)

    assert impact.bytes_freed == 3 * 1024**3
    assert impact.actions_succeeded == 2
    assert impact.tables_touched == 2


def test_daily_impact_excludes_failed_actions(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=1, action="optimize", status="FAILED", bytes_freed=1024**3)
    session.commit()

    start, end = _day_bounds(NOW, 1)
    impact = _daily_impact(session, start, end)

    assert impact.bytes_freed == 0
    assert impact.actions_succeeded == 0


def test_daily_impact_excludes_rows_outside_the_day_window(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=1, action="optimize", bytes_freed=1024**3)
    _log(session, table, days_ago=2, action="optimize", bytes_freed=999 * 1024**3)
    session.commit()

    start, end = _day_bounds(NOW, 1)
    impact = _daily_impact(session, start, end)

    assert impact.bytes_freed == 1024**3


def test_daily_impact_computes_optimize_file_reduction_ratio(session) -> None:
    source = _seed_source(session)
    t1 = _seed_table(session, source, name="t1")
    t2 = _seed_table(session, source, name="t2")
    _log(session, t1, days_ago=1, action="optimize", bytes_freed=1024**3, files_in=100, files_out=10)
    _log(session, t2, days_ago=1, action="optimize", bytes_freed=1024**3, files_in=50, files_out=5)
    # remove_orphan_files input==output (it doesn't rewrite files) -- must not dilute the ratio.
    _log(session, t1, days_ago=1, action="remove_orphan_files", bytes_freed=1024**2, files_in=5, files_out=5)
    session.commit()

    start, end = _day_bounds(NOW, 1)
    impact = _daily_impact(session, start, end)

    assert impact.optimize_files_in == 150
    assert impact.optimize_files_out == 15
    assert impact.optimize_file_reduction_pct == 90.0


# ---------------------------------------------------------------------------
# compute_impact_signal
# ---------------------------------------------------------------------------


def test_compute_impact_signal_flat_when_no_baseline(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=1, action="optimize", bytes_freed=1024**3)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.direction == "flat"
    assert signal.dod_pct is None
    assert signal.wow_pct is None
    assert signal.today.gb_freed > 0


def test_compute_impact_signal_improving_on_dod_increase(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    # Yesterday freed far more than the DoD baseline -- default move threshold is 25%.
    _log(session, table, days_ago=1, action="optimize", bytes_freed=10 * 1024**3)
    _log(session, table, days_ago=2, action="optimize", bytes_freed=1 * 1024**3)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.direction == "improving"
    assert signal.primary_signal == "dod"
    assert signal.dod_pct is not None
    assert signal.dod_pct > 0


def test_compute_impact_signal_worsening_when_reclaim_drops(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=1, action="optimize", bytes_freed=1 * 1024**3)
    _log(session, table, days_ago=2, action="optimize", bytes_freed=10 * 1024**3)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.direction == "worsening"
    assert signal.primary_signal == "dod"
    assert signal.dod_pct is not None
    assert signal.dod_pct < 0


def test_compute_impact_signal_worsening_when_reclaim_drops_to_zero(session) -> None:
    """A real regression -- yesterday's baseline had activity, today has none -- must not be
    silently read as 'stable'/'flat' just because 0 is a valid, non-fabricated number."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=2, action="optimize", bytes_freed=10 * 1024**3)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.today.gb_freed == 0.0
    assert signal.direction == "worsening"
    assert signal.dod_pct == -100.0


def test_compute_impact_signal_wow_only_fires_when_dod_is_flat(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    # DoD flat (small move under threshold), WoW well above the 20% default move threshold.
    _log(session, table, days_ago=1, action="optimize", bytes_freed=105 * 1024**2)
    _log(session, table, days_ago=2, action="optimize", bytes_freed=100 * 1024**2)
    _log(session, table, days_ago=8, action="optimize", bytes_freed=50 * 1024**2)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.direction == "improving"
    assert signal.primary_signal == "wow"


def test_compute_impact_signal_dod_takes_priority_over_wow(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    # Both DoD and WoW would fire -- DoD must win as primary_signal, same precedence as cost_signals.
    _log(session, table, days_ago=1, action="optimize", bytes_freed=10 * 1024**3)
    _log(session, table, days_ago=2, action="optimize", bytes_freed=1 * 1024**3)
    _log(session, table, days_ago=8, action="optimize", bytes_freed=1 * 1024**3)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.primary_signal == "dod"


def test_compute_impact_signal_flat_within_threshold_band(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    # 5% DoD move -- comfortably under the default 25% threshold.
    _log(session, table, days_ago=1, action="optimize", bytes_freed=105 * 1024**2)
    _log(session, table, days_ago=2, action="optimize", bytes_freed=100 * 1024**2)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.direction == "flat"
