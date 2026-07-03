from __future__ import annotations

import json
from typing import Any

from cryo.events.watcher import TableCommitted


class RedisEventStream:
    """Redis Streams-backed event bus for TableCommitted events."""

    def __init__(self, redis_client: Any, *, namespace: str = "cryo") -> None:
        self._redis = redis_client
        self._namespace = namespace

    def publish(self, stream: str, payload: dict[str, Any]) -> str:
        message_id = self._redis.xadd(stream, {"payload": json.dumps(payload)})
        if isinstance(message_id, bytes):
            return message_id.decode("utf-8")
        return str(message_id)

    def read_group(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 10,
    ) -> list[tuple[str, dict[str, Any]]]:
        self._ensure_group(stream, group)
        rows = self._redis.xreadgroup(
            group,
            consumer,
            {stream: ">"},
            count=count,
            block=1,
        )
        messages: list[tuple[str, dict[str, Any]]] = []
        for _, entries in rows or []:
            for message_id, fields in entries:
                raw = fields.get(b"payload") or fields.get("payload")
                text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                message_key = (
                    message_id.decode("utf-8") if isinstance(message_id, bytes) else str(message_id)
                )
                messages.append((message_key, json.loads(text)))
        return messages

    def ack(self, stream: str, group: str, message_id: str) -> None:
        self._redis.xack(stream, group, message_id)

    def publish_commit(self, event: TableCommitted, *, stream: str = "cryo:table-commits") -> str:
        return self.publish(
            stream,
            {
                "table_ref": event.table_ref,
                "snapshot_id": event.snapshot_id,
                "observed_at": event.observed_at.isoformat(),
            },
        )

    def _ensure_group(self, stream: str, group: str) -> None:
        try:
            self._redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
