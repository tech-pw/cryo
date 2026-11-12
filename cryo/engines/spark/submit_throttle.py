"""Space out Spark-on-Kubernetes submissions per destination (#487).

Wrapper, driver and executor pods started in bursts outrun the CNI's warm IP pool
(FailedCreatePodSandBox). Every worker process reserves the next free submit slot in
Redis, so the cap holds across the fleet. Without Redis, only the jitter applies.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Any

from cryo.observability.metrics import spark_submit_throttle_wait_seconds

logger = logging.getLogger(__name__)

DEFAULT_MAX_SUBMITS_PER_MINUTE = 60
_MAX_JITTER_SECONDS = 2.0
_KEY_PREFIX = "cryo:spark_submit_slot:"

# Atomically take the next slot: max(now, next_free), then push next_free one interval on.
_RESERVE_LUA = """
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local nxt = tonumber(redis.call('GET', KEYS[1]) or '0')
local slot = math.max(now, nxt)
local ttl_ms = math.ceil((slot + interval - now) * 1000) + 60000
redis.call('SET', KEYS[1], tostring(slot + interval), 'PX', ttl_ms)
return tostring(slot)
"""


def reserve_submit_delay(
    destination_key: str,
    max_per_minute: int,
    *,
    redis_client: Any = None,
    now: Callable[[], float] = time.time,
) -> float:
    """Seconds to wait before this submission, including jitter. 0 when throttling is off."""
    if max_per_minute <= 0:
        return 0.0
    interval = 60.0 / max_per_minute
    jitter = random.uniform(0, min(_MAX_JITTER_SECONDS, interval / 2))
    if redis_client is None:
        from cryo.queue.redis_queue import get_redis_client

        redis_client = get_redis_client()
    if redis_client is None:
        return jitter
    try:
        current = now()
        slot = float(
            redis_client.eval(_RESERVE_LUA, 1, f"{_KEY_PREFIX}{destination_key}", current, interval)
        )
        return max(0.0, slot - current) + jitter
    except Exception:  # noqa: BLE001 - a Redis blip must not block submission
        logger.warning(
            "Spark submit throttle unavailable; submitting with jitter only", exc_info=True
        )
        return jitter


def wait_for_submit_slot(destination_key: str, max_per_minute: int) -> float:
    delay = reserve_submit_delay(destination_key, max_per_minute)
    spark_submit_throttle_wait_seconds.observe(delay)
    if delay > 0:
        time.sleep(delay)
    return delay
