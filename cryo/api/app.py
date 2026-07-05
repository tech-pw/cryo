from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cryo import __version__
from cryo.api.advanced_routes import router as advanced_router
from cryo.api.dashboard_routes import router as dashboard_router
from cryo.api.extensions import router as extensions_router
from cryo.api.metrics_routes import router as metrics_router
from cryo.api.middleware import RateLimitMiddleware
from cryo.api.phase8_routes import router as phase8_router
from cryo.api.routes import router as api_router
from cryo.config import Settings, get_settings
from cryo.observability.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(
        level="DEBUG" if settings.environment == "dev" else "INFO",
        json_format=settings.environment in {"staging", "prod"},
        environment=settings.environment,
    )
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        openapi_tags=[
            {"name": "health", "description": "Service health checks."},
            {"name": "dashboards", "description": "Aggregated datalake health and cost views."},
            {"name": "tables", "description": "Iceberg table inventory and detail views."},
            {"name": "scans", "description": "Scanner trigger requests."},
            {"name": "runs", "description": "Maintenance run and job tracking."},
            {"name": "ai", "description": "Anomaly detection, explain, Copilot, and MCP tools."},
            {
                "name": "compliance",
                "description": "Compliance status, protected snapshots, catalog latency.",
            },
            {"name": "phase8", "description": "Adaptive routing and data tiering policies."},
            {
                "name": "advanced",
                "description": (
                    "Advanced features: lineage, anomalies, approvals, quality, signals, FinOps."
                ),
            },
            {"name": "observability", "description": "Prometheus metrics and health checks."},
        ],
    )
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_middleware(
        RateLimitMiddleware,
        enabled=settings.environment != "test",
        rate=settings.api_rate_limit_rps,
        capacity=settings.api_rate_limit_burst,
    )

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    app.include_router(api_router)
    app.include_router(extensions_router)
    app.include_router(phase8_router)
    app.include_router(dashboard_router)
    app.include_router(advanced_router)
    app.include_router(metrics_router)
    return app


app = create_app()
