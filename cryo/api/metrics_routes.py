"""Prometheus metrics and deep health.

The root paths are for in-cluster scrapers and probes; the web nginx does not proxy them.
The /api/v1/ops copies are what the browser uses, and require an admin.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from cryo.api.deps import require_admin
from cryo.api.security import Principal
from cryo.config import get_settings
from cryo.observability.fleet_metrics import refresh_fleet_metrics_cached
from cryo.observability.health import health_check

router = APIRouter(tags=["observability"])
ops_router = APIRouter(prefix="/api/v1/ops", tags=["observability"])


def _require_metrics_token(authorization: str | None) -> None:
    """Enforce CRYO_METRICS_BEARER_TOKEN on the scraper path when it is set (#262)."""
    expected = get_settings().metrics_bearer_token
    if not expected:
        return
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        presented.strip().encode(), expected.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Metrics require a bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(
    authorization: Annotated[str | None, Header()] = None,
) -> PlainTextResponse:
    """Expose Prometheus metrics for scraping.

    This endpoint returns all registered Prometheus metrics in the
    standard exposition format. When ``CRYO_METRICS_BEARER_TOKEN`` is set it requires
    ``Authorization: Bearer <token>``.
    """
    _require_metrics_token(authorization)
    return _render_metrics()


def _render_metrics() -> PlainTextResponse:
    refresh_fleet_metrics_cached()
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


@ops_router.get("/metrics", response_class=PlainTextResponse)
def ops_metrics(_principal: Annotated[Principal, Depends(require_admin)]) -> PlainTextResponse:
    return _render_metrics()


@ops_router.get("/healthz/deep")
def ops_healthz_deep(_principal: Annotated[Principal, Depends(require_admin)]) -> JSONResponse:
    return healthz_deep()
