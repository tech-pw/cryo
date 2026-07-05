"""Tests for OpenTelemetry tracing bootstrap."""

from __future__ import annotations

from cryo.config import Settings
from cryo.observability.tracing import init_tracing, shutdown_tracing


def test_tracing_disabled_by_default() -> None:
    settings = Settings(otel_enabled=False)
    init_tracing(settings, service_name="cryo-test")
    shutdown_tracing()


def test_tracing_init_when_enabled(monkeypatch) -> None:
    settings = Settings(otel_enabled=True, otel_endpoint="http://localhost:4318/v1/traces")
    init_tracing(settings, service_name="cryo-test")
    shutdown_tracing()
