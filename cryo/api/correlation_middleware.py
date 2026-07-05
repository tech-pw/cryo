"""Attach correlation IDs to requests and structured logs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from cryo.observability.correlation import get_request_id, new_request_id, set_request_id


class CorrelationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get("x-request-id") or request.headers.get("x-correlation-id")
        request_id = incoming.strip() if incoming else new_request_id()
        set_request_id(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = get_request_id() or request_id
        return response
