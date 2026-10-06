"""Redis Streams buffer for raw Trino query rows.

Pure I/O module: XADD raw fetched queries to a single per-source Stream and return the
count accepted. The curator drains this stream on a separate cadence. Cap is enforced
via the Redis Stream ``MAXLEN ~ N`` approximation -- bounded memory even under runaway
traffic, and the curator picks up where the stream left off.

Two helpers cover the two halves:

- :func:`buffer_trino_queries` -- called by the fetcher path; XADD one message per
  query, payload is a single JSON field.
- :func:`drain_trino_queries` -- called by the curator; XREADGROUP-style read that
  hands a batch of raw dicts to the caller and tracks the last-seen ID in Redis so
  the next drain picks up where this one left off.

Why a separate buffer (not the existing ``RunEvent`` bus): RunEvent is keyed by
``run_id`` and scoped to maintenance runs. Query rows are a fleet-wide firehose with
no per-run concept, so giving them their own stream + reader key keeps the two
lifecycles independent -- a runaway query stream can never backpressure a maintenance
event stream.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)


# Defaults are conservative -- the curator and fetcher can override at call time.
DEFAULT_STREAM_KEY_TEMPLATE = "cryo:queries:source:{source_id}"
# Cap of 250k messages per source. At ~1 KB/message that's 250 MB per source of in-Redis
# hot buffer, which fits comfortably in any reasonable Redis instance. The curator is
# expected to drain every tick, so this is a backstop, not the steady state.
DEFAULT_MAXLEN = 250_000
# Curator reads in 1000-row batches by default.
DEFAULT_DRAIN_BATCH = 1000


def _stream_key(source_id: Any) -> str:
    return DEFAULT_STREAM_KEY_TEMPLATE.format(source_id=str(source_id))


def _redis_client() -> Any:
    """Resolve the process-wide Redis client used for the query buffer.

    Lazy import to avoid pulling the redis client at module import time -- the curator
    is constructed during scheduler boot, and at that point redis is guaranteed to be
    configured.
    """
    from cryo.queue.redis_queue import get_redis_client

    return get_redis_client()


def buffer_trino_queries(
    queries: Iterable[dict[str, Any]],
    *,
    source_id: Any,
    maxlen: int = DEFAULT_MAXLEN,
) -> int:
    """XADD every query to the per-source stream. Returns count accepted.

    Skips rows without a ``query_id`` -- the curator dedupes by that field and a missing
    ID would be re-archived on every re-fetch.
    """
    client = _redis_client()
    if client is None:
        # Without Redis the curator can't see the buffer anyway; fail soft so a Redis
        # outage doesn't break the fetcher path. The tick continues and the next tick
        # picks up the same window (Trino's ring buffer gives us ~5 min of grace).
        logger.warning("Redis unavailable; dropping %s queries to the floor", "?")
        return 0
    accepted = 0
    for query in queries:
        qid = query.get("query_id")
        if not qid:
            continue
        try:
            client.xadd(
                _stream_key(source_id),
                {"payload": json.dumps(query, default=str)},
                maxlen=maxlen,
                approximate=True,
            )
            accepted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("XADD failed for query_id=%r: %s", qid, exc)
    return accepted


def drain_trino_queries(
    *,
    source_id: Any,
    last_id: str = "0",
    count: int = DEFAULT_DRAIN_BATCH,
    block_ms: int = 0,
) -> tuple[list[dict[str, Any]], str]:
    """Read up to ``count`` pending rows for a source; return (rows, new_last_id).

    Uses XRANGE starting at ``last_id`` (exclusive of it). Pass back the returned
    ``new_last_id`` on the next call. If the stream is empty, returns ``([], last_id)``.

    The caller is responsible for advancing the watermark *only* after the curator
    successfully appends to Iceberg -- this function only moves its in-Redis cursor.
    """
    client = _redis_client()
    if client is None:
        return [], last_id
    try:
        raw_rows = client.xrange(_stream_key(source_id), min=f"({last_id}", count=count)
    except Exception as exc:  # noqa: BLE001
        logger.warning("XRANGE failed on %s: %s", _stream_key(source_id), exc)
        return [], last_id
    rows: list[dict[str, Any]] = []
    new_last_id = last_id
    for message_id, fields in raw_rows:
        try:
            payload = json.loads(fields.get("payload") or "{}")
        except (TypeError, ValueError):
            continue
        rows.append(payload)
        new_last_id = message_id
    return rows, new_last_id


def trim_trino_stream(*, source_id: Any, maxlen: int = DEFAULT_MAXLEN) -> int:
    """Hard-trim the per-source stream down to ``maxlen`` entries. Returns removed count.

    Called by the curator when it has fully drained everything up to a high-water mark
    and is confident no consumer is behind. Approximate trim is fine -- the curator
    tracks its own last_id and a small over-retain never causes duplicates in Iceberg
    (the writer dedupes by query_id).
    """
    client = _redis_client()
    if client is None:
        return 0
    try:
        return int(client.xtrim(_stream_key(source_id), maxlen=maxlen, approximate=True))
    except Exception as exc:  # noqa: BLE001
        logger.warning("XTRIM failed on %s: %s", _stream_key(source_id), exc)
        return 0


def stream_length(*, source_id: Any) -> int:
    """Current length of the per-source stream -- useful for health checks / metrics."""
    client = _redis_client()
    if client is None:
        return 0
    try:
        return int(client.xlen(_stream_key(source_id)))
    except Exception:  # noqa: BLE001
        return 0
