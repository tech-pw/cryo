"""API rate limiting middleware (in-memory token bucket)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


class TokenBucket:
    """Simple token-bucket limiter keyed per client identifier."""

    def __init__(self, *, rate: float, capacity: int) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()

    def consume(self, tokens: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.last_refill = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


def client_ip(request: Request) -> str:
    """Best-effort client address: first X-Forwarded-For hop, else the socket peer.

    The forwarded header is client-controlled when the API is reached directly, so callers
    must not rely on this alone for security decisions (login throttling also counts per
    account)."""
    return _client_key(request)


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def _is_expensive_request(method: str, path: str) -> bool:
    if method not in {"POST", "GET"}:
        return False
    if path == "/api/v1/maintenance/preview" and method == "POST":
        return True
    if path == "/api/v1/scans" and method == "POST":
        return True
    if path.startswith("/api/v1/advanced/anomalies") and method == "GET":
        return True
    if path == "/api/v1/anomalies/shared-s3/refresh" and method == "POST":
        return True
    if path == "/api/v1/routing/preview" and method == "POST":
        return True
    return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate-limit expensive API endpoints per client IP."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        rate: float = 0.5,
        capacity: int = 10,
        enabled: bool = True,
    ) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.rate = rate
        self.capacity = capacity
        self._buckets: dict[str, TokenBucket] = {}

    def _bucket_for(self, key: str) -> TokenBucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(rate=self.rate, capacity=self.capacity)
            self._buckets[key] = bucket
        return bucket

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not self.enabled or not _is_expensive_request(request.method, request.url.path):
            return await call_next(request)

        key = _client_key(request)
        if not self._bucket_for(key).consume():
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded for expensive endpoint. Retry shortly.",
                },
                headers={"Retry-After": "2"},
            )

        return await call_next(request)
