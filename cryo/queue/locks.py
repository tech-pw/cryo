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

    def table_lock_holder(self, table_id: str) -> str | None: ...

    def list_table_locks(self) -> list[tuple[str, str]]: ...

    def acquire_job_owner(self, job_id: str, holder: str, ttl_seconds: int) -> bool: ...

    def renew_job_owner(self, job_id: str, holder: str, ttl_seconds: int) -> bool: ...

    def release_job_owner(self, job_id: str, holder: str) -> None: ...

    def get_job_owner(self, job_id: str) -> str | None: ...


@dataclass
class _MemoryLock:
    holder: str
    expires_at: datetime


class InMemoryLockStore:
    def __init__(self, clock: Any | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._leaders: dict[str, _MemoryLock] = {}
        self._table_locks: dict[str, _MemoryLock] = {}
        self._job_owners: dict[str, _MemoryLock] = {}

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

    def table_lock_holder(self, table_id: str) -> str | None:
        self._purge_expired(self._table_locks)
        lock = self._table_locks.get(table_id)
        return lock.holder if lock is not None else None

    def list_table_locks(self) -> list[tuple[str, str]]:
        self._purge_expired(self._table_locks)
        return [(table_id, lock.holder) for table_id, lock in self._table_locks.items()]

    def acquire_job_owner(self, job_id: str, holder: str, ttl_seconds: int) -> bool:
        return self._acquire(self._job_owners, job_id, holder, ttl_seconds)

    def renew_job_owner(self, job_id: str, holder: str, ttl_seconds: int) -> bool:
        self._purge_expired(self._job_owners)
        lock = self._job_owners.get(job_id)
        if lock is None or lock.holder != holder:
            return False
        lock.expires_at = self._clock() + timedelta(seconds=ttl_seconds)
        return True

    def release_job_owner(self, job_id: str, holder: str) -> None:
        lock = self._job_owners.get(job_id)
        if lock and lock.holder == holder:
            del self._job_owners[job_id]

    def get_job_owner(self, job_id: str) -> str | None:
        self._purge_expired(self._job_owners)
        lock = self._job_owners.get(job_id)
        return lock.holder if lock is not None else None

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
        lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('expire', KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        return bool(self._redis.eval(lua, 1, key, job_id, ttl_seconds))

    def release_table_lock(self, table_id: str, job_id: str) -> None:
        key = self._table_key(table_id)
        lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        else
            return 0
        end
        """
        self._redis.eval(lua, 1, key, job_id)

    def is_table_locked(self, table_id: str) -> bool:
        return bool(self._redis.exists(self._table_key(table_id)))

    def table_lock_holder(self, table_id: str) -> str | None:
        return _decode(self._redis.get(self._table_key(table_id))) or None

    def list_table_locks(self) -> list[tuple[str, str]]:
        # SCAN, not KEYS -- KEYS blocks the whole Redis instance on a large keyspace;
        # SCAN iterates in small cursor batches instead.
        prefix = f"{self._namespace}:table-lock:"
        pairs: list[tuple[str, str]] = []
        for raw_key in self._redis.scan_iter(match=f"{prefix}*"):
            key = _decode(raw_key)
            table_id = key[len(prefix) :]
            holder = _decode(self._redis.get(key))
            if holder:
                pairs.append((table_id, holder))
        return pairs

    def acquire_job_owner(self, job_id: str, holder: str, ttl_seconds: int) -> bool:
        return bool(self._redis.set(self._job_owner_key(job_id), holder, nx=True, ex=ttl_seconds))

    def renew_job_owner(self, job_id: str, holder: str, ttl_seconds: int) -> bool:
        key = self._job_owner_key(job_id)
        current = self._redis.get(key)
        if _decode(current) != holder:
            return False
        return bool(self._redis.expire(key, ttl_seconds))

    def release_job_owner(self, job_id: str, holder: str) -> None:
        key = self._job_owner_key(job_id)
        lua = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        else
            return 0
        end
        """
        self._redis.eval(lua, 1, key, holder)

    def get_job_owner(self, job_id: str) -> str | None:
        value = _decode(self._redis.get(self._job_owner_key(job_id)))
        return value or None

    def _leader_key(self, name: str) -> str:
        return f"{self._namespace}:leader:{name}"

    def _table_key(self, table_id: str) -> str:
        return f"{self._namespace}:table-lock:{table_id}"

    def _job_owner_key(self, job_id: str) -> str:
        return f"{self._namespace}:job-owner:{job_id}"


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
