"""Every key the queue creates has to expire on its own.

Nothing ever deleted a `cryo:job:<id>` hash. `complete()` wrote a terminal value into the
hash's status field and left the key in place, so the keyspace only grew. Measured in prod on
2026-09-23: 22,552 job hashes, Redis at 31.8MB after 44.5h uptime -- 17.1 MB/day against a
512Mi container limit, about a month from an empty start to being OOMKilled. `maxmemory` was
0, so Redis never pushed back, and `/data` was the container's ephemeral layer, so the kill
took the snapshot with it too.

Uses fakeredis rather than a hand-written double because TTL bookkeeping is exactly the
behaviour under test, and the hand-rolled `FakeRedis` in test_redis_maintenance_queue.py
models no expiry at all.
"""

from __future__ import annotations

import uuid

import fakeredis
import pytest

from cryo.domain.enums import JobStatus
from cryo.queue.redis_queue import (
    _ACTIVE_JOB_TTL_SEC,
    _TERMINAL_JOB_TTL_SEC,
    RedisMaintenanceQueue,
)
from cryo.queue.run_events import STREAM_TTL_SEC, RedisRunEventBus

DEST = "dest-a"


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


@pytest.fixture
def queue(redis: fakeredis.FakeRedis) -> RedisMaintenanceQueue:
    return RedisMaintenanceQueue(redis, consumer="worker-1")


def _ttl(redis: fakeredis.FakeRedis, job_id: str) -> int:
    return int(redis.ttl(f"cryo:job:{job_id}"))


def test_an_enqueued_job_hash_expires_rather_than_living_forever(redis, queue) -> None:
    queue.enqueue("job-1", DEST)

    assert _ttl(redis, "job-1") > 0


def test_the_active_horizon_outlasts_the_longest_real_queue_wait() -> None:
    """A RUNNING job is reaped at 6h, but a QUEUED one can wait far longer behind a backlog --
    3,046 jobs once sat queued for 21+ hours. The safety net has to clear that comfortably, or
    it would delete the hash of a job that is still genuinely waiting to run."""
    assert _ACTIVE_JOB_TTL_SEC >= 48 * 60 * 60


def test_completing_a_job_shortens_its_hash_to_the_terminal_horizon(redis, queue) -> None:
    """The change that actually bounds the keyspace: completions are the overwhelming
    majority of keys, so they must not keep the week-long active horizon."""
    queue.enqueue("job-1", DEST)
    queue.admit("job-1", DEST)
    queue.mark_running("job-1")
    assert _ttl(redis, "job-1") > _TERMINAL_JOB_TTL_SEC

    queue.complete("job-1", JobStatus.SUCCEEDED)

    assert 0 < _ttl(redis, "job-1") <= _TERMINAL_JOB_TTL_SEC


def test_a_job_still_in_play_keeps_getting_its_expiry_pushed_out(redis, queue) -> None:
    """Each transition refreshes the horizon, so time spent queued never accumulates toward
    deleting a live job's hash."""
    queue.enqueue("job-1", DEST)
    redis.expire("cryo:job:job-1", 30)
    assert _ttl(redis, "job-1") <= 30

    queue.admit("job-1", DEST)
    assert _ttl(redis, "job-1") > 30

    redis.expire("cryo:job:job-1", 30)
    queue.mark_running("job-1")
    assert _ttl(redis, "job-1") > 30


def test_a_deferred_job_keeps_the_active_horizon_not_the_terminal_one(redis, queue) -> None:
    """Deferred means "come back on a later wave", not finished. Giving it the short terminal
    horizon would delete the hash of a job the governor still intends to admit."""
    queue.enqueue("job-1", DEST)
    queue.admit("job-1", DEST)

    queue.defer("job-1")

    assert _ttl(redis, "job-1") > _TERMINAL_JOB_TTL_SEC


def test_returning_a_job_to_pending_keeps_it_addressable(redis, queue) -> None:
    queue.enqueue("job-1", DEST)
    queue.admit("job-1", DEST)
    redis.expire("cryo:job:job-1", 30)

    queue.return_to_pending("job-1", DEST)

    assert _ttl(redis, "job-1") > 30


def test_no_job_hash_survives_a_full_lifecycle_without_an_expiry(redis, queue) -> None:
    """The invariant, stated directly: after driving jobs through every transition the queue
    supports, not one `cryo:job:*` key is left immortal. A new code path that writes the hash
    without refreshing its TTL fails here rather than in a month of production growth."""
    completed, deferred, returned, running = (str(uuid.uuid4()) for _ in range(4))

    queue.enqueue(completed, DEST)
    queue.admit(completed, DEST)
    queue.mark_running(completed)
    queue.complete(completed, JobStatus.SUCCEEDED)

    queue.enqueue(deferred, DEST)
    queue.admit(deferred, DEST)
    queue.defer(deferred)

    queue.enqueue(returned, DEST)
    queue.admit(returned, DEST)
    queue.return_to_pending(returned, DEST)

    queue.enqueue(running, DEST)
    queue.admit(running, DEST)
    queue.mark_running(running)

    immortal = [key.decode() for key in redis.scan_iter(match="cryo:job:*") if redis.ttl(key) == -1]

    assert immortal == []


def test_a_run_event_stream_expires_after_its_last_event(redis) -> None:
    """MAX_STREAM_LEN capped how big one run's buffer got, but nothing capped how many runs
    accumulated -- one key each, forever. Safe to expire because every event is also archived
    to the observation lakehouse; this stream is only the live tail."""
    bus = RedisRunEventBus(redis)
    run_id = uuid.uuid4()

    bus.publish(run_id, {"type": "log_line", "message": "started"})

    ttl = int(redis.ttl(f"cryo:run:{run_id}:events"))
    assert 0 < ttl <= STREAM_TTL_SEC


def test_publishing_again_slides_the_streams_expiry_forward(redis) -> None:
    """A long run keeps its buffer alive while it is still producing events."""
    bus = RedisRunEventBus(redis)
    run_id = uuid.uuid4()
    bus.publish(run_id, {"type": "log_line", "message": "started"})
    redis.expire(f"cryo:run:{run_id}:events", 30)

    bus.publish(run_id, {"type": "log_line", "message": "still going"})

    assert int(redis.ttl(f"cryo:run:{run_id}:events")) > 30
