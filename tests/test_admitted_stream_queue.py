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
from redis.exceptions import ResponseError

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


def test_a_worker_keeps_claiming_after_redis_loses_the_stream_and_its_group(redis):
    """Regression for #376: the chart's Redis has no persistence, so a restart drops the
    admitted stream and the `workers` group. The worker had already cached that its group
    existed, so every XAUTOCLAIM failed with NOGROUP until the pod itself was restarted."""
    worker = _queue(redis, "worker-1")
    _admit(worker, "job-1")
    assert worker.claim_admitted() == "job-1"
    worker.mark_running("job-1")

    redis.flushall()
    assert worker.claim_admitted() is None

    # The governor admits again after the restart; XADD recreates the stream with no group.
    redis.flushall()
    _admit(_queue(redis, "governor"), "job-2")
    assert worker.claim_admitted() == "job-2"


def test_a_worker_recovers_when_only_the_stream_key_is_deleted(redis):
    worker = _queue(redis, "worker-1")
    assert worker.claim_admitted() is None

    redis.delete(worker._stream_key())
    _admit(_queue(redis, "governor"), "job-1")

    assert worker.claim_admitted() == "job-1"


def test_a_group_lost_while_the_stream_survives_redelivers_every_unstarted_entry(redis):
    """Recreated from id 0: acknowledged entries are deleted at mark_running, so whatever is
    still in the stream is admitted work nobody has started, and all of it must come back."""
    worker = _queue(redis, "worker-1")
    for job_id in ("job-1", "job-2"):
        _admit(worker, job_id)
    assert worker.claim_admitted() == "job-1"
    worker.mark_running("job-1")

    redis.xgroup_destroy(worker._stream_key(), rq._ADMITTED_GROUP)

    assert worker.claim_admitted() == "job-2"


def test_a_recreated_group_is_retried_only_once(redis, monkeypatch):
    worker = _queue(redis, "worker-1")
    calls = []

    def always_missing(*_args, **_kwargs):
        calls.append(1)
        raise ResponseError("NOGROUP No such key or consumer group")

    monkeypatch.setattr(redis, "xreadgroup", always_missing)

    with pytest.raises(ResponseError, match="NOGROUP"):
        worker.claim_admitted()
    assert len(calls) == 2


def test_other_redis_errors_are_not_mistaken_for_a_lost_group(redis, monkeypatch):
    worker = _queue(redis, "worker-1")
    recreated = []
    real_create = redis.xgroup_create

    def counting_create(*args, **kwargs):
        recreated.append(1)
        return real_create(*args, **kwargs)

    def wrong_type(*_args, **_kwargs):
        raise ResponseError("WRONGTYPE Operation against a key")

    monkeypatch.setattr(redis, "xgroup_create", counting_create)
    monkeypatch.setattr(redis, "xreadgroup", wrong_type)

    with pytest.raises(ResponseError, match="WRONGTYPE"):
        worker.claim_admitted()
    assert len(recreated) == 1


def test_worker_presence_is_visible_again_once_a_worker_polls_after_a_restart(redis):
    governor = _queue(redis, "governor")
    worker = _queue(redis, "worker-1")
    worker.claim_admitted()
    assert governor.active_worker_count(stale_after_seconds=60) == 1

    redis.flushall()
    assert governor.active_worker_count(stale_after_seconds=60) == 0
    worker.claim_admitted()

    assert governor.active_worker_count(stale_after_seconds=60) == 1


def test_returning_a_job_redis_forgot_does_not_drive_in_flight_negative(redis):
    """After a restart the in-flight counter starts at zero. Releasing a slot this Redis never
    recorded would leave it at -1, and every later admission would read one short."""
    queue = _queue(redis, "governor")
    _admit(queue, "job-1")

    redis.flushall()
    # What orphan recovery writes for a DB-ADMITTED job whose hash is gone.
    queue.enqueue("job-1", DEST)
    queue.return_to_pending("job-1", DEST)
    _admit(queue, "job-2")

    assert queue.pending(DEST) == ["job-1"]
    assert redis.hgetall(queue._in_flight_key(DEST)) == {b"default": b"1"}


def test_returning_an_admitted_job_still_releases_its_slot(redis):
    queue = _queue(redis, "governor")
    queue.enqueue("job-1", DEST)
    queue.admit("job-1", DEST, replica="r1")
    assert queue.local_in_flight_by_replica(DEST) == {"r1": 1}

    queue.return_to_pending("job-1", DEST)

    assert queue.local_in_flight_by_replica(DEST) == {"r1": 0}


def test_drain_snapshot_counts_busy_workers_and_the_oldest_unclaimed_wait(redis):
    """#490: the capacity card needs busy workers, unclaimed jobs and how long they waited."""
    worker = _queue(redis, "worker-1")
    _admit(worker, "job-1")
    _admit(worker, "job-2")
    _admit(worker, "job-3")
    assert worker.claim_admitted() == "job-1"
    time.sleep(0.01)

    snapshot = worker.drain_snapshot()

    assert snapshot["workers_alive"] == 1
    assert snapshot["workers_busy"] == 1
    assert snapshot["admitted_waiting"] == 2
    assert snapshot["oldest_waiting_seconds"] is not None
    assert 0 <= snapshot["oldest_waiting_seconds"] < 60


def test_drain_snapshot_with_nothing_waiting(redis):
    snapshot = _queue(redis, "worker-1").drain_snapshot()

    assert snapshot["admitted_waiting"] == 0
    assert snapshot["oldest_waiting_seconds"] is None
