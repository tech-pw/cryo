"""Registry of Spark-on-K8s jobs a worker submitted and moved on from (#435).

A parked job keeps its RUNNING status, its in-flight slot and its table lock. The registry
records which step is in flight and when the governor should next look at it. Three keys:

- ``state``: hash, job id -> JSON entry (step, Spark state, owner, job start).
- ``due``: sorted set, job id -> next check time. The resolver reads it oldest-due first
  with a limit, so a resolve tick is bounded however many jobs are parked.
- ``owner:<holder>``: set of the job ids one worker has in flight, for its cap.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

# A worker pod that goes away leaves its owner set behind; the TTL lets it age out.
_OWNER_SET_TTL_SEC = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class AsyncSparkEntry:
    job_id: str
    table_id: str
    destination_id: str
    step_index: int
    owner: str
    spark: dict[str, Any] = field(default_factory=dict)
    conflict_attempt: int = 0
    job_started_at: float = 0.0
    # When this step was parked; 0 for entries written before #491.
    parked_at: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | bytes) -> AsyncSparkEntry:
        payload = json.loads(raw)
        return cls(
            job_id=str(payload["job_id"]),
            table_id=str(payload["table_id"]),
            destination_id=str(payload["destination_id"]),
            step_index=int(payload["step_index"]),
            owner=str(payload.get("owner") or ""),
            spark=dict(payload.get("spark") or {}),
            conflict_attempt=int(payload.get("conflict_attempt") or 0),
            job_started_at=float(payload.get("job_started_at") or 0.0),
            parked_at=float(payload.get("parked_at") or 0.0),
        )


class AsyncSparkRegistry(Protocol):
    def park(self, entry: AsyncSparkEntry, *, next_check_at: float) -> None: ...

    def get(self, job_id: str) -> AsyncSparkEntry | None: ...

    def is_parked(self, job_id: str) -> bool: ...

    def clear(self, job_id: str) -> None: ...

    def owned_count(self, owner: str) -> int: ...

    def due(self, now: float, limit: int) -> list[AsyncSparkEntry]: ...

    def reschedule(self, job_id: str, next_check_at: float) -> None: ...

    def parked_count(self) -> int: ...

    def mark_checked(self, job_ids: list[str], at: float) -> None: ...

    def describe(self, job_ids: list[str]) -> dict[str, ParkedJob]: ...

    def overdue_count(self, due_before: float) -> int: ...


@dataclass(frozen=True, slots=True)
class ParkedJob:
    """What the UI shows for a parked job (#491)."""

    job_id: str
    step_index: int
    spark_ref: str | None
    parked_at: float | None
    last_checked_at: float | None
    next_check_at: float | None


class InMemoryAsyncSparkRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, AsyncSparkEntry] = {}
        self._due: dict[str, float] = {}
        self._checked: dict[str, float] = {}

    def mark_checked(self, job_ids: list[str], at: float) -> None:
        for job_id in job_ids:
            if job_id in self._entries:
                self._checked[job_id] = at

    def describe(self, job_ids: list[str]) -> dict[str, ParkedJob]:
        return {
            job_id: _parked_job(entry, self._checked.get(job_id), self._due.get(job_id))
            for job_id in job_ids
            if (entry := self._entries.get(job_id)) is not None
        }

    def overdue_count(self, due_before: float) -> int:
        return sum(1 for at in self._due.values() if at < due_before)

    def park(self, entry: AsyncSparkEntry, *, next_check_at: float) -> None:
        self._entries[entry.job_id] = entry
        self._due[entry.job_id] = next_check_at

    def get(self, job_id: str) -> AsyncSparkEntry | None:
        return self._entries.get(job_id)

    def is_parked(self, job_id: str) -> bool:
        return job_id in self._entries

    def clear(self, job_id: str) -> None:
        self._entries.pop(job_id, None)
        self._due.pop(job_id, None)
        self._checked.pop(job_id, None)

    def owned_count(self, owner: str) -> int:
        return sum(1 for entry in self._entries.values() if entry.owner == owner)

    def due(self, now: float, limit: int) -> list[AsyncSparkEntry]:
        ready = sorted((at, job_id) for job_id, at in self._due.items() if at <= now)
        return [self._entries[job_id] for _, job_id in ready[:limit]]

    def reschedule(self, job_id: str, next_check_at: float) -> None:
        if job_id in self._entries:
            self._due[job_id] = next_check_at

    def parked_count(self) -> int:
        return len(self._entries)


class RedisAsyncSparkRegistry:
    def __init__(self, redis_client: Any, namespace: str = "cryo") -> None:
        self._redis = redis_client
        self._prefix = f"{namespace}:async-spark"

    def park(self, entry: AsyncSparkEntry, *, next_check_at: float) -> None:
        previous = self.get(entry.job_id)
        pipe = self._redis.pipeline()
        if previous is not None and previous.owner != entry.owner:
            pipe.srem(self._owner_key(previous.owner), entry.job_id)
        pipe.hset(self._state_key(), entry.job_id, entry.to_json())
        pipe.zadd(self._due_key(), {entry.job_id: next_check_at})
        pipe.sadd(self._owner_key(entry.owner), entry.job_id)
        pipe.expire(self._owner_key(entry.owner), _OWNER_SET_TTL_SEC)
        pipe.execute()

    def get(self, job_id: str) -> AsyncSparkEntry | None:
        raw = self._redis.hget(self._state_key(), job_id)
        return AsyncSparkEntry.from_json(raw) if raw else None

    def is_parked(self, job_id: str) -> bool:
        return bool(self._redis.hexists(self._state_key(), job_id))

    def clear(self, job_id: str) -> None:
        previous = self.get(job_id)
        pipe = self._redis.pipeline()
        pipe.hdel(self._state_key(), job_id)
        pipe.hdel(self._checked_key(), job_id)
        pipe.zrem(self._due_key(), job_id)
        if previous is not None:
            pipe.srem(self._owner_key(previous.owner), job_id)
        pipe.execute()

    def owned_count(self, owner: str) -> int:
        return int(self._redis.scard(self._owner_key(owner)) or 0)

    def due(self, now: float, limit: int) -> list[AsyncSparkEntry]:
        job_ids = self._redis.zrangebyscore(self._due_key(), "-inf", now, start=0, num=limit)
        if not job_ids:
            return []
        raws = self._redis.hmget(self._state_key(), job_ids)
        entries: list[AsyncSparkEntry] = []
        for job_id, raw in zip(job_ids, raws, strict=True):
            if raw:
                entries.append(AsyncSparkEntry.from_json(raw))
            else:
                # A due marker with no state: the job was cleared between the two calls.
                self._redis.zrem(self._due_key(), job_id)
        return entries

    def reschedule(self, job_id: str, next_check_at: float) -> None:
        self._redis.zadd(self._due_key(), {job_id: next_check_at}, xx=True)

    def parked_count(self) -> int:
        return int(self._redis.hlen(self._state_key()) or 0)

    def mark_checked(self, job_ids: list[str], at: float) -> None:
        if job_ids:
            self._redis.hset(self._checked_key(), mapping={job_id: at for job_id in job_ids})

    def describe(self, job_ids: list[str]) -> dict[str, ParkedJob]:
        if not job_ids:
            return {}
        pipe = self._redis.pipeline()
        pipe.hmget(self._state_key(), job_ids)
        pipe.hmget(self._checked_key(), job_ids)
        for job_id in job_ids:
            pipe.zscore(self._due_key(), job_id)
        raws, checked, *due = pipe.execute()
        out: dict[str, ParkedJob] = {}
        for job_id, raw, checked_at, due_at in zip(job_ids, raws, checked, due, strict=True):
            if raw:
                out[job_id] = _parked_job(
                    AsyncSparkEntry.from_json(raw),
                    float(checked_at) if checked_at is not None else None,
                    float(due_at) if due_at is not None else None,
                )
        return out

    def overdue_count(self, due_before: float) -> int:
        return int(self._redis.zcount(self._due_key(), "-inf", f"({due_before}") or 0)

    def _checked_key(self) -> str:
        return f"{self._prefix}:checked"

    def _state_key(self) -> str:
        return f"{self._prefix}:state"

    def _due_key(self) -> str:
        return f"{self._prefix}:due"

    def _owner_key(self, owner: str) -> str:
        return f"{self._prefix}:owner:{owner}"


def _parked_job(
    entry: AsyncSparkEntry, checked_at: float | None, due_at: float | None
) -> ParkedJob:
    ref = entry.spark.get("ref")
    return ParkedJob(
        job_id=entry.job_id,
        step_index=entry.step_index,
        spark_ref=str(ref) if ref else None,
        parked_at=entry.parked_at or None,
        last_checked_at=checked_at,
        next_check_at=due_at,
    )
