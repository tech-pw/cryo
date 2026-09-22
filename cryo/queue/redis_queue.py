from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from cryo.domain.enums import JobStatus

# Worker heartbeats older than this are dropped from the presence set entirely. Generous
# relative to the liveness window callers actually ask about (active_worker_count's
# stale_after_seconds) so a brief Redis or scheduling hiccup doesn't erase a live pod.
_WORKER_HEARTBEAT_RETENTION_SEC = 600


@dataclass(frozen=True)
class QueueEntry:
    job_id: str
    destination_id: str
    priority: int = 0
    replica: str | None = None


class MaintenanceQueue(Protocol):
    def enqueue(self, job_id: str, destination_id: str, priority: int = 0) -> None: ...

    def pending(self, destination_id: str) -> list[str]: ...

    def pending_by_destination(self) -> dict[str, list[str]]: ...

    def admit(self, job_id: str, destination_id: str, replica: str | None = None) -> None: ...

    def claim_admitted(self) -> str | None: ...

    def requeue_for_continuation(self, job_id: str) -> None: ...

    def awaiting_claim(self, job_id: str) -> bool: ...

    def admitted_depth(self) -> int: ...

    def return_to_pending(self, job_id: str, destination_id: str, priority: int = 0) -> None: ...

    def record_worker_heartbeat(self, worker_id: str) -> None: ...

    def active_worker_count(self, stale_after_seconds: int = 60) -> int: ...

    def mark_running(self, job_id: str) -> None: ...

    def complete(self, job_id: str, status: JobStatus | str) -> None: ...

    def defer(self, job_id: str) -> None: ...

    def cancel_pending(self, job_id: str, destination_id: str | None = None) -> None: ...

    def local_in_flight(self, destination_id: str) -> int: ...

    def local_in_flight_by_replica(self, destination_id: str) -> dict[str, int]: ...

    def status(self, job_id: str) -> JobStatus | None: ...

    def is_pending(self, job_id: str, destination_id: str) -> bool: ...


class InMemoryMaintenanceQueue:
    """Deterministic queue used by unit tests and local orchestration wiring."""

    def __init__(self) -> None:
        self._pending: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        self._admitted: deque[str] = deque()
        self._entries: dict[str, QueueEntry] = {}
        self._statuses: dict[str, JobStatus] = {}
        self._worker_heartbeats: dict[str, datetime] = {}
        self._sequence = 0

    def enqueue(self, job_id: str, destination_id: str, priority: int = 0) -> None:
        self._sequence += 1
        self._entries[job_id] = QueueEntry(job_id, destination_id, priority)
        self._statuses[job_id] = JobStatus.QUEUED
        self._pending[destination_id].append((-priority, self._sequence, job_id))
        self._pending[destination_id].sort()

    def pending(self, destination_id: str) -> list[str]:
        return [job_id for _, _, job_id in self._pending.get(destination_id, [])]

    def pending_by_destination(self) -> dict[str, list[str]]:
        return {
            destination_id: self.pending(destination_id)
            for destination_id, entries in self._pending.items()
            if entries
        }

    def admit(self, job_id: str, destination_id: str, replica: str | None = None) -> None:
        self._remove_pending(job_id, destination_id)
        old_entry = self._entries.get(job_id, QueueEntry(job_id, destination_id))
        self._entries[job_id] = QueueEntry(
            job_id=job_id,
            destination_id=destination_id,
            priority=old_entry.priority,
            replica=replica,
        )
        self._statuses[job_id] = JobStatus.ADMITTED
        self._admitted.append(job_id)

    def claim_admitted(self) -> str | None:
        while self._admitted:
            job_id = self._admitted.popleft()
            if self._statuses.get(job_id) is JobStatus.ADMITTED:
                return job_id
        return None

    def requeue_for_continuation(self, job_id: str) -> None:
        self._statuses[job_id] = JobStatus.ADMITTED
        self._admitted.append(job_id)

    def awaiting_claim(self, job_id: str) -> bool:
        return job_id in self._admitted

    def admitted_depth(self) -> int:
        return len(self._admitted)

    def return_to_pending(self, job_id: str, destination_id: str, priority: int = 0) -> None:
        self._admitted = deque(other for other in self._admitted if other != job_id)
        self.enqueue(job_id, destination_id, priority)

    def record_worker_heartbeat(self, worker_id: str) -> None:
        self._worker_heartbeats[worker_id] = datetime.now(UTC)

    def active_worker_count(self, stale_after_seconds: int = 60) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        return sum(1 for seen in self._worker_heartbeats.values() if seen >= cutoff)

    def mark_running(self, job_id: str) -> None:
        self._statuses[job_id] = JobStatus.RUNNING

    def complete(self, job_id: str, status: JobStatus | str) -> None:
        self._statuses[job_id] = JobStatus(status)

    def defer(self, job_id: str) -> None:
        entry = self._entries.get(job_id)
        if entry:
            self._remove_pending(job_id, entry.destination_id)
        self._statuses[job_id] = JobStatus.DEFERRED

    def cancel_pending(self, job_id: str, destination_id: str | None = None) -> None:
        if destination_id is not None:
            self._remove_pending(job_id, destination_id)
            return
        entry = self._entries.get(job_id)
        if entry:
            self._remove_pending(job_id, entry.destination_id)
            return
        for other_destination_id in list(self._pending):
            self._remove_pending(job_id, other_destination_id)

    def local_in_flight(self, destination_id: str) -> int:
        return sum(
            1
            for job_id, entry in self._entries.items()
            if entry.destination_id == destination_id
            and self._statuses.get(job_id) in {JobStatus.ADMITTED, JobStatus.RUNNING}
        )

    def local_in_flight_by_replica(self, destination_id: str) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for job_id, entry in self._entries.items():
            if entry.destination_id != destination_id:
                continue
            if self._statuses.get(job_id) in {JobStatus.ADMITTED, JobStatus.RUNNING}:
                counts[entry.replica or "default"] += 1
        return dict(counts)

    def status(self, job_id: str) -> JobStatus | None:
        return self._statuses.get(job_id)

    def is_pending(self, job_id: str, destination_id: str) -> bool:
        return any(entry[2] == job_id for entry in self._pending.get(destination_id, []))

    def entry(self, job_id: str) -> QueueEntry | None:
        return self._entries.get(job_id)

    def _remove_pending(self, job_id: str, destination_id: str) -> None:
        self._pending[destination_id] = [
            entry for entry in self._pending.get(destination_id, []) if entry[2] != job_id
        ]


class RedisMaintenanceQueue:
    """Redis-backed queue and local in-flight counter for the scheduler/governor/worker path."""

    def __init__(self, redis_client: Any, namespace: str = "cryo") -> None:
        self._redis = redis_client
        self._namespace = namespace

    def enqueue(self, job_id: str, destination_id: str, priority: int = 0) -> None:
        pipe = self._redis.pipeline()
        pipe.hset(
            self._job_key(job_id), mapping={"destination_id": destination_id, "status": "queued"}
        )
        pipe.zadd(self._pending_key(destination_id), {job_id: priority})
        pipe.sadd(self._destinations_key(), destination_id)
        pipe.execute()

    def pending(self, destination_id: str) -> list[str]:
        values = self._redis.zrevrange(self._pending_key(destination_id), 0, -1)
        return [_decode(value) for value in values]

    def pending_by_destination(self) -> dict[str, list[str]]:
        destinations = [_decode(value) for value in self._redis.smembers(self._destinations_key())]
        return {
            destination_id: pending
            for destination_id in destinations
            if (pending := self.pending(destination_id))
        }

    def admit(self, job_id: str, destination_id: str, replica: str | None = None) -> None:
        replica_name = replica or "default"
        pipe = self._redis.pipeline()
        pipe.zrem(self._pending_key(destination_id), job_id)
        pipe.hset(
            self._job_key(job_id),
            mapping={
                "destination_id": destination_id,
                "status": "admitted",
                "replica": replica_name,
            },
        )
        pipe.rpush(self._admitted_key(), job_id)
        pipe.hincrby(self._in_flight_key(destination_id), replica_name, 1)
        pipe.execute()

    def claim_admitted(self) -> str | None:
        value = self._redis.lpop(self._admitted_key())
        return _decode(value) if value is not None else None

    def requeue_for_continuation(self, job_id: str) -> None:
        """Hand an already-admitted RUNNING job back to claim_admitted() for another dispatch
        pass, without touching its in-flight slot (it never released one -- this isn't a new
        admission, just re-entering the same job into the claim queue). Used by orphan recovery
        after resolving an adopted job's interrupted step: process_next()'s own statement loop
        rebuilds the plan and skips any step already SUCCEEDED/SKIPPED in the ledger, so handing
        the job back here either dispatches its next operation or -- if that was the last one --
        correctly finalizes it as SUCCEEDED. Regression, 2026-07-29: without this, a job whose
        owning worker died mid-step sat stuck forever once orphan recovery resolved that one
        step; nothing ever dispatched the remaining operations."""
        pipe = self._redis.pipeline()
        pipe.hset(self._job_key(job_id), "status", "admitted")
        pipe.rpush(self._admitted_key(), job_id)
        pipe.execute()

    def awaiting_claim(self, job_id: str) -> bool:
        """True while job_id is still sitting in the claim list waiting for a free worker.

        This is the discriminator reconcile_stuck_admitted_jobs needs to tell the two very
        different ADMITTED-with-no-engine_ref cases apart: a job dropped by the at-most-once
        lpop handoff (gone from the list, nothing will ever pick it up) versus a job that is
        simply behind a long queue (still on the list, a worker will reach it). Before this
        existed the reaper could not distinguish them and failed both after 15 minutes.
        """
        try:
            position = self._redis.lpos(self._admitted_key(), job_id)
        except AttributeError:
            # redis-py < 4.0 / a client stub without LPOS. Fall back to scanning the list;
            # correctness matters more here than the O(n), and the reaper runs once a wave.
            values = self._redis.lrange(self._admitted_key(), 0, -1)
            return any(_decode(value) == job_id for value in values)
        return position is not None

    def admitted_depth(self) -> int:
        return int(self._redis.llen(self._admitted_key()) or 0)

    def return_to_pending(self, job_id: str, destination_id: str, priority: int = 0) -> None:
        """Move an admitted-but-undispatched job back to its destination's pending zset.

        Releases the in-flight slot the admission took, drops any stale claim-list entry, and
        restores QUEUED so the next governor wave can admit it again. Used instead of failing
        a job outright when its dispatch was lost rather than attempted.
        """
        pipe = self._redis.pipeline()
        pipe.lrem(self._admitted_key(), 0, job_id)
        pipe.hincrby(self._in_flight_key(destination_id), self._replica_of(job_id), -1)
        pipe.hset(
            self._job_key(job_id),
            mapping={"destination_id": destination_id, "status": "queued"},
        )
        pipe.zadd(self._pending_key(destination_id), {job_id: priority})
        pipe.sadd(self._destinations_key(), destination_id)
        pipe.execute()

    def _replica_of(self, job_id: str) -> str:
        entry = self._redis.hgetall(self._job_key(job_id))
        return _decode(entry.get(b"replica") or entry.get("replica")) or "default"

    def record_worker_heartbeat(self, worker_id: str) -> None:
        """Announce that this worker pod is alive and polling.

        The governor has no other way to know how many workers exist: it probes ENGINE load
        (Trino queries, Spark Jobs) which says nothing about how fast the claim queue drains,
        and workers process one job at a time. Without a live worker count the governor
        admits against engine capacity alone and buries the claim list under work no worker
        can reach. A sorted set keyed by last-seen timestamp keeps the read O(log n) and lets
        dead pods age out on their own -- no deregistration path to get wrong on a hard kill.
        """
        now = datetime.now(UTC).timestamp()
        pipe = self._redis.pipeline()
        pipe.zadd(self._workers_key(), {worker_id: now})
        pipe.zremrangebyscore(self._workers_key(), "-inf", now - _WORKER_HEARTBEAT_RETENTION_SEC)
        pipe.execute()

    def active_worker_count(self, stale_after_seconds: int = 60) -> int:
        cutoff = datetime.now(UTC).timestamp() - stale_after_seconds
        return int(self._redis.zcount(self._workers_key(), cutoff, "+inf") or 0)

    def _workers_key(self) -> str:
        return f"{self._namespace}:workers"

    def mark_running(self, job_id: str) -> None:
        self._redis.hset(self._job_key(job_id), "status", "running")

    def complete(self, job_id: str, status: JobStatus | str) -> None:
        entry = self._redis.hgetall(self._job_key(job_id))
        destination_id = _decode(entry.get(b"destination_id") or entry.get("destination_id"))
        replica = _decode(entry.get(b"replica") or entry.get("replica")) or "default"
        pipe = self._redis.pipeline()
        pipe.hset(self._job_key(job_id), "status", JobStatus(status).value)
        if destination_id:
            pipe.hincrby(self._in_flight_key(destination_id), replica, -1)
        pipe.execute()

    def defer(self, job_id: str) -> None:
        entry = self._redis.hgetall(self._job_key(job_id))
        destination_id = _decode(entry.get(b"destination_id") or entry.get("destination_id"))
        pipe = self._redis.pipeline()
        if destination_id:
            pipe.zrem(self._pending_key(destination_id), job_id)
        pipe.hset(self._job_key(job_id), "status", "deferred")
        pipe.execute()

    def cancel_pending(self, job_id: str, destination_id: str | None = None) -> None:
        """Remove a pending queue entry.

        Prefer the explicit `destination_id` when the caller already knows which pending
        zset the entry lives in (e.g. iterating `pending_by_destination()`). Falling back to
        the job hash's recorded `destination_id` is unreliable: a corrupt/legacy entry (job
        hash missing, or pointing at a different destination than the zset it's actually
        stored under) makes that lookup a no-op, so the entry never gets removed and
        `purge_stale_queue_entries`/governor orphan-cleanup rediscover -- and re-log -- the
        same entries forever. Seen in production: 73 stale `job_id="None"` entries surviving
        every governor wave because their job hash pointed at the wrong destination.
        """
        pipe = self._redis.pipeline()
        if destination_id:
            pipe.zrem(self._pending_key(destination_id), job_id)
        else:
            entry = self._redis.hgetall(self._job_key(job_id))
            entry_destination_id = _decode(
                entry.get(b"destination_id") or entry.get("destination_id")
            )
            if entry_destination_id:
                pipe.zrem(self._pending_key(entry_destination_id), job_id)
            else:
                for value in self._redis.smembers(self._destinations_key()):
                    pipe.zrem(self._pending_key(_decode(value)), job_id)
        pipe.execute()

    def local_in_flight(self, destination_id: str) -> int:
        return sum(self.local_in_flight_by_replica(destination_id).values())

    def local_in_flight_by_replica(self, destination_id: str) -> dict[str, int]:
        values: Mapping[Any, Any] = self._redis.hgetall(self._in_flight_key(destination_id))
        return {_decode(replica): max(0, int(count)) for replica, count in values.items()}

    def status(self, job_id: str) -> JobStatus | None:
        value = self._redis.hget(self._job_key(job_id), "status")
        return JobStatus(_decode(value)) if value else None

    def is_pending(self, job_id: str, destination_id: str) -> bool:
        """True only if job_id is actually present in destination_id's pending zset --
        the thing pending_by_destination() (and therefore the governor) actually reads.

        2026-09-07 fix: status() alone answers a different question ("what does the job
        hash's status field say") that can desync from this one. Confirmed live: 3,046
        jobs across 2 runs had status()=='queued' (the hash field was never touched) while
        genuinely absent from their destination's pending zset -- reconcile_queued_jobs
        trusted status() and treated them as "already active", so it never re-enqueued
        them; they sat invisible to every wave for 21+ hours despite the destination
        itself actively dispatching other runs' work the whole time. See
        reconcile_queued_jobs's own docstring for the incident this closes.
        """
        return self._redis.zscore(self._pending_key(destination_id), job_id) is not None

    def _job_key(self, job_id: str) -> str:
        return f"{self._namespace}:job:{job_id}"

    def _pending_key(self, destination_id: str) -> str:
        return f"{self._namespace}:pending:{destination_id}"

    def _admitted_key(self) -> str:
        return f"{self._namespace}:admitted"

    def _destinations_key(self) -> str:
        return f"{self._namespace}:destinations"

    def _in_flight_key(self, destination_id: str) -> str:
        return f"{self._namespace}:inflight:{destination_id}"


_cached_redis_client: Any = None


def get_redis_client(*, force: bool = False) -> Any:
    """Return a process-wide Redis client built from ``Settings.redis_url``.

    Lazy + cached; cheap to call repeatedly. Returns ``None`` if Redis isn't configured
    so callers can fail open rather than crash on a missing dependency.
    """
    global _cached_redis_client
    if force or _cached_redis_client is None:
        try:
            from cryo.config import get_settings
            from cryo.infra.wiring import _redis_client as _build
        except Exception:  # noqa: BLE001 - allow import-time partials
            return None
        try:
            _cached_redis_client = _build(get_settings().redis_url)
        except Exception:  # noqa: BLE001
            return None
    return _cached_redis_client


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
