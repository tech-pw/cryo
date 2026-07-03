from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from cryo.events.watcher import TableCommitted


class EventStream(Protocol):
    def publish(self, stream: str, payload: dict[str, Any]) -> str: ...

    def read_group(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 10,
    ) -> list[tuple[str, dict[str, Any]]]: ...

    def ack(self, stream: str, group: str, message_id: str) -> None: ...


@dataclass(frozen=True)
class ConsumerResult:
    handled: int = 0
    failed: int = 0


Handler = Callable[[TableCommitted], None]


class TableCommitConsumer:
    """Subscribe to TableCommitted events and trigger opportunistic re-scoring."""

    def __init__(
        self,
        stream: EventStream,
        *,
        stream_name: str = "cryo:table-commits",
        group: str = "cryo-events",
        consumer: str = "consumer-1",
        on_commit: Handler | None = None,
    ) -> None:
        self._stream = stream
        self._stream_name = stream_name
        self._group = group
        self._consumer = consumer
        self._on_commit = on_commit

    def publish(self, event: TableCommitted) -> str:
        payload = {
            "table_ref": event.table_ref,
            "snapshot_id": event.snapshot_id,
            "observed_at": event.observed_at.isoformat(),
        }
        return self._stream.publish(self._stream_name, payload)

    def consume_once(self, *, count: int = 10) -> ConsumerResult:
        handled = 0
        failed = 0
        for message_id, payload in self._stream.read_group(
            self._stream_name,
            self._group,
            self._consumer,
            count=count,
        ):
            try:
                event = _parse_event(payload)
                if self._on_commit is not None:
                    self._on_commit(event)
                self._stream.ack(self._stream_name, self._group, message_id)
                handled += 1
            except Exception:
                failed += 1
        return ConsumerResult(handled=handled, failed=failed)


class InMemoryEventStream:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict[str, Any]]] = []
        self.pending: list[tuple[str, dict[str, Any]]] = []
        self._seq = 0

    def publish(self, stream: str, payload: dict[str, Any]) -> str:
        del stream
        self._seq += 1
        message_id = str(self._seq)
        self.messages.append((message_id, payload))
        self.pending.append((message_id, payload))
        return message_id

    def read_group(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 10,
    ) -> list[tuple[str, dict[str, Any]]]:
        del stream, group, consumer
        batch = self.pending[:count]
        self.pending = self.pending[count:]
        return batch

    def ack(self, stream: str, group: str, message_id: str) -> None:
        del stream, group, message_id


def _parse_event(payload: dict[str, Any]) -> TableCommitted:
    from datetime import datetime

    observed_at = datetime.fromisoformat(str(payload["observed_at"]).replace("Z", "+00:00"))
    return TableCommitted(
        table_ref=str(payload["table_ref"]),
        snapshot_id=int(payload["snapshot_id"]),
        observed_at=observed_at,
    )
