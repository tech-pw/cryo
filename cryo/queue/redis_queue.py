from __future__ import annotations

import logging
import os
import socket
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from cryo.domain.enums import JobStatus

logger = logging.getLogger(__name__)

# Consumer group that every worker pod joins to claim admitted jobs.
_ADMITTED_GROUP = "workers"

# How long an entry may sit unacknowledged in the group's pending list before another worker
# may take it over. This window only ever covers the claim -> mark_running handoff, because
# mark_running acknowledges the entry (see RedisMaintenanceQueue.mark_running); everything
# after RUNNING belongs to reconcile_stale_running_jobs and orphan recovery. That split is
# what lets this be measured in minutes rather than having to exceed the longest possible
# maintenance operation.
_CLAIM_HANDOFF_IDLE_MS = 120_000

# Bound on how many already-delivered entries one claim_admitted() call will step over while
# looking for work. Reclaimed entries whose job has since gone terminal are acknowledged and
# skipped; this stops a large pending list from turning a single poll into an unbounded loop.
_MAX_RECLAIM_PROBES = 20

# Every cryo:job:<id> hash carries an expiry, because nothing else ever removes one. Before
# this, complete() set the hash's status field to a terminal value and left the key behind
# forever: 2026-09-23 prod held 22,552 of them, and Redis grew 17.1 MB/day against a 512Mi
# container limit -- roughly a month from an empty start to being OOMKilled by the kernel.
# Because maxmemory was 0, Redis never pushed back, and /data was the node's ephemeral disk,
# so the kill took the RDB snapshot with it.
#
# Two horizons rather than one:
#
# _ACTIVE_JOB_TTL_SEC is a safety net refreshed on every state transition, so a job stays
# addressable for as long as it is genuinely in play. It has to clear the worst legitimate
# case, which is queue time, not run time -- reconcile_stale_running_jobs reaps a RUNNING job
# after 6h, but a QUEUED job can wait far longer behind a backlog (3,046 jobs once sat queued
# for 21+ hours). A week is comfortably past that while still bounding the leak.
#
# _TERMINAL_JOB_TTL_SEC applies once complete() runs and no further transition can occur. It
# only has to outlive readers already holding the id, so it is short -- this is what keeps the
# steady state small, since completions are the overwhelming majority of keys.
#
# Expiry is safe to be wrong in the "too early" direction: Postgres is the source of truth and
# both recovery paths rebuild from it. reconcile_queued_jobs re-enqueues a DB-QUEUED job that
# is missing from Redis, and orphan recovery re-admits a DB-ADMITTED job whose hash is gone
# (worker/orphan_recovery.py, `if queue.status(job.id) is None`). Neither inspects a job that
# is already terminal in Postgres, which is the only kind the short TTL can reach.
_ACTIVE_JOB_TTL_SEC = 7 * 24 * 60 * 60
_TERMINAL_JOB_TTL_SEC = 60 * 60


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

    def __init__(self, consumer: str = "in-memory") -> None:
        self._pending: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        self._admitted: deque[str] = deque()
        # Mirrors a consumer group's pending-entries list: claimed but not yet acknowledged,
        # with the claim time so a stalled handoff can be taken over.
        self._claimed: dict[str, tuple[str, datetime]] = {}
        self._consumers: dict[str, datetime] = {}
        self._consumer = consumer
        self._entries: dict[str, QueueEntry] = {}
        self._statuses: dict[str, JobStatus] = {}
        self._sequence = 0

    def enqueue(self, job_id: str, destination_id: str, priority: int = 0) -> None:
        from cryo.maintenance.writes import ensure_maintenance_writes_enabled

        ensure_maintenance_writes_enabled()
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
        now = datetime.now(UTC)
        self._consumers[self._consumer] = now
        stalled_cutoff = now - timedelta(milliseconds=_CLAIM_HANDOFF_IDLE_MS)
        for job_id, (owner, claimed_at) in sorted(
            self._claimed.items(), key=lambda item: item[1][1]
        ):
            if owner == self._consumer or claimed_at > stalled_cutoff:
                continue
            if self._statuses.get(job_id) is not JobStatus.ADMITTED:
                del self._claimed[job_id]
                continue
            self._claimed[job_id] = (self._consumer, now)
            return job_id
        while self._admitted:
            job_id = self._admitted.popleft()
            if self._statuses.get(job_id) is JobStatus.ADMITTED:
                self._claimed[job_id] = (self._consumer, now)
                return job_id
        return None

    def requeue_for_continuation(self, job_id: str) -> None:
        self._statuses[job_id] = JobStatus.ADMITTED
        self._claimed.pop(job_id, None)
        self._admitted.append(job_id)

    def awaiting_claim(self, job_id: str) -> bool:
        return job_id in self._admitted or job_id in self._claimed

    def admitted_depth(self) -> int:
        return len(self._admitted)

    def return_to_pending(self, job_id: str, destination_id: str, priority: int = 0) -> None:
        self._admitted = deque(other for other in self._admitted if other != job_id)
        self._claimed.pop(job_id, None)
        self.enqueue(job_id, destination_id, priority)

    def active_worker_count(self, stale_after_seconds: int = 60) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_seconds)
        busy = {owner for owner, _ in self._claimed.values()}
        return sum(
            1 for consumer, seen in self._consumers.items() if seen >= cutoff or consumer in busy
        )

    def mark_running(self, job_id: str) -> None:
        self._statuses[job_id] = JobStatus.RUNNING
        self._claimed.pop(job_id, None)

    def complete(self, job_id: str, status: JobStatus | str) -> None:
        self._statuses[job_id] = JobStatus(status)
        self._claimed.pop(job_id, None)

    def defer(self, job_id: str) -> None:
        entry = self._entries.get(job_id)
        if entry:
            self._remove_pending(job_id, entry.destination_id)
        self._statuses[job_id] = JobStatus.DEFERRED
        self._claimed.pop(job_id, None)

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
    """Redis-backed queue and local in-flight counter for the scheduler/governor/worker path.

    Admitted jobs travel over a Redis Streams consumer group rather than a plain list. The
    list handoff (``RPUSH cryo:admitted`` / ``LPOP``) was at-most-once: once a worker popped a
    job id, the only record of it was that worker's memory, so a pod dying before it could
    write RUNNING to Postgres dropped the job permanently -- the row stayed ADMITTED with no
    engine_ref and nothing would ever pick it up again. A consumer group keeps the entry in
    its pending list until it is acknowledged, so the same crash is just a redelivery.

    Acknowledgement happens at ``mark_running``, not at completion. That keeps the pending
    list covering exactly the claim -> RUNNING handoff, which gives the two recovery paths
    non-overlapping ownership: the consumer group recovers jobs lost before they started, and
    reconcile_stale_running_jobs / orphan recovery own everything after. It also means the
    undelivered depth of the stream is precisely "admitted work no worker has started yet",
    which is the backpressure signal the governor needs.
    """

    def __init__(
        self, redis_client: Any, namespace: str = "cryo", consumer: str | None = None
    ) -> None:
        self._redis = redis_client
        self._namespace = namespace
        # The pod name in Kubernetes, so a restarted container rejoins as itself and picks its
        # own unacknowledged entries back up instead of waiting to be reclaimed.
        self._consumer = consumer or f"{socket.gethostname()}-{os.getpid()}"
        self._group_ready = False

    def enqueue(self, job_id: str, destination_id: str, priority: int = 0) -> None:
        from cryo.maintenance.writes import ensure_maintenance_writes_enabled

        ensure_maintenance_writes_enabled()
        pipe = self._redis.pipeline()
        pipe.hset(
            self._job_key(job_id), mapping={"destination_id": destination_id, "status": "queued"}
        )
        pipe.expire(self._job_key(job_id), _ACTIVE_JOB_TTL_SEC)
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
        entry_id = _decode(self._redis.xadd(self._stream_key(), {"job_id": job_id}))
        pipe = self._redis.pipeline()
        pipe.zrem(self._pending_key(destination_id), job_id)
        pipe.hset(
            self._job_key(job_id),
            mapping={
                "destination_id": destination_id,
                "status": "admitted",
                "replica": replica_name,
                "stream_id": entry_id,
            },
        )
        pipe.expire(self._job_key(job_id), _ACTIVE_JOB_TTL_SEC)
        pipe.hincrby(self._in_flight_key(destination_id), replica_name, 1)
        pipe.execute()

    def claim_admitted(self) -> str | None:
        """Take ownership of one admitted job, preferring a stalled handoff over new work.

        Stalled entries come first so a job dropped by a dying worker is retried ahead of
        jobs that have never been attempted -- it has already waited longest, and leaving it
        behind a full backlog is what turned a crash into a permanently stuck job before.
        """
        self._ensure_group()
        for _ in range(_MAX_RECLAIM_PROBES):
            reclaimed = self._reclaim_stalled()
            if reclaimed is None:
                break
            entry_id, job_id = reclaimed
            if job_id and self.status(job_id) is JobStatus.ADMITTED:
                return job_id
            # The job finished under its original owner (or was cancelled) and only the
            # unacknowledged entry outlived it. Drop the entry by its own id rather than by
            # looking it up on the job hash, which a completed job no longer carries --
            # otherwise the entry stays pending and every poll reclaims it again.
            self._drop_entry(entry_id)
        return self._read_new()

    def _read_new(self) -> str | None:
        response = self._redis.xreadgroup(
            _ADMITTED_GROUP, self._consumer, {self._stream_key(): ">"}, count=1
        )
        for _, entries in response or []:
            for _, fields in entries:
                return _decode(fields.get(b"job_id") or fields.get("job_id"))
        return None

    def _reclaim_stalled(self) -> tuple[str, str] | None:
        """Take over one entry whose owner has gone quiet. Returns (entry_id, job_id)."""
        response = self._redis.xautoclaim(
            self._stream_key(),
            _ADMITTED_GROUP,
            self._consumer,
            min_idle_time=_CLAIM_HANDOFF_IDLE_MS,
            start_id="0-0",
            count=1,
        )
        # XAUTOCLAIM answers (next_cursor, claimed_entries, deleted_ids).
        entries = response[1] if response and len(response) > 1 else []
        for entry_id, fields in entries or []:
            job_id = _decode(fields.get(b"job_id") or fields.get("job_id"))
            if job_id:
                logger.warning(
                    "Reclaimed admitted job %s: its previous worker never started it", job_id
                )
            return _decode(entry_id), job_id
        return None

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
        self._ack(job_id)
        entry_id = _decode(self._redis.xadd(self._stream_key(), {"job_id": job_id}))
        pipe = self._redis.pipeline()
        pipe.hset(self._job_key(job_id), mapping={"status": "admitted", "stream_id": entry_id})
        pipe.expire(self._job_key(job_id), _ACTIVE_JOB_TTL_SEC)
        pipe.execute()

    def awaiting_claim(self, job_id: str) -> bool:
        """True while the stream still holds this job's admission entry.

        Entries are removed at mark_running, so this is an exact answer to "is this job still
        somewhere between admitted and started" -- undelivered, or claimed by a worker that
        has not begun it yet. An ADMITTED row that answers False here is one whose entry was
        lost outright (a Redis flush, essentially), which is the only case
        reconcile_stuck_admitted_jobs still has to repair.
        """
        entry_id = _decode(self._redis.hget(self._job_key(job_id), "stream_id"))
        if not entry_id:
            return False
        entries = self._redis.xrange(self._stream_key(), min=entry_id, max=entry_id, count=1)
        return bool(entries)

    def admitted_depth(self) -> int:
        """Admitted jobs that no worker has started yet.

        Stream length counts entries not yet acknowledged (i.e. not yet RUNNING); subtracting
        the group's pending count removes the ones a worker already holds. What remains is the
        queue the governor is deciding whether to add to.
        """
        total = int(self._redis.xlen(self._stream_key()) or 0)
        return max(0, total - self._group_pending())

    def return_to_pending(self, job_id: str, destination_id: str, priority: int = 0) -> None:
        """Move an admitted-but-undispatched job back to its destination's pending zset.

        Releases the in-flight slot the admission took, drops any stale stream entry, and
        restores QUEUED so the next governor wave can admit it again. Used instead of failing
        a job outright when its dispatch was lost rather than attempted.
        """
        self._ack(job_id)
        pipe = self._redis.pipeline()
        pipe.hincrby(self._in_flight_key(destination_id), self._replica_of(job_id), -1)
        pipe.hset(
            self._job_key(job_id),
            mapping={"destination_id": destination_id, "status": "queued"},
        )
        pipe.expire(self._job_key(job_id), _ACTIVE_JOB_TTL_SEC)
        pipe.zadd(self._pending_key(destination_id), {job_id: priority})
        pipe.sadd(self._destinations_key(), destination_id)
        pipe.execute()

    def _replica_of(self, job_id: str) -> str:
        entry = self._redis.hgetall(self._job_key(job_id))
        return _decode(entry.get(b"replica") or entry.get("replica")) or "default"

    def active_worker_count(self, stale_after_seconds: int = 60) -> int:
        """Workers currently attached to the consumer group.

        A worker counts as alive if it polled recently or is holding an unacknowledged entry.
        The second clause is why this replaced an explicit heartbeat: a worker runs one job at
        a time and does not poll again until it finishes, so presence measured by polling
        alone reported zero exactly when the fleet was busiest -- which made the governor's
        drain-rate cap silently inert under the load it exists to handle.
        """
        self._ensure_group()
        try:
            consumers = self._redis.xinfo_consumers(self._stream_key(), _ADMITTED_GROUP)
        except Exception:  # noqa: BLE001 - missing stream/group means no workers yet
            return 0
        stale_after_ms = stale_after_seconds * 1000
        alive = 0
        for consumer in consumers or []:
            pending = int(_field(consumer, "pending") or 0)
            # `inactive` (Redis 7.2+) is time since the last command; `idle` is the 7.0 name.
            inactive = _field(consumer, "inactive")
            if inactive is None:
                inactive = _field(consumer, "idle")
            if pending > 0 or int(inactive or 0) <= stale_after_ms:
                alive += 1
        return alive

    def mark_running(self, job_id: str) -> None:
        pipe = self._redis.pipeline()
        pipe.hset(self._job_key(job_id), "status", "running")
        pipe.expire(self._job_key(job_id), _ACTIVE_JOB_TTL_SEC)
        pipe.execute()
        self._ack(job_id)

    def complete(self, job_id: str, status: JobStatus | str) -> None:
        entry = self._redis.hgetall(self._job_key(job_id))
        destination_id = _decode(entry.get(b"destination_id") or entry.get("destination_id"))
        replica = _decode(entry.get(b"replica") or entry.get("replica")) or "default"
        self._ack(job_id)
        pipe = self._redis.pipeline()
        pipe.hset(self._job_key(job_id), "status", JobStatus(status).value)
        # The one transition after which no further transition can happen, so this is where
        # the long active TTL is traded down for the short terminal one.
        pipe.expire(self._job_key(job_id), _TERMINAL_JOB_TTL_SEC)
        if destination_id:
            pipe.hincrby(self._in_flight_key(destination_id), replica, -1)
        pipe.execute()

    def defer(self, job_id: str) -> None:
        entry = self._redis.hgetall(self._job_key(job_id))
        destination_id = _decode(entry.get(b"destination_id") or entry.get("destination_id"))
        self._ack(job_id)
        pipe = self._redis.pipeline()
        if destination_id:
            pipe.zrem(self._pending_key(destination_id), job_id)
        pipe.hset(self._job_key(job_id), "status", "deferred")
        # Deferred is not terminal -- the job comes back on a later wave, so it keeps the
        # active horizon rather than the short one complete() applies.
        pipe.expire(self._job_key(job_id), _ACTIVE_JOB_TTL_SEC)
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

    def _ack(self, job_id: str) -> None:
        """Acknowledge and delete this job's stream entry, if it still has one.

        Deleting rather than only acknowledging is what makes stream length mean "admitted but
        not yet started"; see admitted_depth.
        """
        entry_id = _decode(self._redis.hget(self._job_key(job_id), "stream_id"))
        if not entry_id:
            return
        pipe = self._redis.pipeline()
        pipe.xack(self._stream_key(), _ADMITTED_GROUP, entry_id)
        pipe.xdel(self._stream_key(), entry_id)
        pipe.hdel(self._job_key(job_id), "stream_id")
        pipe.execute()

    def _drop_entry(self, entry_id: str) -> None:
        """Acknowledge and delete one stream entry by its own id, with no job hash involved."""
        if not entry_id:
            return
        pipe = self._redis.pipeline()
        pipe.xack(self._stream_key(), _ADMITTED_GROUP, entry_id)
        pipe.xdel(self._stream_key(), entry_id)
        pipe.execute()

    def _ensure_group(self) -> None:
        if self._group_ready:
            return
        try:
            self._redis.xgroup_create(self._stream_key(), _ADMITTED_GROUP, id="0", mkstream=True)
        except Exception as exc:  # noqa: BLE001 - BUSYGROUP just means someone got there first
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    def _group_pending(self) -> int:
        self._ensure_group()
        try:
            groups = self._redis.xinfo_groups(self._stream_key())
        except Exception:  # noqa: BLE001 - no stream yet
            return 0
        for group in groups or []:
            if _decode(_field(group, "name")) == _ADMITTED_GROUP:
                return int(_field(group, "pending") or 0)
        return 0

    def _job_key(self, job_id: str) -> str:
        return f"{self._namespace}:job:{job_id}"

    def _pending_key(self, destination_id: str) -> str:
        return f"{self._namespace}:pending:{destination_id}"

    def _stream_key(self) -> str:
        return f"{self._namespace}:admitted:stream"

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


def _field(mapping: Any, name: str) -> Any:
    """Read one field out of an XINFO reply, which may be keyed by str or bytes."""
    if not isinstance(mapping, Mapping):
        return None
    if name in mapping:
        return mapping[name]
    return mapping.get(name.encode("utf-8"))
