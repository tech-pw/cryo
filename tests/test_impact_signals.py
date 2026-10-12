"""Tests for cryo/reporting/impact_signals.py's north-star reclaim-impact metric.

NOW is pinned to 06:00 UTC on a day so `day_bounds`' calendar-day windows land where each
test expects: "yesterday" is the full UTC day before NOW's date, DoD baseline the day before
that, WoW baseline 7 days before "yesterday".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import AuditLog, Source, Table
from cryo.reporting.impact_signals import _daily_impact, compute_impact_signal, day_bounds

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
    day_start, _ = day_bounds(NOW, days_ago)
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
# day_bounds
# ---------------------------------------------------------------------------


def test_day_bounds_is_a_full_utc_calendar_day() -> None:
    start, end = day_bounds(NOW, 1)
    assert start == datetime(2026, 8, 18, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 19, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _daily_impact
# ---------------------------------------------------------------------------


def test_daily_impact_zero_with_no_audit_rows(session) -> None:
    start, end = day_bounds(NOW, 1)
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

    start, end = day_bounds(NOW, 1)
    impact = _daily_impact(session, start, end)

    assert impact.bytes_freed == 3 * 1024**3
    assert impact.actions_succeeded == 2
    assert impact.tables_touched == 2


def test_daily_impact_excludes_failed_actions(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=1, action="optimize", status="FAILED", bytes_freed=1024**3)
    session.commit()

    start, end = day_bounds(NOW, 1)
    impact = _daily_impact(session, start, end)

    assert impact.bytes_freed == 0
    assert impact.actions_succeeded == 0


def test_daily_impact_excludes_rows_outside_the_day_window(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=1, action="optimize", bytes_freed=1024**3)
    _log(session, table, days_ago=2, action="optimize", bytes_freed=999 * 1024**3)
    session.commit()

    start, end = day_bounds(NOW, 1)
    impact = _daily_impact(session, start, end)

    assert impact.bytes_freed == 1024**3


def test_daily_impact_computes_optimize_file_reduction_ratio(session) -> None:
    source = _seed_source(session)
    t1 = _seed_table(session, source, name="t1")
    t2 = _seed_table(session, source, name="t2")
    _log(
        session, t1, days_ago=1, action="optimize", bytes_freed=1024**3, files_in=100, files_out=10
    )
    _log(session, t2, days_ago=1, action="optimize", bytes_freed=1024**3, files_in=50, files_out=5)
    # remove_orphan_files input==output (it doesn't rewrite files) -- must not dilute the ratio.
    _log(
        session,
        t1,
        days_ago=1,
        action="remove_orphan_files",
        bytes_freed=1024**2,
        files_in=5,
        files_out=5,
    )
    session.commit()

    start, end = day_bounds(NOW, 1)
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
    # Both DoD and WoW would fire -- DoD must win as primary_signal, same precedence as
    # cost_signals.
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


def test_daily_impact_distinguishes_unmeasured_bytes_from_measured_zero(session) -> None:
    """Recovery plan §6, 2026-09-20: live-confirmed the real bug -- 2,013 succeeded
    remove_orphan_files actions on 2026-09-19, every single one with actual_bytes_freed IS
    NULL (the Trino engine never sets it), summed via coalesce-to-0 into a report reading
    'zero GB freed' as if it were a genuine measurement of zero impact."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=1, action="remove_orphan_files", bytes_freed=None)
    _log(session, table, days_ago=1, action="remove_orphan_files", bytes_freed=None)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.today.actions_succeeded == 2
    assert signal.today.bytes_freed == 0
    assert signal.today.actions_with_bytes_evidence == 0
    assert signal.today.actions_without_bytes_evidence == 2
    assert signal.today.bytes_freed_is_known is False
    assert signal.direction == "unknown"
    assert signal.dod_pct is None
    assert signal.wow_pct is None


def test_daily_impact_zero_successful_actions_is_still_a_real_measured_zero(session) -> None:
    """The other half of the same fix: a day with genuinely NO successful reclaim actions is a
    real, legitimate zero (not a missing-evidence gap) -- it must still be able to read as
    'worsening' against an active baseline, not get relabeled 'unknown' just because
    actions_with_bytes_evidence is also 0 in this case."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=2, action="optimize", bytes_freed=10 * 1024**3)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.today.actions_succeeded == 0
    assert signal.today.bytes_freed_is_known is True
    assert signal.direction == "worsening"


def test_daily_impact_mixed_evidence_day_has_real_bytes_but_untrustworthy_percentage(
    session,
) -> None:
    """Review finding F10 (follow-up review, 2026-09-20): a day where SOME but not all actions
    reported bytes has real evidence (bytes_freed_is_known True, a genuine measured subtotal --
    not the fully-blind 'unmeasured' case), but that subtotal is NOT a trustworthy percentage
    numerator: an unknown number of additional bytes went unmeasured, so a DoD/WoW comparison
    against it (or against another day with different coverage) could look like a real swing
    that's actually just a change in measurement coverage. direction MUST be 'unknown' here --
    the earlier assumption that 'partial evidence is still evidence, not unknown' conflated
    'has a real subtotal' with 'total is comparable,' which is exactly the bug this fixes."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=1, action="remove_orphan_files", bytes_freed=5 * 1024**3)
    _log(session, table, days_ago=1, action="remove_orphan_files", bytes_freed=None)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.today.bytes_freed_is_known is True
    assert signal.today.actions_with_bytes_evidence == 1
    assert signal.today.actions_without_bytes_evidence == 1
    assert signal.today.measurement_state == "partial"
    assert signal.today.is_comparable_total is False
    assert signal.direction == "unknown"
    assert signal.dod_pct is None
    assert signal.wow_pct is None


def test_complete_today_against_partial_baseline_does_not_manufacture_a_percentage(session) -> None:
    """Direct regression for the follow-up review's exact complaint: even when TODAY is fully
    'complete' (every action measured), a baseline day with only PARTIAL coverage must not be
    used as a percentage denominator -- comparing a real total against an unknown-sized
    fragment of another day's total produces a manufactured swing that reflects a difference in
    measurement coverage, not real reclaim volume. Before this fix, dod_baseline.gb_freed was
    used whenever bytes_freed_is_known was True, which included 'partial' -- exactly the case
    this test targets."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    # Today (days_ago=1): fully complete, 1 GB, the true total.
    _log(session, table, days_ago=1, action="remove_orphan_files", bytes_freed=1 * 1024**3)
    # DoD baseline (days_ago=2): partial -- 100 GB measured, but 9 more actions unmeasured, so
    # the real total that day could be far larger than 100 GB.
    _log(session, table, days_ago=2, action="remove_orphan_files", bytes_freed=100 * 1024**3)
    for _ in range(9):
        _log(session, table, days_ago=2, action="remove_orphan_files", bytes_freed=None)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.today.measurement_state == "complete"
    assert signal.dod_pct is None, (
        "a partial-coverage baseline must never be used as a percentage denominator, even "
        "when today's own measurement is complete"
    )
    assert signal.dod_baseline_gb is None, (
        "the baseline number itself must be nulled, not just the trend -- a consumer reading "
        "only dod_baseline_gb (not measurement_state) must never see an uncaveated partial "
        "subtotal presented as if it were the real baseline total"
    )


def test_measurement_state_is_complete_when_every_action_reports_bytes(session) -> None:
    """Review finding F10: a day where 100% of succeeded actions reported bytes must be
    'complete', distinguishable from 'partial' -- gb_freed can be trusted as the real total."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=1, action="remove_orphan_files", bytes_freed=1 * 1024**3)
    _log(session, table, days_ago=1, action="optimize", bytes_freed=2 * 1024**3)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.today.measurement_state == "complete"
    assert signal.measurement_state == "complete"


def test_measurement_state_is_partial_not_complete_when_only_some_actions_report_bytes(
    session,
) -> None:
    """Review finding F10's core fix: the old boolean (bytes_freed_is_known) treated 1-of-1000
    measured actions the same as 1000-of-1000 -- both read as fully 'known'. measurement_state
    must distinguish 'partial' (gb_freed is a real but INCOMPLETE subtotal) from 'complete'."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=1, action="remove_orphan_files", bytes_freed=1 * 1024**3)
    for _ in range(9):
        _log(session, table, days_ago=1, action="remove_orphan_files", bytes_freed=None)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.today.actions_succeeded == 10
    assert signal.today.actions_with_bytes_evidence == 1
    assert signal.today.measurement_state == "partial"
    assert signal.today.measurement_state != "complete", (
        "1 measured out of 10 successful actions must NOT read as 'complete' -- gb_freed here "
        "is a real but partial subtotal, not the true day total"
    )


def test_measurement_state_is_unmeasured_when_actions_succeeded_but_none_report_bytes(
    session,
) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=1, action="remove_orphan_files", bytes_freed=None)
    _log(session, table, days_ago=1, action="remove_orphan_files", bytes_freed=None)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.today.measurement_state == "unmeasured"
    assert signal.measurement_state == "unmeasured"
    assert signal.direction == "unknown"


def test_measurement_state_is_no_actions_when_nothing_succeeded(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="t1")
    _log(session, table, days_ago=2, action="optimize", bytes_freed=10 * 1024**3)
    session.commit()

    signal = compute_impact_signal(session, now=NOW)

    assert signal.today.actions_succeeded == 0
    assert signal.today.measurement_state == "no_actions"
    assert signal.measurement_state == "no_actions"
    # A real, legitimate zero -- must still be able to read as worsening against an active
    # baseline (see test_daily_impact_zero_successful_actions_is_still_a_real_measured_zero).
    assert signal.direction == "worsening"
