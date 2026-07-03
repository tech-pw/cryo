from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from cryo.domain.enums import JobStatus


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

    def mark_running(self, job_id: str) -> None: ...

    def complete(self, job_id: str, status: JobStatus | str) -> None: ...

    def defer(self, job_id: str) -> None: ...

    def local_in_flight(self, destination_id: str) -> int: ...

    def local_in_flight_by_replica(self, destination_id: str) -> dict[str, int]: ...

    def status(self, job_id: str) -> JobStatus | None: ...


class InMemoryMaintenanceQueue:
    """Deterministic queue used by unit tests and local orchestration wiring."""

    def __init__(self) -> None:
        self._pending: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        self._admitted: deque[str] = deque()
        self._entries: dict[str, QueueEntry] = {}
        self._statuses: dict[str, JobStatus] = {}
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

    def mark_running(self, job_id: str) -> None:
        self._statuses[job_id] = JobStatus.RUNNING

    def complete(self, job_id: str, status: JobStatus | str) -> None:
        self._statuses[job_id] = JobStatus(status)

    def defer(self, job_id: str) -> None:
        entry = self._entries.get(job_id)
        if entry:
            self._remove_pending(job_id, entry.destination_id)
        self._statuses[job_id] = JobStatus.DEFERRED

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

    def local_in_flight(self, destination_id: str) -> int:
        return sum(self.local_in_flight_by_replica(destination_id).values())

    def local_in_flight_by_replica(self, destination_id: str) -> dict[str, int]:
        values: Mapping[Any, Any] = self._redis.hgetall(self._in_flight_key(destination_id))
        return {_decode(replica): max(0, int(count)) for replica, count in values.items()}

    def status(self, job_id: str) -> JobStatus | None:
        value = self._redis.hget(self._job_key(job_id), "status")
        return JobStatus(_decode(value)) if value else None

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


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
