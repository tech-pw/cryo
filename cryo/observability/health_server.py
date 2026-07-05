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

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/healthz", "/healthz/deep"}:
            self.send_response(404)
            self.end_headers()
            return
        ok = self.readiness_check()
        payload = {"status": "ok" if ok else "degraded", "service": self.service_name}
        body = json.dumps(payload).encode()
        self.send_response(200 if ok else 503)
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
) -> ThreadingHTTPServer:
    handler = type(
        "BoundHealthHandler",
        (_HealthHandler,),
        {
            "service_name": service_name,
            "readiness_check": readiness_check or (lambda: True),
        },
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)
    thread = threading.Thread(
        target=server.serve_forever, name=f"{service_name}-health", daemon=True
    )
    thread.start()
    logger.info("Health server listening on :%s for %s", port, service_name)
    return server
