"""Short-lived event replay. Postgres holds the final message; Redis is optional."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

_MEMORY: dict[str, list[dict[str, Any]]] = defaultdict(list)
_MAX = 2000


def publish(message_id: str, event: dict[str, Any]) -> None:
    bucket = _MEMORY[message_id]
    bucket.append(event)
    if len(bucket) > _MAX:
        del bucket[: len(bucket) - _MAX]
    try:
        from cryo.queue.redis_queue import get_redis_client

        client = get_redis_client()
        if client is None:
            return
        key = f"cryo:assistant:msg:{message_id}"
        client.xadd(key, {"payload": json.dumps(event)}, maxlen=_MAX)
        client.expire(key, 3600)
    except Exception:
        return


def replay(message_id: str, *, after_seq: int = 0) -> list[dict[str, Any]]:
    stored = [
        event for event in _MEMORY.get(message_id, []) if int(event.get("seq") or 0) > after_seq
    ]
    if stored:
        return stored
    try:
        from cryo.queue.redis_queue import get_redis_client

        client = get_redis_client()
        if client is None:
            return []
        rows = client.xrange(f"cryo:assistant:msg:{message_id}")
    except Exception:
        return []
    events: list[dict[str, Any]] = []
    for _entry_id, fields in rows or []:
        raw = fields.get("payload") or fields.get(b"payload")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not raw:
            continue
        event = json.loads(raw)
        if int(event.get("seq") or 0) > after_seq:
            events.append(event)
    return events


def clear(message_id: str) -> None:
    _MEMORY.pop(message_id, None)


def mark_stopped(message_id: str) -> None:
    _MEMORY[f"stop:{message_id}"].append({"stopped": True})
    client = _redis()
    if client is None:
        return
    try:
        client.set(f"cryo:assistant:stop:{message_id}", "1", ex=120)
    except Exception:
        return


def is_stopped(message_id: str) -> bool:
    if _MEMORY.get(f"stop:{message_id}"):
        return True
    client = _redis()
    if client is None:
        return False
    try:
        return bool(client.get(f"cryo:assistant:stop:{message_id}"))
    except Exception:
        return False


def turn_slots_left(subject: str, limit: int) -> int:
    client = _redis()
    if client is None:
        used = len(_MEMORY.get(f"active:{subject}", []))
        return max(0, limit - used)
    try:
        raw = client.get(f"cryo:assistant:active:{subject}")
        used = int(raw or 0)
    except Exception:
        return limit
    return max(0, limit - used)


def acquire_turn(subject: str, limit: int) -> bool:
    client = _redis()
    if client is None:
        count = len(_MEMORY.get(f"active:{subject}", []))
        if count >= limit:
            return False
        _MEMORY[f"active:{subject}"].append({"on": True})
        return True
    key = f"cryo:assistant:active:{subject}"
    try:
        count = int(client.incr(key))
        client.expire(key, 120)
    except Exception:
        return True
    if count > limit:
        try:
            client.decr(key)
        except Exception:
            return False
        return False
    return True


def release_turn(subject: str) -> None:
    bucket = _MEMORY.get(f"active:{subject}")
    if bucket:
        bucket.pop()
    client = _redis()
    if client is None:
        return
    try:
        client.decr(f"cryo:assistant:active:{subject}")
    except Exception:
        return


def _redis() -> Any:
    try:
        from cryo.queue.redis_queue import get_redis_client
    except Exception:
        return None
    try:
        return get_redis_client()
    except Exception:
        return None
