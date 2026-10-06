from __future__ import annotations

from typing import Any

from cryo.queue.reconcile import purge_stale_queue_entries
from cryo.queue.redis_queue import RedisMaintenanceQueue


class _FakePipeline:
    """Immediate-execution stand-in -- no atomicity needed for these tests."""

    def __init__(self, client: FakeRedis) -> None:
        self._client = client

    def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> _FakePipeline:
        self._client.hset(key, mapping=mapping, **kwargs)
        return self

    def zadd(self, key: str, mapping: dict[str, float]) -> _FakePipeline:
        self._client.zadd(key, mapping)
        return self

    def sadd(self, key: str, *values: str) -> _FakePipeline:
        self._client.sadd(key, *values)
        return self

    def zrem(self, key: str, member: str) -> _FakePipeline:
        self._client.zrem(key, member)
        return self

    def rpush(self, key: str, value: str) -> _FakePipeline:
        self._client.rpush(key, value)
        return self

    def hincrby(self, key: str, field: str, amount: int) -> _FakePipeline:
        self._client.hincrby(key, field, amount)
        return self

    def execute(self) -> list[Any]:
        return []


class FakeRedis:
    """Minimal in-memory stand-in for the subset of redis-py used by RedisMaintenanceQueue.

    Stores everything as bytes, matching real redis-py response types, since
    `cryo.queue.redis_queue._decode` is written to expect that.
    """

    def __init__(self) -> None:
        self._hashes: dict[str, dict[bytes, bytes]] = {}
        self._zsets: dict[str, dict[bytes, float]] = {}
        self._sets: dict[str, set[bytes]] = {}
        self._lists: dict[str, list[bytes]] = {}

    @staticmethod
    def _enc(value: Any) -> bytes:
        return value if isinstance(value, bytes) else str(value).encode("utf-8")

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    def hset(self, key: str, mapping: dict[str, Any] | None = None, **kwargs: Any) -> None:
        data = dict(mapping or {})
        data.update(kwargs)
        bucket = self._hashes.setdefault(key, {})
        for field, value in data.items():
            bucket[self._enc(field)] = self._enc(value)

    def hget(self, key: str, field: str) -> bytes | None:
        return self._hashes.get(key, {}).get(self._enc(field))

    def hgetall(self, key: str) -> dict[bytes, bytes]:
        return dict(self._hashes.get(key, {}))

    def hincrby(self, key: str, field: str, amount: int) -> int:
        bucket = self._hashes.setdefault(key, {})
        current = int(bucket.get(self._enc(field), b"0"))
        new_value = current + amount
        bucket[self._enc(field)] = self._enc(new_value)
        return new_value

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        bucket = self._zsets.setdefault(key, {})
        for member, score in mapping.items():
            bucket[self._enc(member)] = score

    def zrem(self, key: str, member: str) -> None:
        self._zsets.get(key, {}).pop(self._enc(member), None)

    def zrevrange(self, key: str, start: int, end: int) -> list[bytes]:
        items = sorted(self._zsets.get(key, {}).items(), key=lambda kv: kv[1], reverse=True)
        members = [member for member, _ in items]
        if end == -1:
            return members[start:]
        return members[start : end + 1]

    def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    def sadd(self, key: str, *values: str) -> None:
        bucket = self._sets.setdefault(key, set())
        for value in values:
            bucket.add(self._enc(value))

    def smembers(self, key: str) -> set[bytes]:
        return set(self._sets.get(key, set()))

    def rpush(self, key: str, value: str) -> None:
        self._lists.setdefault(key, []).append(self._enc(value))

    def lpop(self, key: str) -> bytes | None:
        bucket = self._lists.get(key)
        return bucket.pop(0) if bucket else None


class _FakeStore:
    def __init__(self, existing_destinations: set[str], jobs: dict[str, str]) -> None:
        self._existing_destinations = existing_destinations
        self._jobs = jobs

    def destination_exists(self, destination_id: str) -> bool:
        return destination_id in self._existing_destinations

    def get_job(self, job_id: str) -> str:
        if job_id not in self._jobs:
            raise KeyError(job_id)
        return self._jobs[job_id]


def test_cancel_pending_removes_entry_even_with_stale_job_hash() -> None:
    """Reproduces the production bug: a pending entry's own job hash points at a
    *different* destination than the zset it's actually stored under (legacy/corrupt
    data). Without an explicit destination_id, cancel_pending used to zrem the wrong
    key and silently no-op, so the entry was "purged" every cycle but never actually
    removed.
    """
    redis = FakeRedis()
    queue = RedisMaintenanceQueue(redis)

    # Simulate corrupt state directly: job "None" sitting in destination A's pending
    # zset, but its job hash (if any) claims destination B.
    redis.zadd(queue._pending_key("dest-a"), {"None": 0})
    redis.sadd(queue._destinations_key(), "dest-a")
    redis.hset(queue._job_key("None"), mapping={"destination_id": "dest-b", "status": "queued"})

    assert queue.pending("dest-a") == ["None"]

    queue.cancel_pending("None", "dest-a")

    assert queue.pending("dest-a") == []


def test_purge_stale_queue_entries_actually_drains_orphans_in_one_pass() -> None:
    """End-to-end version of the same bug via the real purge path used by the
    governor/reconciler. Before the fix, this returned 1 purged on every call
    forever because the removal never stuck; it must now converge to 0 on the
    second pass.
    """
    redis = FakeRedis()
    queue = RedisMaintenanceQueue(redis)
    store = _FakeStore(existing_destinations=set(), jobs={})

    redis.zadd(queue._pending_key("missing-dest"), {"None": 0})
    redis.sadd(queue._destinations_key(), "missing-dest")
    # Job hash exists but (as in production) points somewhere unrelated.
    redis.hset(queue._job_key("None"), mapping={"destination_id": "some-other-dest"})

    first_pass = purge_stale_queue_entries(store, queue)
    assert first_pass == 1

    second_pass = purge_stale_queue_entries(store, queue)
    assert second_pass == 0
    assert queue.pending_by_destination() == {}
