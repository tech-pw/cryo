from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4


class LockStore(Protocol):
    def acquire_leader(self, name: str, holder: str, ttl_seconds: int) -> bool: ...

    def acquire_table_lock(self, table_id: str, job_id: str, ttl_seconds: int) -> bool: ...

    def renew_table_lock(self, table_id: str, job_id: str, ttl_seconds: int) -> bool: ...

    def release_table_lock(self, table_id: str, job_id: str) -> None: ...

    def is_table_locked(self, table_id: str) -> bool: ...


@dataclass
class _MemoryLock:
    holder: str
    expires_at: datetime


class InMemoryLockStore:
    def __init__(self, clock: Any | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._leaders: dict[str, _MemoryLock] = {}
        self._table_locks: dict[str, _MemoryLock] = {}

    def acquire_leader(self, name: str, holder: str, ttl_seconds: int) -> bool:
        return self._acquire(self._leaders, name, holder, ttl_seconds)

    def acquire_table_lock(self, table_id: str, job_id: str, ttl_seconds: int) -> bool:
        return self._acquire(self._table_locks, table_id, job_id, ttl_seconds)

    def renew_table_lock(self, table_id: str, job_id: str, ttl_seconds: int) -> bool:
        self._purge_expired(self._table_locks)
        lock = self._table_locks.get(table_id)
        if lock is None or lock.holder != job_id:
            return False
        lock.expires_at = self._clock() + timedelta(seconds=ttl_seconds)
        return True

    def release_table_lock(self, table_id: str, job_id: str) -> None:
        lock = self._table_locks.get(table_id)
        if lock and lock.holder == job_id:
            del self._table_locks[table_id]

    def is_table_locked(self, table_id: str) -> bool:
        self._purge_expired(self._table_locks)
        return table_id in self._table_locks

    def _acquire(
        self,
        locks: dict[str, _MemoryLock],
        key: str,
        holder: str,
        ttl_seconds: int,
    ) -> bool:
        self._purge_expired(locks)
        existing = locks.get(key)
        if existing and existing.holder != holder:
            return False
        locks[key] = _MemoryLock(
            holder=holder,
            expires_at=self._clock() + timedelta(seconds=ttl_seconds),
        )
        return True

    def _purge_expired(self, locks: dict[str, _MemoryLock]) -> None:
        now = self._clock()
        expired = [key for key, lock in locks.items() if lock.expires_at <= now]
        for key in expired:
            del locks[key]


class RedisLockStore:
    """Redis SET NX lock store for leader election and per-table admission guards."""

    def __init__(self, redis_client: Any, namespace: str = "cryo") -> None:
        self._redis = redis_client
        self._namespace = namespace

    def acquire_leader(self, name: str, holder: str, ttl_seconds: int) -> bool:
        return bool(self._redis.set(self._leader_key(name), holder, nx=True, ex=ttl_seconds))

    def acquire_table_lock(self, table_id: str, job_id: str, ttl_seconds: int) -> bool:
        token = job_id or str(uuid4())
        return bool(self._redis.set(self._table_key(table_id), token, nx=True, ex=ttl_seconds))

    def renew_table_lock(self, table_id: str, job_id: str, ttl_seconds: int) -> bool:
        key = self._table_key(table_id)
        current = self._redis.get(key)
        if _decode(current) != job_id:
            return False
        return bool(self._redis.expire(key, ttl_seconds))

    def release_table_lock(self, table_id: str, job_id: str) -> None:
        key = self._table_key(table_id)
        current = self._redis.get(key)
        if _decode(current) == job_id:
            self._redis.delete(key)

    def is_table_locked(self, table_id: str) -> bool:
        return bool(self._redis.exists(self._table_key(table_id)))

    def _leader_key(self, name: str) -> str:
        return f"{self._namespace}:leader:{name}"

    def _table_key(self, table_id: str) -> str:
        return f"{self._namespace}:table-lock:{table_id}"


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
