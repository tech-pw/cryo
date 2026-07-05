"""Deep health checks for Cryo dependencies."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from cryo.config import Settings, get_settings
from cryo.db.session import SessionLocal


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
    except Exception as exc:
        checks["database"] = {"status": "error", "detail": str(exc)}
        overall = "degraded"

    try:
        import redis

        client = redis.from_url(settings.redis_url, decode_responses=False)
        client.ping()
        checks["redis"] = {"status": "ok"}
    except Exception as exc:
        checks["redis"] = {"status": "error", "detail": str(exc)}
        overall = "degraded"

    return {"status": overall, "checks": checks}
