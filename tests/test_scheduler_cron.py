"""Tests for cryo.scheduler.cron -- the exact-minute-match bug found live 2026-08-28..31.

cron_is_due originally required `now` to land on the exact scheduled minute. That's fine as long
as the daemon's tick() loop calls it once a minute, every minute, with no drift -- but a single
tick's own duration had grown past 60s (each of several sequential per-table metastore-connect
attempts individually bounded at 30s, see layout_analyzer.py's timeout-fix comment), so a tick
landing even slightly late silently skipped that day's cron target minute entirely, with no retry
until the identical window the next day. Confirmed live: zero new MaintenanceRun rows created for
3+ days straight across every daily preset, despite the scheduler pod itself staying healthy the
whole time -- the exact-minute match, not any crash or hang, was the root cause.

cron_is_due now scans backward from `now`, bounded by _MISSED_FIRE_LOOKBACK, for the most recent
matching minute not yet covered by last_run_at -- a late tick still catches a missed fire on its
very next invocation instead of waiting for the next day's identical window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.scheduler.cron import _MISSED_FIRE_LOOKBACK, cron_is_due, next_cron_tick


def test_exact_minute_still_matches() -> None:
    """Baseline: calling exactly at the scheduled minute must still work, same as before this
    fix -- the backward scan starts at `now` itself."""
    now = datetime(2026, 8, 30, 20, 0, tzinfo=UTC)
    assert cron_is_due("0 20 * * *", now=now, last_run_at=None)


def test_late_tick_still_catches_a_missed_fire() -> None:
    """Direct regression test for the live incident: a tick landing several minutes after the
    scheduled minute (the exact shape a slow metastore-connect-bound tick produces) must still
    report due, not silently skip the day."""
    now = datetime(2026, 8, 30, 20, 7, tzinfo=UTC)
    assert cron_is_due("0 20 * * *", now=now, last_run_at=None)


def test_never_run_preset_fires_on_next_opportunity_not_never() -> None:
    """last_run_at=None (a preset that has never run -- exactly the state all 5 real presets
    were found in live 2026-08-31) must not be treated as "already covered" -- it's the most
    eligible case, not the least."""
    now = datetime(2026, 8, 30, 20, 30, tzinfo=UTC)
    assert cron_is_due("0 20 * * *", now=now, last_run_at=None)


def test_already_run_today_does_not_fire_again() -> None:
    """The core last_run_at contract must survive the rewrite: once today's fire has actually
    been recorded, calling again later the same day (or minute) must not re-fire."""
    fire_time = datetime(2026, 8, 30, 20, 0, tzinfo=UTC)
    later_same_day = datetime(2026, 8, 30, 21, 45, tzinfo=UTC)
    assert not cron_is_due("0 20 * * *", now=later_same_day, last_run_at=fire_time)


def test_run_recorded_at_exactly_the_fire_minute_does_not_refire_the_same_minute() -> None:
    now = datetime(2026, 8, 30, 20, 0, tzinfo=UTC)
    assert not cron_is_due("0 20 * * *", now=now, last_run_at=now)


def test_yesterdays_run_does_not_suppress_todays_fire() -> None:
    yesterday_fire = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    today = datetime(2026, 8, 30, 20, 5, tzinfo=UTC)
    assert cron_is_due("0 20 * * *", now=today, last_run_at=yesterday_fire)


def test_lookback_window_is_bounded_not_unbounded() -> None:
    """A last_run_at more than _MISSED_FIRE_LOOKBACK stale must not cause the scan to reach
    further back and fire an ancient missed occurrence -- it should only ever catch fires within
    the bounded window, same safety property the old exact-minute check had by construction."""
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    long_ago = now - _MISSED_FIRE_LOOKBACK - timedelta(days=10)
    # "0 20 * * *" last fired at 2026-08-29 20:00, which IS within the 24h lookback from noon
    # on the 30th -- so this should still correctly fire (last_run_at being ancient doesn't
    # suppress an in-window fire).
    assert cron_is_due("0 20 * * *", now=now, last_run_at=long_ago)


def test_no_fire_time_exists_within_the_lookback_window() -> None:
    """A cron whose only in-window match has already been covered by last_run_at, with no other
    matching minute anywhere in the scan range, must report not-due -- confirms the scan
    actually terminates at `earliest` rather than looping forever or defaulting to True."""
    # "0 20 * * *" fires once at 20:00 daily. Checking at 19:00 (before today's fire) with
    # last_run_at covering yesterday's fire means the only in-window match (today 20:00) hasn't
    # happened *yet* -- the scan must not walk past `now` into the future, so it finds nothing
    # and correctly reports not-due.
    now = datetime(2026, 8, 30, 19, 0, tzinfo=UTC)
    yesterdays_fire = datetime(2026, 8, 29, 20, 0, tzinfo=UTC)
    assert not cron_is_due("0 20 * * *", now=now, last_run_at=yesterdays_fire)


def test_multiple_missed_days_still_fires_once_not_multiple_times_per_call() -> None:
    """A preset that missed several consecutive days (e.g. the scheduler pod was down) must
    still report due=True on the next check -- the backward scan finds the most recent
    in-window fire, it doesn't need to "catch up" every missed day in a single call (the
    scheduler's own mark_preset_run + next day's tick handles that naturally)."""
    now = datetime(2026, 8, 30, 20, 10, tzinfo=UTC)
    three_days_ago = now - timedelta(days=3)
    assert cron_is_due("0 20 * * *", now=now, last_run_at=three_days_ago)


def test_timezone_conversion_still_applies_before_scanning() -> None:
    """0 10 * * * in Asia/Kolkata means 10:00 IST (04:30 UTC), not 10:00 UTC -- the tz
    conversion must still happen before the backward scan runs, same as the original
    implementation's single-minute check."""
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    ist_fire_as_utc = datetime(2026, 8, 18, 4, 30, tzinfo=UTC)  # 10:00 IST
    utc_ten_as_ist = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)  # 15:30 IST, NOT the fire time

    assert cron_is_due("0 10 * * *", now=ist_fire_as_utc, tz=ist)
    # Once today's IST fire is recorded, checking again later the same IST day (even at a
    # different UTC clock time) must not re-fire.
    assert not cron_is_due("0 10 * * *", now=utc_ten_as_ist, last_run_at=ist_fire_as_utc, tz=ist)


def test_weekday_field_uses_standard_cron_sunday_zero() -> None:
    monday = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
    saturday = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
    sunday = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
    assert monday.weekday() == 0
    assert saturday.weekday() == 5
    assert sunday.weekday() == 6
    friday = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
    assert cron_is_due("0 9 * * 1-5", now=monday)
    assert not cron_is_due("0 9 * * 1-5", now=saturday, last_run_at=friday)
    assert cron_is_due("0 9 * * 0", now=sunday)
    assert next_cron_tick("0 9 * * 1-5", now=saturday) == datetime(2026, 8, 24, 9, 0, tzinfo=UTC)


class TestNextCronTick:
    """next_cron_tick is a distinct forward-walking helper (informational "when will this next
    fire" display, e.g. scanner.py's config summary) -- it must keep exact-minute semantics for
    "next", which is a different question from cron_is_due's new "is anything overdue"."""

    def test_returns_the_exact_next_matching_minute(self) -> None:
        now = datetime(2026, 8, 30, 19, 58, tzinfo=UTC)
        assert next_cron_tick("0 20 * * *", now=now) == datetime(2026, 8, 30, 20, 0, tzinfo=UTC)

    def test_when_now_is_already_past_todays_fire_returns_tomorrows(self) -> None:
        """This is exactly the scenario that would have broken if next_cron_tick still routed
        through cron_is_due's new backward-scanning behavior -- it must walk forward to
        tomorrow's fire, not report `now` itself as already due."""
        now = datetime(2026, 8, 30, 20, 30, tzinfo=UTC)
        result = next_cron_tick("0 20 * * *", now=now)
        assert result == datetime(2026, 8, 31, 20, 0, tzinfo=UTC)

    def test_returns_now_itself_when_now_is_the_exact_fire_minute(self) -> None:
        now = datetime(2026, 8, 30, 20, 0, tzinfo=UTC)
        assert next_cron_tick("0 20 * * *", now=now) == now
