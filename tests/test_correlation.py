from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from cryo.api.correlation_middleware import CorrelationMiddleware
from cryo.observability.correlation import (
    clear_context,
    correlation_fields,
    correlation_scope,
    normalize_correlation_id,
    set_context,
)


def test_normalize_correlation_id_rejects_unbounded_and_log_unsafe_input() -> None:
    assert normalize_correlation_id(" safe.id:42 ") == "safe.id:42"

    control_character = normalize_correlation_id("unsafe\nvalue")
    oversized = normalize_correlation_id("x" * 129)

    assert uuid.UUID(str(control_character))
    assert uuid.UUID(str(oversized))
    assert normalize_correlation_id("unsafe\nvalue", generate=False) is None


def test_correlation_scope_restores_nested_context() -> None:
    clear_context()
    assert correlation_fields() == {}

    with correlation_scope(clear=True, correlation_id="root", run_id="run-1"):
        set_context(job_id="job-1")
        with correlation_scope(effect_id="effect-1"):
            assert correlation_fields()["effect_id"] == "effect-1"
        assert "effect_id" not in correlation_fields()
        assert correlation_fields()["job_id"] == "job-1"

    assert correlation_fields() == {}


def test_middleware_propagates_root_id_without_leaking_job_context() -> None:
    app = FastAPI()
    app.add_middleware(CorrelationMiddleware)

    @app.get("/context/{item_id}")
    def context(item_id: str) -> dict[str, str]:
        if item_id == "bind":
            set_context(job_id="job-from-request")
        return correlation_fields()

    with TestClient(app) as client:
        first = client.get(
            "/context/bind",
            headers={
                "X-Request-ID": "request-1",
                "X-Correlation-ID": "correlation-1",
            },
        )
        second = client.get("/context/next")

    assert first.headers["X-Request-ID"] == "request-1"
    assert first.headers["X-Correlation-ID"] == "correlation-1"
    assert first.json()["job_id"] == "job-from-request"
    assert "job_id" not in second.json()
    assert second.json()["request_id"] == second.headers["X-Request-ID"]
    assert second.json()["correlation_id"] == second.headers["X-Correlation-ID"]

    metrics = generate_latest().decode()
    assert 'route="/context/{item_id}"' in metrics
    assert 'route="/context/bind"' not in metrics
