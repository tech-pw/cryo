"""Tests for cryo/scheduler/scheduler.py's fifo_priority and its wiring into enqueue_run --
the fix for a live cross-run admission-starvation incident, 2026-09-01.

Distinct from test_scheduler_priority_order.py's apply_priority_order (which orders
candidates *within* one run's own job list before enqueue): every enqueue_run call site left
priority at its 0 default, so once jobs from different runs landed in the same destination's
Redis sorted set, ZREVRANGE (cryo/queue/redis_queue.py) fell back to reverse-lexicographic
UUID ordering on the tied scores -- admission across runs sharing a destination was
effectively random by job UUID, not fair or age-based. Confirmed live: two runs open 28h with
621 combined jobs on spark-k8s-fallback got zero admissions for hours while a same-day run
kept getting slots purely because its jobs' UUIDs happened to sort higher.

fifo_priority gives every run's jobs a score keyed on the run's own creation time, so the
oldest run at a shared destination is always admitted first (ZREVRANGE returns highest score
first; negated epoch seconds makes "older" == "higher score").
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from cryo.domain.enums import Mode
from cryo.scheduler.scheduler import (
    ScheduledJobDraft,
    ScheduledRunDraft,
    enqueue_run,
    fifo_priority,
)


def test_older_created_at_yields_higher_priority_score() -> None:
    older = fifo_priority(datetime(2026, 8, 31, 8, 0, tzinfo=UTC))
    newer = fifo_priority(datetime(2026, 9, 1, 3, 0, tzinfo=UTC))
    # ZREVRANGE returns highest score first -- older must score higher so it's admitted first.
    assert older > newer


def test_fifo_priority_defaults_to_now_when_no_created_at_given() -> None:
    before = fifo_priority()
    after = fifo_priority(datetime.now(UTC))
    # Both approximate "now" -- must be close (same second or adjacent), not wildly apart.
    assert abs(before - after) <= 1


def test_enqueue_run_passes_fifo_priority_by_default() -> None:
    run = ScheduledRunDraft(
        id="run-1",
        preset_id="preset-1",
        trigger="cron",
        deadline_at=None,
        jobs=(
            ScheduledJobDraft(
                id="job-1", run_id="run-1", table_id="t1", destination_id="dest-1", mode=Mode.DEEP
            ),
        ),
    )
    queue = MagicMock()
    created_at = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)

    enqueue_run(run, queue, created_at=created_at)

    queue.enqueue.assert_called_once()
    args, _ = queue.enqueue.call_args
    assert args[0] == "job-1"
    assert args[1] == "dest-1"
    assert args[2] == fifo_priority(created_at)


def test_enqueue_run_explicit_priority_overrides_fifo() -> None:
    """An explicit priority (if some future caller ever needs one) must still win over the
    fifo default -- fifo_priority is the fallback, not a mandatory override."""
    run = ScheduledRunDraft(
        id="run-1",
        preset_id="preset-1",
        trigger="cron",
        deadline_at=None,
        jobs=(
            ScheduledJobDraft(
                id="job-1", run_id="run-1", table_id="t1", destination_id="dest-1", mode=Mode.DEEP
            ),
        ),
    )
    queue = MagicMock()

    enqueue_run(run, queue, priority=999)

    args, _ = queue.enqueue.call_args
    assert args[2] == 999


def test_two_runs_enqueued_older_first_admit_in_creation_order() -> None:
    """Direct regression for the live incident shape: two runs' jobs land in the same
    destination's queue at different priorities derived from their creation times -- a real
    (in-memory) queue must return the older run's job first from a descending-priority read,
    matching what ZREVRANGE does against the real Redis-backed queue."""
    from cryo.queue.redis_queue import InMemoryMaintenanceQueue

    queue = InMemoryMaintenanceQueue()
    older_run = ScheduledRunDraft(
        id="run-old",
        preset_id="preset-1",
        trigger="cron",
        deadline_at=None,
        jobs=(
            ScheduledJobDraft(
                id="job-old",
                run_id="run-old",
                table_id="t1",
                destination_id="dest-1",
                mode=Mode.DEEP,
            ),
        ),
    )
    newer_run = ScheduledRunDraft(
        id="run-new",
        preset_id="preset-1",
        trigger="cron",
        deadline_at=None,
        jobs=(
            ScheduledJobDraft(
                id="job-new",
                run_id="run-new",
                table_id="t2",
                destination_id="dest-1",
                mode=Mode.DEEP,
            ),
        ),
    )
    # Enqueue newer first (simulating the newer run's cron firing before the older one's
    # backlog has drained) -- FIFO priority, not insertion order, must still decide admission.
    enqueue_run(newer_run, queue, created_at=datetime(2026, 9, 1, 3, 0, tzinfo=UTC))
    enqueue_run(older_run, queue, created_at=datetime(2026, 8, 31, 8, 0, tzinfo=UTC))

    pending = queue.pending("dest-1")
    assert pending[0] == "job-old"
    assert pending[1] == "job-new"
