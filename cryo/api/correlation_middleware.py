"""Attach correlation IDs to requests and structured logs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from cryo.observability.correlation import correlation_scope, normalize_correlation_id
from cryo.observability.metrics import api_request_duration_seconds, api_requests_total


class CorrelationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = normalize_correlation_id(request.headers.get("x-request-id"))
        correlation_id = normalize_correlation_id(
            request.headers.get("x-correlation-id") or request_id
        )
        assert request_id is not None
        assert correlation_id is not None
        started_at = perf_counter()
        status_code = 500
        with correlation_scope(
            clear=True,
            request_id=request_id,
            correlation_id=correlation_id,
        ):
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Correlation-ID"] = correlation_id
                return response
            finally:
                route = request.scope.get("route")
                route_template = getattr(route, "path", "unmatched")
                method = request.method.upper()
                api_requests_total.labels(
                    route=route_template,
                    method=method,
                    status_class=f"{status_code // 100}xx",
                ).inc()
                api_request_duration_seconds.labels(
                    route=route_template,
                    method=method,
                ).observe(perf_counter() - started_at)
