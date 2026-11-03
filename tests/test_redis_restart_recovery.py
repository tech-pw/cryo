"""What survives a Redis restart, end to end (#376).

The chart's Redis keeps no persistence, so a restart drops every key: the admitted stream and
its consumer group, job hashes, pending zsets, in-flight counters and table locks. Postgres
still holds the truth. These tests drive the real Redis queue (fakeredis), the governor, the
worker's orphan recovery and the worker daemon loop through that restart and check that work
already ADMITTED in Postgres is dispatched again rather than stranded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest

from cryo.cli import WorkerRunner
from cryo.domain.enums import JobStatus, Mode
from cryo.events.redis_stream import RedisEventStream
from cryo.governor.admission import IST, AdmissionConfig
from cryo.governor.governor import Governor
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.reconcile import reconcile_stuck_admitted_jobs
from cryo.queue.redis_queue import RedisMaintenanceQueue
from cryo.worker.orphan_recovery import recover_orphaned_jobs
from cryo.worker.worker import DestinationConfig, InMemoryJobStore, MaintenanceJobRecord

DEST = "dest"
_WAVE_NOW = datetime(2026, 6, 29, 20, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _ist_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cryo.display_time.display_zone", lambda: IST)


@pytest.fixture
def redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis()


class _NoSteps:
    def get_started_step(self, job_id: str) -> None:
        del job_id


def _store(*job_ids: str) -> InMemoryJobStore:
    store = InMemoryJobStore()
    _add_jobs(store, *job_ids)
    store.add_destination(
        DestinationConfig(
            DEST,
            config={
                "replicas": ["r1"],
                "per_replica_cap": 4,
                "offpeak_start_ist": 0,
                "offpeak_end_ist": 7,
                "adaptive_budget": False,
            },
        )
    )
    return store


def _add_jobs(store: InMemoryJobStore, *job_ids: str) -> None:
    for job_id in job_ids:
        store.add_job(
            MaintenanceJobRecord(
                id=job_id,
                run_id="run-1",
                table_id=f"table-{job_id}",
                destination_id=DEST,
                mode=Mode.STANDARD,
            )
        )


def _governor(queue: RedisMaintenanceQueue, locks: InMemoryLockStore, store: InMemoryJobStore):
    return Governor(queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False))


def _admit_through_governor(
    queue: RedisMaintenanceQueue, store: InMemoryJobStore, locks: InMemoryLockStore, *job_ids: str
) -> None:
    for job_id in job_ids:
        queue.enqueue(job_id, DEST)
    assert _governor(queue, locks, store).run_wave(now=_WAVE_NOW).admitted == len(job_ids)


def test_admitted_jobs_are_dispatched_again_after_redis_loses_everything(redis) -> None:
    store = _store("job-1", "job-2")
    locks = InMemoryLockStore()
    governor_queue = RedisMaintenanceQueue(redis, consumer="governor")
    _admit_through_governor(governor_queue, store, locks, "job-1", "job-2")
    worker_queue = RedisMaintenanceQueue(redis, consumer="worker-1")
    # The worker has polled before, so it has already cached that the group exists.
    assert worker_queue.claim_admitted() in {"job-1", "job-2"}

    redis.flushall()
    assert {job.status for job in store.jobs.values()} == {JobStatus.ADMITTED}

    # Worker tick: orphan recovery puts DB-ADMITTED jobs Redis has forgotten back in pending.
    recover_orphaned_jobs(store, worker_queue, locks, _NoSteps(), holder_id="worker-1")
    assert sorted(governor_queue.pending(DEST)) == ["job-1", "job-2"]

    # Next governor wave admits them onto the recreated stream.
    assert _governor(governor_queue, locks, store).run_wave(now=_WAVE_NOW).admitted == 2

    claimed = {worker_queue.claim_admitted(), worker_queue.claim_admitted()}
    assert claimed == {"job-1", "job-2"}
    assert governor_queue.local_in_flight(DEST) == 2


def test_the_governor_backstop_requeues_admitted_jobs_without_any_worker_help(redis) -> None:
    """If no worker runs orphan recovery (fleet scaled to zero, every pod crash-looping), the
    governor's own stuck-admitted pass returns the jobs to pending once the grace period ends."""
    store = _store("job-1")
    locks = InMemoryLockStore()
    governor_queue = RedisMaintenanceQueue(redis, consumer="governor")
    _admit_through_governor(governor_queue, store, locks, "job-1")

    redis.flushall()
    requeued = reconcile_stuck_admitted_jobs(
        store,
        governor_queue,
        locks,
        now=datetime.now(UTC) + timedelta(hours=1),
        stuck_after_seconds=15 * 60,
    )

    assert requeued == 1
    assert store.get_job("job-1").status is JobStatus.QUEUED
    assert governor_queue.pending(DEST) == ["job-1"]
    # Read raw: local_in_flight clamps at zero and would hide a counter driven to -1.
    assert redis.hgetall(governor_queue._in_flight_key(DEST)) == {}


class _ClaimOnlyWorker:
    def __init__(self, queue: RedisMaintenanceQueue) -> None:
        self.queue = queue
        self.claimed: list[str] = []

    def process_next(self) -> None:
        job_id = self.queue.claim_admitted()
        if job_id is not None:
            self.claimed.append(job_id)
            self.queue.mark_running(job_id)


def test_the_worker_daemon_keeps_ticking_cleanly_across_a_redis_restart(redis, monkeypatch):
    """Every tick used to fail with NOGROUP after the restart; five in a row turned /healthz
    unhealthy, and the liveness probe restarted every worker at once."""
    monkeypatch.setattr("cryo.worker.orphan_recovery.recover_orphaned_jobs", lambda **_: None)
    store = _store("job-1")
    locks = InMemoryLockStore()
    worker_queue = RedisMaintenanceQueue(redis, consumer="worker-1")
    # Long-lived like the real governor, so it too has cached that the group exists.
    governor_queue = RedisMaintenanceQueue(redis, consumer="governor")
    runner = WorkerRunner(interval=0)
    runner._wiring = SimpleNamespace(job_store=store, queue=worker_queue, lock_store=locks)
    worker = _ClaimOnlyWorker(worker_queue)
    runner._worker = worker

    def admit_after_restart() -> None:
        _add_jobs(store, "job-2")
        _admit_through_governor(governor_queue, store, locks, "job-2")

    _admit_through_governor(governor_queue, store, locks, "job-1")
    ticks: list[Any] = [lambda: None, redis.flushall, admit_after_restart]
    failures_seen: list[int] = []

    def between_ticks(timeout: float | None = None) -> bool:
        del timeout
        failures_seen.append(runner.consecutive_tick_failures)
        if ticks:
            ticks.pop(0)()
        else:
            runner._stop.set()
        return runner._stop.is_set()

    runner._stop.wait = between_ticks  # type: ignore[method-assign]
    runner.run()

    assert failures_seen == [0, 0, 0, 0]
    assert worker.claimed == ["job-1", "job-2"]


def test_the_commit_event_consumer_survives_its_stream_vanishing_mid_read(redis, monkeypatch):
    stream = RedisEventStream(redis)
    stream.publish("cryo:table-commits", {"n": 1})
    real_read = redis.xreadgroup
    lost = []

    def lose_redis_once(*args, **kwargs):
        if not lost:
            lost.append(True)
            redis.flushall()
            stream.publish("cryo:table-commits", {"n": 2})
        return real_read(*args, **kwargs)

    monkeypatch.setattr(redis, "xreadgroup", lose_redis_once)

    messages = stream.read_group("cryo:table-commits", "cryo-events", "consumer-1")

    assert [payload for _, payload in messages] == [{"n": 2}]
