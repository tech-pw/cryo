"""Queue-side fixes from the governor review: bounded pending reads, a depth that is not the
read length, rebuildable in-flight counters, and a defer() that returns the slot it held."""

from __future__ import annotations

from typing import Any

from cryo.domain.enums import JobStatus
from cryo.queue.redis_queue import InMemoryMaintenanceQueue, RedisMaintenanceQueue
from tests.test_redis_maintenance_queue import FakeRedis, _FakePipeline


class _Pipe(_FakePipeline):
    def hset(  # type: ignore[override]
        self,
        key: str,
        field: str | None = None,
        value: Any = None,
        mapping: dict[str, Any] | None = None,
    ) -> _Pipe:
        self._client.hset(key, field, value, mapping=mapping)
        return self

    def delete(self, key: str) -> _Pipe:
        self._client.delete(key)
        return self

    def expire(self, key: str, seconds: int) -> _Pipe:
        return self

    def hdel(self, key: str, field: str) -> _Pipe:
        return self


class _Redis(FakeRedis):
    def pipeline(self) -> _Pipe:  # type: ignore[override]
        return _Pipe(self)

    def hset(  # type: ignore[override]
        self,
        key: str,
        field: str | None = None,
        value: Any = None,
        mapping: dict[str, Any] | None = None,
    ) -> None:
        data = dict(mapping or {})
        if field is not None:
            data[field] = value
        super().hset(key, mapping=data)

    def delete(self, key: str) -> None:
        self._hashes.pop(key, None)

    def hget(self, key: str, field: str) -> bytes | None:
        return super().hget(key, field)


def _queue() -> RedisMaintenanceQueue:
    return RedisMaintenanceQueue(_Redis(), consumer="test")


def test_pending_limit_returns_the_head_in_priority_order() -> None:
    queue = _queue()
    for index in range(5):
        queue.enqueue(f"job-{index}", "dest", priority=index)

    assert queue.pending("dest", limit=2) == ["job-4", "job-3"]
    assert queue.pending("dest", limit=0) == []
    assert len(queue.pending("dest")) == 5


def test_pending_depth_counts_the_whole_queue_not_the_limited_read() -> None:
    queue = _queue()
    for index in range(7):
        queue.enqueue(f"job-{index}", "dest", priority=index)

    assert len(queue.pending_by_destination(limit=3)["dest"]) == 3
    assert queue.pending_depth("dest") == 7
    assert queue.pending_depth("unknown") == 0


def test_in_memory_queue_matches_the_limit_and_depth_contract() -> None:
    queue = InMemoryMaintenanceQueue()
    for index in range(4):
        queue.enqueue(f"job-{index}", "dest", priority=index)

    assert queue.pending("dest", limit=2) == ["job-3", "job-2"]
    assert queue.pending_by_destination(limit=1) == {"dest": ["job-3"]}
    assert queue.pending_depth("dest") == 4
    assert queue.known_destinations() == ["dest"]


def test_reset_in_flight_replaces_a_drifted_counter_with_database_truth() -> None:
    queue = _queue()
    queue._redis.hset(queue._in_flight_key("dest"), mapping={"default": 9, "ghost": 2})

    queue.reset_in_flight("dest", {"default": 3})

    assert queue.local_in_flight_by_replica("dest") == {"default": 3}


def test_reset_in_flight_to_nothing_clears_the_counter() -> None:
    queue = _queue()
    queue._redis.hset(queue._in_flight_key("dest"), mapping={"default": 4})

    queue.reset_in_flight("dest", {})

    assert queue.local_in_flight_by_replica("dest") == {}


def _seed_job_hash(queue: RedisMaintenanceQueue, job_id: str, status: str) -> None:
    queue._redis.hset(
        queue._job_key(job_id),
        mapping={"destination_id": "dest", "status": status, "replica": "default"},
    )
    queue._redis.hset(queue._in_flight_key("dest"), mapping={"default": 2})


def test_defer_returns_the_slot_of_an_admitted_job() -> None:
    queue = _queue()
    _seed_job_hash(queue, "job-1", JobStatus.ADMITTED.value)

    queue.defer("job-1")

    assert queue.local_in_flight_by_replica("dest") == {"default": 1}


def test_defer_of_a_pending_job_does_not_touch_the_counter() -> None:
    queue = _queue()
    _seed_job_hash(queue, "job-1", JobStatus.QUEUED.value)

    queue.defer("job-1")

    assert queue.local_in_flight_by_replica("dest") == {"default": 2}
