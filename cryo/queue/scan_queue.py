from __future__ import annotations

import json
from collections import deque
from typing import Any, Protocol


class ScanQueue(Protocol):
    def enqueue(self, run_id: str, payload: dict[str, Any]) -> None: ...

    def enqueue_chunks(self, run_id: str, payload: dict[str, Any], *, num_chunks: int) -> None: ...

    def claim(self) -> dict[str, Any] | None: ...

    def pending_count(self) -> int: ...


class InMemoryScanQueue:
    """Deterministic scan queue for unit tests and local wiring."""

    def __init__(self) -> None:
        self._pending: deque[dict[str, Any]] = deque()

    def enqueue(self, run_id: str, payload: dict[str, Any]) -> None:
        self._pending.append({"run_id": run_id, **payload})

    def enqueue_chunks(self, run_id: str, payload: dict[str, Any], *, num_chunks: int) -> None:
        for chunk_id in range(num_chunks):
            self.enqueue(
                run_id,
                {
                    **payload,
                    "chunk_id": chunk_id,
                    "num_chunks": num_chunks,
                },
            )

    def claim(self) -> dict[str, Any] | None:
        if not self._pending:
            return None
        return self._pending.popleft()

    def pending_count(self) -> int:
        return len(self._pending)


class RedisScanQueue:
    """Redis list-backed queue for ad-hoc scanner trigger requests."""

    def __init__(self, redis_client: Any, *, namespace: str = "cryo") -> None:
        self._redis = redis_client
        self._key = f"{namespace}:scan:pending"

    def enqueue(self, run_id: str, payload: dict[str, Any]) -> None:
        body = json.dumps({"run_id": run_id, **payload})
        self._redis.rpush(self._key, body)

    def enqueue_chunks(self, run_id: str, payload: dict[str, Any], *, num_chunks: int) -> None:
        if num_chunks <= 0:
            raise ValueError("num_chunks must be positive")
        pipe = self._redis.pipeline()
        for chunk_id in range(num_chunks):
            body = json.dumps(
                {
                    "run_id": run_id,
                    **payload,
                    "chunk_id": chunk_id,
                    "num_chunks": num_chunks,
                }
            )
            pipe.rpush(self._key, body)
        pipe.execute()

    def claim(self) -> dict[str, Any] | None:
        raw = self._redis.lpop(self._key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("scan queue payload must be a JSON object")
        return parsed

    def pending_count(self) -> int:
        return int(self._redis.llen(self._key))
