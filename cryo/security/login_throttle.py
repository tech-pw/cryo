"""Failed sign-in throttling for ``/auth/login`` and ``/auth/google`` (#262).

Counts failures per account and per client IP in fixed windows. Once either count reaches
its limit, further attempts get 429 with ``Retry-After`` until the window expires, so a
correct password does not get through either. A successful sign-in clears that account's
count. The IP count is not cleared, so one good account can't reset an IP that is guessing
other accounts.

Counts live in Redis so the limit holds across API replicas. When Redis is unavailable the
counters fall back to this process's memory: weaker across replicas, but sign-in keeps
working and each replica still throttles.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_KEY_PREFIX = "cryo:auth:login_failures"


@dataclass(frozen=True)
class ThrottleDecision:
    allowed: bool
    retry_after_seconds: int = 0


def _account_key(account: str) -> str:
    # Hash so Redis never holds the attempted usernames (attackers type passwords there too).
    digest = hashlib.sha256(account.strip().casefold().encode("utf-8")).hexdigest()[:32]
    return f"{_KEY_PREFIX}:acct:{digest}"


def _ip_key(client_ip: str) -> str:
    return f"{_KEY_PREFIX}:ip:{client_ip}"


class _MemoryCounters:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._counts: dict[str, tuple[int, float]] = {}

    def get(self, key: str) -> tuple[int, int]:
        with self._lock:
            entry = self._counts.get(key)
            if entry is None:
                return 0, 0
            count, expires_at = entry
            remaining = expires_at - self._clock()
            if remaining <= 0:
                del self._counts[key]
                return 0, 0
            return count, max(1, int(remaining + 0.999))

    def incr(self, key: str, window_seconds: int) -> None:
        with self._lock:
            now = self._clock()
            count, expires_at = self._counts.get(key, (0, 0.0))
            if expires_at <= now:
                count, expires_at = 0, now + window_seconds
            self._counts[key] = (count + 1, expires_at)

    def delete(self, key: str) -> None:
        with self._lock:
            self._counts.pop(key, None)


class LoginThrottle:
    def __init__(
        self,
        *,
        redis_client: Any | None,
        max_account_failures: int,
        max_ip_failures: int,
        window_seconds: int,
        enabled: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._redis = redis_client
        self._max_account = max_account_failures
        self._max_ip = max_ip_failures
        self._window = window_seconds
        self._enabled = enabled
        self._memory = _MemoryCounters(clock)

    def _keys(self, account: str | None, client_ip: str | None) -> list[tuple[str, int]]:
        keys: list[tuple[str, int]] = []
        if account:
            keys.append((_account_key(account), self._max_account))
        if client_ip:
            keys.append((_ip_key(client_ip), self._max_ip))
        return keys

    def _redis_get(self, key: str) -> tuple[int, int] | None:
        if self._redis is None:
            return None
        try:
            pipe = self._redis.pipeline()
            pipe.get(key)
            pipe.ttl(key)
            raw, ttl = pipe.execute()
        except Exception:  # noqa: BLE001 - Redis down: fall back to memory
            logger.warning("Login throttle: Redis read failed; using in-process counters")
            return None
        count = int(raw) if raw is not None else 0
        ttl_seconds = int(ttl) if ttl is not None and int(ttl) > 0 else self._window
        return count, ttl_seconds

    def check(self, *, account: str | None, client_ip: str | None) -> ThrottleDecision:
        """Whether a sign-in attempt may proceed. Call before checking credentials."""
        if not self._enabled:
            return ThrottleDecision(True)
        retry_after = 0
        for key, limit in self._keys(account, client_ip):
            state = self._redis_get(key)
            count, ttl = state if state is not None else self._memory.get(key)
            if count >= limit:
                retry_after = max(retry_after, ttl or self._window)
        if retry_after:
            return ThrottleDecision(False, retry_after)
        return ThrottleDecision(True)

    def record_failure(self, *, account: str | None, client_ip: str | None) -> None:
        if not self._enabled:
            return
        for key, _limit in self._keys(account, client_ip):
            if self._redis is not None:
                try:
                    # SET NX EX then INCR in one MULTI: the window starts at the first failure
                    # and later failures don't extend it, so a locked account always unlocks
                    # within one window. (EXPIRE NX would need Redis 7.)
                    pipe = self._redis.pipeline()
                    pipe.set(key, 0, ex=self._window, nx=True)
                    pipe.incr(key)
                    pipe.execute()
                    continue
                except Exception:  # noqa: BLE001
                    logger.warning("Login throttle: Redis write failed; using in-process counters")
            self._memory.incr(key, self._window)

    def record_success(self, *, account: str) -> None:
        if not self._enabled:
            return
        key = _account_key(account)
        if self._redis is not None:
            try:
                self._redis.delete(key)
            except Exception:  # noqa: BLE001
                logger.warning("Login throttle: Redis delete failed")
        self._memory.delete(key)


_throttle: LoginThrottle | None = None
_throttle_lock = threading.Lock()


def get_login_throttle() -> LoginThrottle:
    global _throttle
    with _throttle_lock:
        if _throttle is None:
            from cryo.config import get_settings
            from cryo.queue.redis_queue import get_redis_client

            settings = get_settings()
            _throttle = LoginThrottle(
                redis_client=get_redis_client(),
                max_account_failures=settings.auth_login_max_failures,
                max_ip_failures=settings.auth_login_ip_max_failures,
                window_seconds=settings.auth_login_window_seconds,
                enabled=settings.auth_login_throttle_enabled,
            )
        return _throttle


def set_login_throttle(throttle: LoginThrottle | None) -> None:
    """Swap the process-wide throttle (tests)."""
    global _throttle
    with _throttle_lock:
        _throttle = throttle
