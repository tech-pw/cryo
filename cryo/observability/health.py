"""Deep health checks for Cryo dependencies."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from cryo.config import Settings, get_settings
from cryo.db.session import SessionLocal

logger = logging.getLogger(__name__)


def health_check(*, settings: Settings | None = None) -> dict[str, Any]:
    """Check database and Redis connectivity.

    Returns a dict with overall status and per-dependency check results.
    """
    settings = settings or get_settings()
    checks: dict[str, dict[str, str]] = {}
    overall = "ok"

    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception:
        logger.exception("database health check failed")
        checks["database"] = {"status": "error"}
        overall = "degraded"

    try:
        import redis

        client = redis.from_url(settings.redis_url, decode_responses=False)
        client.ping()
        checks["redis"] = {"status": "ok"}
    except Exception:
        logger.exception("redis health check failed")
        checks["redis"] = {"status": "error"}
        overall = "degraded"

    return {"status": overall, "checks": checks}
