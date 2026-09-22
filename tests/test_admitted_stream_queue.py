"""Delivery guarantees of the admitted-job consumer group.

The list-based handoff this replaced (`RPUSH cryo:admitted` / `LPOP`) was at-most-once: a
worker that popped a job id and died before writing RUNNING to Postgres dropped the job
permanently, leaving the row ADMITTED with no engine_ref and no owner. These tests pin the
properties that make that impossible, and the two derived signals the governor reads off the
same structure instead of a separate heartbeat.

Uses fakeredis rather than a hand-written double because the behaviour under test *is* Redis
consumer-group semantics -- pending lists, idle-time reclaim, XINFO accounting.
"""

from __future__ import annotations

import time

import fakeredis
import pytest

from cryo.domain.enums import JobStatus
from cryo.queue import redis_queue as rq
from cryo.queue.redis_queue import RedisMaintenanceQueue

DEST = "dest-a"


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


def _queue(redis: fakeredis.FakeRedis, consumer: str) -> RedisMaintenanceQueue:
    return RedisMaintenanceQueue(redis, consumer=consumer)


def _admit(queue: RedisMaintenanceQueue, job_id: str) -> None:
    queue.enqueue(job_id, DEST)
    queue.admit(job_id, DEST)


def test_a_claimed_job_survives_the_death_of_the_worker_that_claimed_it(redis, monkeypatch):
    """The core regression: claim, then lose the worker before it starts the job."""
    monkeypatch.setattr(rq, "_CLAIM_HANDOFF_IDLE_MS", 0)
    doomed = _queue(redis, "worker-1")
    _admit(doomed, "job-1")

    assert doomed.claim_admitted() == "job-1"
    # worker-1 dies here: no mark_running, no complete, nothing written back.

    survivor = _queue(redis, "worker-2")
    assert survivor.claim_admitted() == "job-1"


def test_a_job_in_progress_is_not_handed_to_a_second_worker(redis, monkeypatch):
    monkeypatch.setattr(rq, "_CLAIM_HANDOFF_IDLE_MS", 0)
    owner = _queue(redis, "worker-1")
    _admit(owner, "job-1")

    assert owner.claim_admitted() == "job-1"
    owner.mark_running("job-1")

    # mark_running is the acknowledgement point, so the entry is gone and no reclaim can
    # double-dispatch a job that is already executing.
    assert _queue(redis, "worker-2").claim_admitted() is None


def test_a_reclaimed_entry_for_an_already_finished_job_is_discarded(redis, monkeypatch):
    monkeypatch.setattr(rq, "_CLAIM_HANDOFF_IDLE_MS", 0)
    owner = _queue(redis, "worker-1")
    _admit(owner, "job-1")
    assert owner.claim_admitted() == "job-1"

    # Finished out-of-band (run cancelled, status forced) without going through mark_running,
    # so the stream entry outlives the job. It must not be dispatched again.
    redis.hset(owner._job_key("job-1"), "status", JobStatus.SUCCEEDED.value)

    assert _queue(redis, "worker-2").claim_admitted() is None


def test_a_stale_entry_whose_job_hash_is_gone_is_cleared_rather_than_reclaimed_forever(
    redis, monkeypatch
):
    """The entry has to be droppable by its own id.

    Acknowledging via the job hash's recorded stream_id cannot clear an entry whose job has
    already completed -- completion removes that field. The entry would stay pending and
    every single poll would reclaim the same tombstone again.
    """
    monkeypatch.setattr(rq, "_CLAIM_HANDOFF_IDLE_MS", 0)
    owner = _queue(redis, "worker-1")
    _admit(owner, "job-1")
    owner.claim_admitted()
    redis.delete(owner._job_key("job-1"))

    survivor = _queue(redis, "worker-2")
    assert survivor.claim_admitted() is None
    # Actually gone, not merely skipped this once.
    assert survivor.admitted_depth() == 0
    assert redis.xlen(survivor._stream_key()) == 0


def test_a_fresh_claim_is_not_stolen_before_the_handoff_window_elapses(redis):
    owner = _queue(redis, "worker-1")
    _admit(owner, "job-1")
    assert owner.claim_admitted() == "job-1"

    # Default window is minutes; nothing has been idle that long.
    assert _queue(redis, "worker-2").claim_admitted() is None


def test_claims_are_handed_out_in_admission_order(redis):
    queue = _queue(redis, "worker-1")
    for job_id in ("job-1", "job-2", "job-3"):
        _admit(queue, job_id)

    claimed = []
    for _ in range(3):
        job_id = queue.claim_admitted()
        claimed.append(job_id)
        queue.mark_running(job_id)

    assert claimed == ["job-1", "job-2", "job-3"]


def test_an_empty_queue_claims_nothing(redis):
    assert _queue(redis, "worker-1").claim_admitted() is None


def test_admitted_depth_counts_only_work_no_worker_has_started(redis):
    """This is the governor's backpressure signal, so what it excludes matters."""
    queue = _queue(redis, "worker-1")
    for job_id in ("job-1", "job-2", "job-3"):
        _admit(queue, job_id)
    assert queue.admitted_depth() == 3

    # Claimed but not started: a worker owns it, so it is no longer queue depth.
    queue.claim_admitted()
    assert queue.admitted_depth() == 2

    queue.mark_running("job-1")
    assert queue.admitted_depth() == 2

    queue.claim_admitted()
    queue.mark_running("job-2")
    queue.complete("job-2", JobStatus.SUCCEEDED)
    assert queue.admitted_depth() == 1


def test_a_worker_busy_on_a_long_job_still_counts_as_alive(redis):
    """Why the heartbeat this replaced could not work.

    A worker runs one job at a time and does not poll again until it finishes, so presence
    measured by polling alone read zero exactly when every worker was busy -- which made the
    governor's drain-rate cap inert under the load it exists to handle.
    """
    queue = _queue(redis, "worker-1")
    _admit(queue, "job-1")
    queue.claim_admitted()

    assert queue.active_worker_count(stale_after_seconds=0) == 1


def test_a_worker_that_stopped_polling_and_holds_nothing_is_not_counted(redis, monkeypatch):
    """Dead consumers linger in the group until reaped, so recency has to be checked.

    The idle times are injected because fakeredis reports everything as idle for 0ms inside a
    single test; the accounting under test is the predicate, not Redis's clock.
    """
    queue = _queue(redis, "worker-1")
    monkeypatch.setattr(
        redis,
        "xinfo_consumers",
        lambda *_, **__: [
            {b"name": b"busy", b"pending": 1, b"inactive": 900_000},
            {b"name": b"polling", b"pending": 0, b"inactive": 5_000},
            {b"name": b"gone", b"pending": 0, b"inactive": 900_000},
        ],
    )

    assert queue.active_worker_count(stale_after_seconds=60) == 2


def test_each_worker_pod_is_counted_once_and_observers_are_not_counted(redis):
    for name in ("worker-1", "worker-2", "worker-3"):
        _queue(redis, name).claim_admitted()

    # The governor only ever reads this count; asking the question must not make it look like
    # another worker showed up.
    assert _queue(redis, "governor").active_worker_count(stale_after_seconds=60) == 3


def test_worker_count_is_zero_before_any_worker_has_ever_polled(redis):
    queue = _queue(redis, "worker-1")
    _admit(queue, "job-1")

    assert queue.active_worker_count() == 0


def test_awaiting_claim_is_true_between_admission_and_start(redis):
    queue = _queue(redis, "worker-1")
    _admit(queue, "job-1")
    assert queue.awaiting_claim("job-1")

    queue.claim_admitted()
    assert queue.awaiting_claim("job-1")

    queue.mark_running("job-1")
    assert not queue.awaiting_claim("job-1")


def test_awaiting_claim_is_false_for_a_job_the_queue_never_saw(redis):
    assert not _queue(redis, "worker-1").awaiting_claim("job-unknown")


def test_returning_a_job_to_pending_takes_it_out_of_the_stream(redis):
    queue = _queue(redis, "worker-1")
    _admit(queue, "job-1")

    queue.return_to_pending("job-1", DEST)

    assert not queue.awaiting_claim("job-1")
    assert queue.admitted_depth() == 0
    assert queue.pending(DEST) == ["job-1"]
    assert queue.claim_admitted() is None


def test_deferring_a_job_takes_it_out_of_the_stream(redis):
    queue = _queue(redis, "worker-1")
    _admit(queue, "job-1")
    queue.claim_admitted()

    queue.defer("job-1")

    assert not queue.awaiting_claim("job-1")
    assert queue.admitted_depth() == 0


def test_continuation_puts_a_job_back_without_releasing_its_in_flight_slot(redis):
    queue = _queue(redis, "worker-1")
    _admit(queue, "job-1")
    queue.claim_admitted()
    queue.mark_running("job-1")
    in_flight_before = queue.local_in_flight(DEST)

    # fakeredis 2.38 resets a stream's last_id once XDEL empties it, so the XADD below can
    # be handed back the id of the entry mark_running just deleted -- an id the group has
    # already delivered, which XREADGROUP ">" then skips, failing this test ~20% of the time.
    # Real Redis retains last_id across XDEL and never regresses an id: verified against
    # redis:7-alpine over 20k XADD/XDEL/XADD cycles, 13,525 of which landed both writes in
    # the same millisecond, with zero reuse. The sleep only dodges the fake's defect.
    time.sleep(0.002)
    queue.requeue_for_continuation("job-1")

    assert queue.local_in_flight(DEST) == in_flight_before
    assert queue.claim_admitted() == "job-1"


def test_completion_releases_the_destination_in_flight_slot(redis):
    queue = _queue(redis, "worker-1")
    _admit(queue, "job-1")
    assert queue.local_in_flight(DEST) == 1

    queue.claim_admitted()
    queue.mark_running("job-1")
    queue.complete("job-1", JobStatus.SUCCEEDED)

    assert queue.local_in_flight(DEST) == 0


def test_a_restarted_pod_picks_its_own_unfinished_handoff_back_up(redis, monkeypatch):
    """Consumer names are derived from the pod name, so a container restart rejoins as
    itself rather than stranding its own unacknowledged entry."""
    monkeypatch.setattr(rq, "_CLAIM_HANDOFF_IDLE_MS", 0)
    before = _queue(redis, "cryo-worker-abc123")
    _admit(before, "job-1")
    assert before.claim_admitted() == "job-1"

    after_restart = _queue(redis, "cryo-worker-abc123")
    assert after_restart.claim_admitted() == "job-1"


def test_nothing_is_lost_when_many_workers_claim_concurrently(redis):
    queue = _queue(redis, "admitter")
    for index in range(25):
        _admit(queue, f"job-{index}")

    workers = [_queue(redis, f"worker-{i}") for i in range(5)]
    claimed: list[str] = []
    while True:
        progressed = False
        for worker in workers:
            job_id = worker.claim_admitted()
            if job_id is None:
                continue
            claimed.append(job_id)
            worker.mark_running(job_id)
            progressed = True
        if not progressed:
            break

    assert sorted(claimed) == sorted(f"job-{i}" for i in range(25))
    assert queue.admitted_depth() == 0
