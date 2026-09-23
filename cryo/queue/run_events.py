from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]
MAX_STREAM_LEN = 500
DEFAULT_REPLAY_COUNT = 200

# MAX_STREAM_LEN caps how large one run's stream gets, but nothing capped how many of them
# accumulate: the key was created per run and never removed, so the count grew with every run
# forever. Sliding expiry on publish means a run's buffer outlives its last event by this long
# and then goes away on its own. Safe because this stream is only the live-tail/replay buffer
# for the UI -- every event is also written to the observation lakehouse, which is the archive
# (see attach_observation_lakehouse). A day is far longer than anyone tails a finished run,
# and any request past it reads the lakehouse rather than Redis.
STREAM_TTL_SEC = 24 * 60 * 60

# Module-level handle to the observation lakehouse. Filled in by
# ``attach_observation_lakehouse`` at process startup; the Redis/InMemory bus instances read it on
# each publish so we can keep the bus immutable for tests while still enabling the feature in the
# FastAPI process.
_OBSERVATION_LAKEHOUSE: Any | None = None
_OBSERVATION_LOCK = Lock()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_log_line(
    *,
    run_id: str | uuid.UUID,
    message: str,
    level: str = "info",
    job_id: str | uuid.UUID | None = None,
    table_id: str | uuid.UUID | None = None,
    table_ref: str | None = None,
    chunk_id: int | None = None,
    operation: str | None = None,
    engine: str | None = None,
) -> JsonDict:
    event: JsonDict = {
        "type": "log_line",
        "ts": _utc_now_iso(),
        "run_id": str(run_id),
        "level": level,
        "message": message,
    }
    if job_id is not None:
        event["job_id"] = str(job_id)
    if table_id is not None:
        event["table_id"] = str(table_id)
    if table_ref is not None:
        event["table_ref"] = table_ref
    if chunk_id is not None:
        event["chunk_id"] = chunk_id
    if operation is not None:
        event["operation"] = operation
    if engine is not None:
        event["engine"] = engine
    return event


def build_job_status(
    *,
    run_id: str | uuid.UUID,
    job_id: str | uuid.UUID,
    status: str,
    table_id: str | uuid.UUID | None = None,
    table_ref: str | None = None,
    message: str | None = None,
) -> JsonDict:
    event: JsonDict = {
        "type": "job_status",
        "ts": _utc_now_iso(),
        "run_id": str(run_id),
        "job_id": str(job_id),
        "status": status,
    }
    if table_id is not None:
        event["table_id"] = str(table_id)
    if table_ref is not None:
        event["table_ref"] = table_ref
    if message is not None:
        event["message"] = message
    return event


def build_scan_progress(
    *,
    run_id: str | uuid.UUID,
    event: str,
    message: str,
    chunk_id: int | None = None,
    table_ref: str | None = None,
    level: str = "info",
) -> JsonDict:
    payload: JsonDict = {
        "type": "scan_progress",
        "ts": _utc_now_iso(),
        "run_id": str(run_id),
        "event": event,
        "level": level,
        "message": message,
    }
    if chunk_id is not None:
        payload["chunk_id"] = chunk_id
    if table_ref is not None:
        payload["table_ref"] = table_ref
    return payload


class RunEventSink(Protocol):
    def publish(self, run_id: str | uuid.UUID, event: JsonDict) -> None: ...


class RunEventBus(RunEventSink, Protocol):
    def replay(
        self, run_id: str | uuid.UUID, *, count: int = DEFAULT_REPLAY_COUNT
    ) -> list[JsonDict]: ...

    def read_blocking(
        self,
        run_id: str | uuid.UUID,
        *,
        last_id: str = "0-0",
        block_ms: int = 1000,
        count: int = 50,
    ) -> list[tuple[str, JsonDict]]: ...


class NullRunEventSink:
    """No-op sink used when Redis is unavailable."""

    def publish(self, run_id: str | uuid.UUID, event: JsonDict) -> None:
        return None


class InMemoryRunEventBus:
    """Deterministic event bus for unit tests."""

    def __init__(self) -> None:
        self._events: dict[str, list[tuple[str, JsonDict]]] = defaultdict(list)
        self._lock = Lock()
        self._sequence = 0

    def publish(self, run_id: str | uuid.UUID, event: JsonDict) -> None:
        with self._lock:
            self._sequence += 1
            message_id = f"{self._sequence}-0"
            self._events[str(run_id)].append((message_id, dict(event)))
        _publish_to_lakehouse(run_id, event)

    def replay(
        self, run_id: str | uuid.UUID, *, count: int = DEFAULT_REPLAY_COUNT
    ) -> list[JsonDict]:
        with self._lock:
            rows = self._events.get(str(run_id), [])
            return [event for _, event in rows[-count:]]

    def replay_with_ids(
        self,
        run_id: str | uuid.UUID,
        *,
        count: int = DEFAULT_REPLAY_COUNT,
    ) -> list[tuple[str, JsonDict]]:
        with self._lock:
            rows = self._events.get(str(run_id), [])
            return list(rows[-count:])

    def latest_message_id(self, run_id: str | uuid.UUID) -> str:
        with self._lock:
            rows = self._events.get(str(run_id), [])
            if not rows:
                return "0-0"
            return rows[-1][0]

    def read_blocking(
        self,
        run_id: str | uuid.UUID,
        *,
        last_id: str = "0-0",
        block_ms: int = 1000,
        count: int = 50,
    ) -> list[tuple[str, JsonDict]]:
        del block_ms, count
        with self._lock:
            rows = self._events.get(str(run_id), [])
            if last_id == "0-0":
                return list(rows)
            for index, (message_id, _) in enumerate(rows):
                if message_id == last_id:
                    return rows[index + 1 :]
            return list(rows)


class RedisRunEventBus:
    """Redis Streams-backed run event bus with capped replay."""

    def __init__(self, redis_client: Any, *, namespace: str = "cryo") -> None:
        self._redis = redis_client
        self._namespace = namespace

    def publish(self, run_id: str | uuid.UUID, event: JsonDict) -> None:
        try:
            key = self._stream_key(run_id)
            pipe = self._redis.pipeline()
            pipe.xadd(key, {"payload": json.dumps(event)}, maxlen=MAX_STREAM_LEN, approximate=True)
            pipe.expire(key, STREAM_TTL_SEC)
            pipe.execute()
        except Exception:
            logger.exception("Failed to publish run event for run_id=%s", run_id)
        _publish_to_lakehouse(run_id, event)

    def replay(
        self, run_id: str | uuid.UUID, *, count: int = DEFAULT_REPLAY_COUNT
    ) -> list[JsonDict]:
        return [event for _, event in self.replay_with_ids(run_id, count=count)]

    def replay_with_ids(
        self,
        run_id: str | uuid.UUID,
        *,
        count: int = DEFAULT_REPLAY_COUNT,
    ) -> list[tuple[str, JsonDict]]:
        try:
            rows = self._redis.xrevrange(self._stream_key(run_id), count=count)
        except Exception:
            logger.exception("Failed to replay run events for run_id=%s", run_id)
            return []
        events: list[tuple[str, JsonDict]] = []
        for message_id, fields in reversed(rows or []):
            payload = _decode_payload(fields)
            if payload is None:
                continue
            message_key = (
                message_id.decode("utf-8") if isinstance(message_id, bytes) else str(message_id)
            )
            events.append((message_key, payload))
        return events

    def latest_message_id(self, run_id: str | uuid.UUID) -> str:
        try:
            rows = self._redis.xrevrange(self._stream_key(run_id), count=1)
        except Exception:
            return "0-0"
        if not rows:
            return "0-0"
        message_id, _ = rows[0]
        return message_id.decode("utf-8") if isinstance(message_id, bytes) else str(message_id)

    def read_blocking(
        self,
        run_id: str | uuid.UUID,
        *,
        last_id: str = "0-0",
        block_ms: int = 1000,
        count: int = 50,
    ) -> list[tuple[str, JsonDict]]:
        try:
            rows = self._redis.xread(
                {self._stream_key(run_id): last_id},
                block=block_ms,
                count=count,
            )
        except Exception:
            logger.exception("Failed to read run events for run_id=%s", run_id)
            return []
        messages: list[tuple[str, JsonDict]] = []
        for _, entries in rows or []:
            for message_id, fields in entries:
                payload = _decode_payload(fields)
                if payload is None:
                    continue
                message_key = (
                    message_id.decode("utf-8") if isinstance(message_id, bytes) else str(message_id)
                )
                messages.append((message_key, payload))
        return messages

    def _stream_key(self, run_id: str | uuid.UUID) -> str:
        return f"{self._namespace}:run:{run_id}:events"


def safe_publish(
    sink: RunEventSink | None,
    run_id: str | uuid.UUID,
    event: JsonDict,
) -> None:
    if sink is None:
        return
    try:
        sink.publish(run_id, event)
    except Exception:
        logger.exception("Failed to publish run event for run_id=%s", run_id)
    _publish_to_lakehouse(run_id, event)


def attach_observation_lakehouse(lakehouse: Any) -> None:
    """Wire the process-wide observation lakehouse used as a long-term archive for every run
    event. Safe to call once at app startup; replaces any previous sink. The 30-row Redis stream
    cap does not apply here -- this is the long-term store."""
    global _OBSERVATION_LAKEHOUSE
    with _OBSERVATION_LOCK:
        _OBSERVATION_LAKEHOUSE = lakehouse


def detach_observation_lakehouse() -> None:
    """Drop any attached lakehouse. Mostly useful for tests."""
    global _OBSERVATION_LAKEHOUSE
    with _OBSERVATION_LOCK:
        _OBSERVATION_LAKEHOUSE = None


def _publish_to_lakehouse(run_id: str | uuid.UUID, event: JsonDict) -> None:
    with _OBSERVATION_LOCK:
        lakehouse = _OBSERVATION_LAKEHOUSE
    if lakehouse is None:
        return
    try:
        from cryo.observability.observation_lakehouse import ObservationEvent

        recorded_at_raw = event.get("ts")
        if isinstance(recorded_at_raw, str):
            recorded_at = datetime.fromisoformat(recorded_at_raw.replace("Z", "+00:00"))
        else:
            recorded_at = datetime.now(UTC)
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=UTC)
        message = str(event.get("message") or event.get("subject") or "")
        lakehouse.append(
            ObservationEvent(
                id=uuid.uuid4(),
                recorded_at=recorded_at,
                run_id=str(run_id),
                event_type=str(event.get("type") or event.get("event") or "log_line"),
                level=str(event.get("level") or "info"),
                table_id=str(event["table_id"]) if event.get("table_id") is not None else None,
                subject=message,
                payload=event,
                correlation_id=event.get("correlation_id"),
                actor=event.get("actor"),
            )
        )
    except Exception:
        logger.exception("Failed to archive run event to observation lakehouse")


def scan_progress_message(event: JsonDict, *, chunk_id: int, num_chunks: int) -> str:
    event_type = str(event.get("event") or "")
    table_ref = event.get("table_ref")
    if event_type == "assigned":
        discovered = int(event.get("discovered") or 0)
        assigned = int(event.get("assigned") or 0)
        return (
            f"Assigned {assigned} of {discovered} discovered tables to chunk "
            f"{chunk_id + 1}/{num_chunks}"
        )
    if event_type == "start_table" and table_ref:
        return f"Scanning {table_ref}"
    if event_type == "table_result" and table_ref:
        outcome = str(event.get("outcome") or "")
        if outcome == "scanned":
            return f"Scanned {table_ref}"
        if outcome == "skipped":
            return f"Skipped {table_ref}"
        if outcome == "failed":
            error = event.get("error")
            if error:
                return f"Failed {table_ref}: {error}"
            return f"Failed {table_ref}"
    return f"Scan event: {event_type or 'unknown'}"


def _decode_payload(fields: Any) -> JsonDict | None:
    raw = fields.get(b"payload") if isinstance(fields, dict) else None
    if raw is None and isinstance(fields, dict):
        raw = fields.get("payload")
    if raw is None:
        return None
    text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def get_run_event_bus(redis_client: Any | None) -> RunEventBus | NullRunEventSink:
    if redis_client is None:
        return NullRunEventSink()
    return RedisRunEventBus(redis_client)
