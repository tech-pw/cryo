"""Regression for #290: the commit-rate signal read 0 when the last two scan-history rows
were seconds apart.

Two overlapping scans of the same table wrote near-duplicate history rows (pairs 6 ms, 1 s
and 52 s apart, with identical snapshot counts). ``detect_streaming_from_history`` read only
the last pair, so the snapshot delta was 0 over a near-zero interval and the rate came out
as 0 -- whatever the real write velocity. The planner's streaming-aware retention then never
tightened, and the health score's "High velocity streaming" deduction never fired.

The fix takes the median rate across every recent pair far enough apart in time for the
delta to mean something, instead of the last pair only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.scanner.signals import detect_streaming_from_history

BASE = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _row(*, minutes: float, snapshots: int, **extra: object) -> dict[str, object]:
    return {
        "scan_at": BASE + timedelta(minutes=minutes),
        "total_snapshots": snapshots,
        **extra,
    }


def test_near_duplicate_last_pair_does_not_report_zero_rate() -> None:
    """Two overlapping scans 6 ms apart must not zero out the rate."""
    history = [
        _row(minutes=0, snapshots=100),
        _row(minutes=60, snapshots=170),
        _row(minutes=120, snapshots=240),
        _row(minutes=120 + 0.1, snapshots=240),  # overlapping scan, 6 seconds later
    ]

    is_streaming, commits_per_day = detect_streaming_from_history(history)

    assert commits_per_day > 0
    assert is_streaming is True


def test_median_rate_is_used_across_pairs() -> None:
    """The rate is the median over qualifying pairs, not the newest one."""
    history = [
        _row(minutes=0, snapshots=0),
        _row(minutes=60 * 24, snapshots=70),  # 70 commits over 24h = 70/day
        _row(minutes=60 * 48, snapshots=140),  # 70 commits over 24h = 70/day
        _row(minutes=60 * 72, snapshots=210),  # 70 commits over 24h = 70/day
    ]

    _, commits_per_day = detect_streaming_from_history(history)

    assert commits_per_day == 70.0


def test_single_close_pair_is_not_streaming() -> None:
    """One close pair with no growth is not streaming, and reports no rate."""
    history = [
        _row(minutes=0, snapshots=10),
        _row(minutes=0.5, snapshots=10),
    ]

    is_streaming, commits_per_day = detect_streaming_from_history(history)

    assert is_streaming is False
    assert commits_per_day == 0.0


def test_maintenance_job_suppresses_the_signal() -> None:
    """A row marked as explained by Cryo's own job keeps the signal off."""
    history = [
        _row(minutes=0, snapshots=100),
        _row(minutes=60, snapshots=170),
        _row(minutes=120, snapshots=240, maintenance_job_since_previous=True),
    ]

    is_streaming, commits_per_day = detect_streaming_from_history(history)

    assert is_streaming is False
    assert commits_per_day == 0.0


def test_pairs_longer_than_the_window_are_ignored() -> None:
    """A pair older than the window cannot describe current velocity."""
    history = [
        _row(minutes=0, snapshots=0),
        _row(minutes=60 * 48, snapshots=500),  # 2 days apart, outside the 24h window
    ]

    is_streaming, commits_per_day = detect_streaming_from_history(history)

    assert is_streaming is False
    assert commits_per_day == 0.0


def test_single_row_history_is_not_streaming() -> None:
    assert detect_streaming_from_history([_row(minutes=0, snapshots=5)]) == (False, 0.0)


def test_empty_history_is_not_streaming() -> None:
    assert detect_streaming_from_history([]) == (False, 0.0)


def test_snapshot_expiry_does_not_inflate_the_rate() -> None:
    """A pair where the snapshot count fell contributes no commits."""
    history = [
        _row(minutes=0, snapshots=500),
        _row(minutes=60, snapshots=100),  # expiry dropped the count
    ]

    _, commits_per_day = detect_streaming_from_history(history)

    assert commits_per_day == 0.0
