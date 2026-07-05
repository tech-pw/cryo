"""Prometheus metrics HTTP endpoint for Cryo.

Exposes /metrics endpoint for Prometheus scraping.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from cryo.observability.health import health_check

router = APIRouter(tags=["observability"])


@router.get("/metrics", response_class=PlainTextResponse)
def metrics() -> PlainTextResponse:
    """Expose Prometheus metrics for scraping.

    This endpoint returns all registered Prometheus metrics in the
    standard exposition format.
    """
    return PlainTextResponse(
        content=generate_latest().decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )


@router.get("/healthz/deep")
def healthz_deep() -> JSONResponse:
    """Deep health check covering database and Redis connectivity."""
    result = health_check()
    status_code = 200 if result["status"] == "ok" else 503
    return JSONResponse(content=result, status_code=status_code)
