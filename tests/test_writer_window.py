"""Writer-window cadence math: when a rewrite can commit without failing an external writer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cryo.domain.enums import OperationType
from cryo.planner.writer_window import (
    DATA_REWRITE_OPERATIONS,
    WindowVerdict,
    WriterCommit,
    WriterWindowPolicy,
    contended_chunk_size_gb,
    decide_writer_window,
    format_hold,
    hold_retry_at,
    writer_cadence,
)

MIN = 60_000
# The ingestion DAG in the 2026-11-15 incident committed a copy-on-write MERGE at ~:10.
FIRST = int(datetime(2026, 11, 15, 0, 10, tzinfo=UTC).timestamp() * 1000)
POLICY = WriterWindowPolicy()


def _hourly(hours: int, *, operation: str = "overwrite", start: int = FIRST) -> list[WriterCommit]:
    return [WriterCommit(start + h * 60 * MIN, operation) for h in range(hours)]


def _window(commits: list[WriterCommit], now_ms: int, policy: WriterWindowPolicy = POLICY):
    cadence = writer_cadence(commits, now_ms=now_ms, policy=policy)
    return decide_writer_window(cadence, now_ms=now_ms, policy=policy)


LAST = FIRST + 7 * 60 * MIN  # 07:10, the 8th hourly commit


def test_right_after_a_writer_commit_the_window_is_open_until_the_next_run_reads() -> None:
    window = _window(_hourly(8), LAST + 3 * MIN)

    assert window.verdict is WindowVerdict.OPEN
    assert window.allows_rewrite
    # Next commit ~08:10; the writer reads ~15 min earlier.
    assert window.closes_at_ms == LAST + 60 * MIN - 15 * MIN
    assert window.cadence is not None and window.cadence.interval_minutes == 60


def test_a_commit_moments_ago_holds_until_the_run_settles() -> None:
    window = _window(_hourly(8), LAST + 30_000)

    assert window.verdict is WindowVerdict.HOLD
    assert window.retry_at_ms == LAST + 2 * MIN


def test_close_to_the_next_writer_commit_holds_until_after_it() -> None:
    now = LAST + 40 * MIN  # 07:50: 5 min before the writer reads, 20 min before it commits

    window = _window(_hourly(8), now)

    assert window.verdict is WindowVerdict.HOLD
    assert not window.allows_rewrite
    assert window.retry_at_ms == LAST + 60 * MIN + 2 * MIN


def test_an_overdue_writer_is_polled_until_it_commits() -> None:
    now = LAST + 70 * MIN  # the 08:10 commit has not landed yet

    window = _window(_hourly(8), now)

    assert window.verdict is WindowVerdict.HOLD
    assert window.retry_at_ms == now + 5 * MIN


def test_a_writer_that_missed_a_whole_run_no_longer_holds_maintenance() -> None:
    window = _window(_hourly(8), LAST + 125 * MIN)

    assert window.verdict is WindowVerdict.UNTRACKED
    assert window.allows_rewrite


def test_appends_and_compaction_commits_are_not_writers() -> None:
    commits = _hourly(8, operation="append") + _hourly(8, operation="replace", start=FIRST + MIN)

    assert _window(commits, LAST + 40 * MIN).verdict is WindowVerdict.UNTRACKED


def test_merge_on_read_deletes_count_as_writers() -> None:
    assert _window(_hourly(8, operation="delete"), LAST + 40 * MIN).verdict is WindowVerdict.HOLD


def test_several_commits_in_one_run_are_one_run() -> None:
    commits = _hourly(8) + _hourly(8, operation="delete", start=FIRST + 3 * MIN)

    cadence = writer_cadence(commits, now_ms=LAST + 10 * MIN, policy=POLICY)

    assert cadence is not None
    assert cadence.runs == 8
    assert cadence.interval_ms == 60 * MIN
    assert cadence.run_span_ms == 3 * MIN
    window = decide_writer_window(cadence, now_ms=LAST + 4 * MIN, policy=POLICY)
    # Still inside the run's usual span plus settle.
    assert window.verdict is WindowVerdict.HOLD
    assert window.retry_at_ms == LAST + 5 * MIN


def test_a_writer_every_fifteen_minutes_leaves_no_window() -> None:
    commits = [WriterCommit(FIRST + i * 15 * MIN, "overwrite") for i in range(20)]

    window = _window(commits, FIRST + 19 * 15 * MIN + 3 * MIN)

    assert window.verdict is WindowVerdict.NO_WINDOW
    assert not window.allows_rewrite
    assert window.retry_at_ms is None


@pytest.mark.parametrize(
    ("commits", "label"),
    [
        (_hourly(3), "too few runs"),
        ([WriterCommit(FIRST + h * 8 * 60 * MIN, "overwrite") for h in range(3)], "every 8h"),
        ([], "no commits"),
    ],
)
def test_irregular_or_infrequent_writers_are_untracked(
    commits: list[WriterCommit], label: str
) -> None:
    now = (commits[-1].timestamp_ms if commits else FIRST) + 30 * MIN

    assert _window(commits, now).verdict is WindowVerdict.UNTRACKED, label


def test_commits_outside_the_lookback_are_ignored() -> None:
    now = LAST + 30 * 60 * MIN

    assert writer_cadence(_hourly(8), now_ms=now, policy=POLICY) is None


def test_the_incident_replayed_cryo_would_only_have_started_between_writer_runs() -> None:
    """2026-11-15: an hourly MERGE at ~:10 failed whenever a Cryo replace commit landed
    between its read and its commit. Every minute of a day, the gate opens only when a
    20-minute rewrite finishes before the next run reads."""
    commits = _hourly(48)
    opened = set()
    for minute in range(24 * 60, 48 * 60):
        now = FIRST + minute * MIN
        window = _window([c for c in commits if c.timestamp_ms <= now], now)
        if window.verdict is WindowVerdict.OPEN:
            assert window.closes_at_ms is not None
            assert now + 20 * MIN <= window.closes_at_ms
            opened.add((10 + minute) % 60)
    # Wall-clock minutes: from 2 min after the :10 commit to 20 min before the :55 read.
    assert opened == set(range(12, 36))


def test_policy_overrides_come_from_the_preset() -> None:
    assert not WriterWindowPolicy.from_params({"writer_window": False}).enabled
    policy = WriterWindowPolicy.from_params(
        {"writer_window": {"lead_minutes": 25, "min_runs": 6.0, "bogus": 1, "settle_minutes": -1}}
    )
    assert policy.lead_minutes == 25
    assert policy.min_runs == 6
    assert policy.settle_minutes == POLICY.settle_minutes
    assert WriterWindowPolicy.from_params({"writer_window": "yes"}) == POLICY


def test_a_hold_round_trips_through_the_job_error() -> None:
    window = _window(_hourly(8), LAST + 40 * MIN)

    error = format_hold(window)

    assert error.startswith("writer_window: ")
    assert hold_retry_at(error) == datetime(2026, 11, 15, 8, 12, tzinfo=UTC)
    assert hold_retry_at("table_locked") is None
    assert hold_retry_at("writer_window: no retry") is None


def test_only_data_rewrites_are_gated() -> None:
    assert OperationType.OPTIMIZE in DATA_REWRITE_OPERATIONS
    assert OperationType.EXPIRE_DATA in DATA_REWRITE_OPERATIONS
    assert OperationType.EXPIRE_SNAPSHOTS not in DATA_REWRITE_OPERATIONS
    assert OperationType.OPTIMIZE_MANIFESTS not in DATA_REWRITE_OPERATIONS
    assert OperationType.REMOVE_ORPHAN_FILES not in DATA_REWRITE_OPERATIONS


def _stats(signals: dict) -> dict:
    return {"raw": {"commit_signals": signals}}


def test_contended_chunk_cap_reads_the_stored_writer_cadence() -> None:
    hourly = {"writer_cadence": {"runs": 24, "interval_minutes": 60.0}}

    assert contended_chunk_size_gb(_stats(hourly), {}) == 4.0
    assert (
        contended_chunk_size_gb(_stats(hourly), {"writer_window": {"contended_chunk_size_gb": 2}})
        == 2.0
    )
    assert contended_chunk_size_gb(_stats(hourly), {"writer_window": False}) is None
    assert contended_chunk_size_gb(_stats({"writer_cadence": None}), {}) is None
    slow = {"writer_cadence": {"runs": 4, "interval_minutes": 720.0}}
    assert contended_chunk_size_gb(_stats(slow), {}) is None
    assert contended_chunk_size_gb({}, {}) is None


def test_contended_chunk_cap_falls_back_to_the_arrival_gap_on_older_scans() -> None:
    # gold_orders' stored signals before writer_cadence existed.
    hourly = {"data_commits": 71, "arrival_gap_p50_hours": 0.996, "hours_since_data_commit": 0.6}
    daily = {"data_commits": 8, "arrival_gap_p50_hours": 24.0, "hours_since_data_commit": 2}
    gone = {**hourly, "hours_since_data_commit": 72}

    assert contended_chunk_size_gb(_stats(hourly), {}) == 4.0
    assert contended_chunk_size_gb(_stats(daily), {}) is None
    assert contended_chunk_size_gb(_stats(gone), {}) is None


def test_hold_retry_at_is_utc() -> None:
    window = _window(_hourly(8), LAST + 30_000)
    retry = hold_retry_at(format_hold(window))

    assert retry is not None and retry.tzinfo is not None
    assert retry - datetime.fromtimestamp(LAST / 1000, tz=UTC) == timedelta(minutes=2)
