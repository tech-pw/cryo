"""Tests for cryo/observability/health_server.py's /scale-metric endpoint.

Regression coverage for 2026-07-31: the KEDA worker ScaledObject now scales off this
endpoint instead of Redis admitted-list length (see keda-worker-scaledobject.yaml and
cli.py's _worker_scale_metric) -- these tests pin the wire contract KEDA's metrics-api
trigger depends on (200 + {"value": N} when a provider is wired, 404 when it isn't, 503 on
a provider exception).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from cryo.observability.health_server import start_health_server


def _get(port: int, path: str) -> tuple[int, dict[str, object] | None]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, (json.loads(body) if body else None)


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_scale_metric_returns_provider_value() -> None:
    port = _free_port()
    server = start_health_server(
        port=port, service_name="worker", scale_metric_provider=lambda: 7
    )
    try:
        status, body = _get(port, "/scale-metric")
        assert status == 200
        assert body == {"value": 7}
    finally:
        server.shutdown()


def test_scale_metric_404s_without_a_provider() -> None:
    """Regression: must not silently report 0 (which KEDA would treat as "no demand" and
    scale to minReplicaCount) just because this process never wired a provider."""
    port = _free_port()
    server = start_health_server(port=port, service_name="governor")
    try:
        status, _ = _get(port, "/scale-metric")
        assert status == 404
    finally:
        server.shutdown()


def test_scale_metric_503s_on_provider_error() -> None:
    def _boom() -> int:
        raise RuntimeError("db unavailable")

    port = _free_port()
    server = start_health_server(port=port, service_name="worker", scale_metric_provider=_boom)
    try:
        status, _ = _get(port, "/scale-metric")
        assert status == 503
    finally:
        server.shutdown()


def test_healthz_still_works_alongside_scale_metric() -> None:
    port = _free_port()
    server = start_health_server(
        port=port, service_name="worker", scale_metric_provider=lambda: 0
    )
    try:
        status, body = _get(port, "/healthz")
        assert status == 200
        assert body == {"status": "ok", "service": "worker"}
    finally:
        server.shutdown()
