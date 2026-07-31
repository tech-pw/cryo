"""Minimal HTTP health endpoint for background processes (worker, governor, scheduler)."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    readiness_check: Callable[[], bool] = lambda: True
    service_name: str = "cryo"
    # None means "not exposed" -- do_GET 404s /scale-metric in that case rather than serving a
    # fake 0, which a KEDA metrics-api trigger would happily treat as "no demand" and scale to
    # minReplicaCount even though we simply never wired a provider for this process.
    scale_metric_provider: Callable[[], int] | None = None

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/healthz", "/healthz/deep"}:
            ok = self.readiness_check()
            payload = {"status": "ok" if ok else "degraded", "service": self.service_name}
            status = 200 if ok else 503
        elif self.path == "/scale-metric" and self.scale_metric_provider is not None:
            try:
                value = self.scale_metric_provider()
            except Exception:
                logger.exception("scale-metric provider failed for %s", self.service_name)
                self.send_response(503)
                self.end_headers()
                return
            payload = {"value": value}
            status = 200
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("health %s", format % args)


def start_health_server(
    *,
    port: int,
    service_name: str,
    readiness_check: Callable[[], bool] | None = None,
    scale_metric_provider: Callable[[], int] | None = None,
) -> ThreadingHTTPServer:
    # staticmethod() is required here, not incidental: a plain function object stored as a
    # class attribute is a descriptor, so `self.readiness_check()` on the dynamically-built
    # class below would silently bind `self` as its first positional argument -- every
    # zero-arg callable passed in (every caller in this codebase passes one) would then
    # raise "takes 0 positional arguments but 1 was given" the moment it's actually invoked.
    # Confirmed live: this was dormant because no Deployment wires a readinessProbe at
    # /healthz today, so readiness_check was never actually called in production -- but
    # scale_metric_provider (below) is hit by KEDA's metrics-api trigger on every poll, so
    # this would have 503'd immediately without the fix.
    handler = type(
        "BoundHealthHandler",
        (_HealthHandler,),
        {
            "service_name": service_name,
            "readiness_check": staticmethod(readiness_check or (lambda: True)),
            "scale_metric_provider": staticmethod(scale_metric_provider)
            if scale_metric_provider is not None
            else None,
        },
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(
        target=server.serve_forever, name=f"{service_name}-health", daemon=True
    )
    thread.start()
    logger.info("Health server listening on :%s for %s", port, service_name)
    return server
