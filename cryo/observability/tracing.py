"""OpenTelemetry tracing bootstrap for Cryo services."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from cryo.config import Settings, get_settings

logger = logging.getLogger(__name__)

_tracer_provider: Any | None = None


def init_tracing(settings: Settings | None = None, *, service_name: str | None = None) -> None:
    """Configure OTLP tracing when enabled via settings."""
    global _tracer_provider
    settings = settings or get_settings()
    if not settings.otel_enabled:
        return
    if _tracer_provider is not None:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        logger.warning("OpenTelemetry packages not installed; tracing disabled: %s", exc)
        return

    name = service_name or settings.otel_service_name
    resource = Resource.create({"service.name": name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otel_endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer_provider = provider
    logger.info("OpenTelemetry tracing enabled for %s -> %s", name, settings.otel_endpoint)


def instrument_fastapi(app: Any) -> None:
    settings = get_settings()
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        SQLAlchemyInstrumentor().instrument()
        RedisInstrumentor().instrument()
        HTTPXClientInstrumentor().instrument()
        LoggingInstrumentor().instrument(set_logging_format=False)
    except ImportError as exc:
        logger.warning("OpenTelemetry instrumentations unavailable: %s", exc)


def shutdown_tracing() -> None:
    global _tracer_provider
    if _tracer_provider is None:
        return
    try:
        _tracer_provider.shutdown()
    except Exception as exc:
        logger.warning("Failed to shutdown tracer provider: %s", exc)
    _tracer_provider = None


def get_tracer(name: str) -> Any:
    from opentelemetry import trace

    return trace.get_tracer(name)


@contextmanager
def trace_operation(
    name: str,
    *,
    attributes: Mapping[str, str | int | float | bool | None] | None = None,
) -> Iterator[Any | None]:
    """Create an explicit service span without requiring tracing in local/test runs."""
    if not get_settings().otel_enabled:
        yield None
        return
    clean_attributes = {
        key: value for key, value in (attributes or {}).items() if value is not None
    }
    with get_tracer("cryo.operations").start_as_current_span(
        name,
        attributes=clean_attributes,
    ) as span:
        yield span
