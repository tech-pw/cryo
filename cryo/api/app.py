from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cryo import __version__
from cryo.api.advanced_routes import router as advanced_router
from cryo.api.anomalies_routes import router as anomalies_router
from cryo.api.auth_routes import router as auth_router
from cryo.api.capabilities import router as capabilities_router
from cryo.api.correlation_middleware import CorrelationMiddleware
from cryo.api.cost_insights_routes import router as cost_insights_router
from cryo.api.dashboard_routes import router as dashboard_router
from cryo.api.evidence_routes import router as evidence_router
from cryo.api.extensions import router as extensions_router
from cryo.api.maintenance_control import router as maintenance_control_router
from cryo.api.maintenance_safety import router as maintenance_safety_router
from cryo.api.metrics_routes import router as metrics_router
from cryo.api.middleware import RateLimitMiddleware
from cryo.api.phase8_routes import router as phase8_router
from cryo.api.routes import router as api_router
from cryo.api.routes_dedup import router as dedup_router
from cryo.api.routes_property_recommendations import router as property_recommendations_router
from cryo.api.routes_smart_maintenance import router as smart_maintenance_router
from cryo.api.routes_sources import router as sources_router
from cryo.api.s3_log_config_routes import router as s3_log_config_router
from cryo.api.search_routes import router as search_router
from cryo.config import Settings, get_settings
from cryo.observability.logging import configure_logging
from cryo.observability.tracing import init_tracing, instrument_fastapi, shutdown_tracing


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    shutdown_tracing()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(
        level="DEBUG" if settings.environment == "dev" else "INFO",
        json_format=settings.environment in {"staging", "prod"},
        environment=settings.environment,
    )
    init_tracing(settings, service_name=f"{settings.otel_service_name}-api")
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "health", "description": "Service health checks."},
            {"name": "dashboards", "description": "Aggregated datalake health and cost views."},
            {"name": "search", "description": "Global catalog search across sources and tables."},
            {
                "name": "capabilities",
                "description": "Versioned catalog and maintenance-engine support contract.",
            },
            {"name": "tables", "description": "Iceberg table inventory and detail views."},
            {"name": "scans", "description": "Scanner trigger requests."},
            {"name": "runs", "description": "Maintenance run and job tracking."},
            {
                "name": "maintenance-control",
                "description": "Durable emergency pause and resume controls.",
            },
            {
                "name": "maintenance-safety",
                "description": "Intent, attempt, effect, and reconciliation state.",
            },
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
            {
                "name": "cost-insights",
                "description": (
                    "Real per-bucket / per-table AWS cost attribution from CUR and S3 "
                    "Inventory (CE-grade insight, free — delivered report files only)."
                ),
            },
            {
                "name": "s3-request-cost",
                "description": (
                    "Per-table S3 GET/PUT/LIST request-cost tracking from S3 server access logs."
                ),
            },
            {"name": "observability", "description": "Prometheus metrics and health checks."},
            {
                "name": "anomalies",
                "description": "Catalog warehouse-path collisions (shared or nested S3 locations).",
            },
            {
                "name": "evidence",
                "description": "Stable privacy-safe maintenance and release evidence exports.",
            },
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
    app.add_middleware(CorrelationMiddleware)

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    @app.get("/readyz", tags=["health"])
    def readyz() -> dict[str, str]:
        return {"status": "ready", "environment": settings.environment}

    app.include_router(api_router)
    app.include_router(property_recommendations_router)
    app.include_router(smart_maintenance_router)
    app.include_router(sources_router)
    app.include_router(auth_router)
    app.include_router(extensions_router)
    app.include_router(phase8_router)
    app.include_router(dashboard_router)
    app.include_router(search_router)
    app.include_router(capabilities_router)
    app.include_router(advanced_router)
    app.include_router(metrics_router)
    app.include_router(maintenance_control_router)
    app.include_router(maintenance_safety_router)
    app.include_router(cost_insights_router)
    app.include_router(s3_log_config_router)
    app.include_router(evidence_router)
    app.include_router(dedup_router)
    app.include_router(anomalies_router)
    instrument_fastapi(app)

    # Observation Lakehouse (SANER 2026): long-term archive of every actuation. Wired at startup
    # so the Redis/InMemory event bus instances pick it up via the process-wide handle.
    from cryo.observability.observation_lakehouse import ObservationLakehouse
    from cryo.queue.run_events import attach_observation_lakehouse

    attach_observation_lakehouse(
        ObservationLakehouse(storage_path=settings.observation_lakehouse_path)
    )

    return app


app = create_app()
