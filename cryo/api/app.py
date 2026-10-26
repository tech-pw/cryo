from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from cryo import __version__
from cryo.api.advanced_routes import router as advanced_router
from cryo.api.anomalies_routes import router as anomalies_router
from cryo.api.assistant_routes import router as assistant_router
from cryo.api.auth_routes import router as auth_router
from cryo.api.capabilities import router as capabilities_router
from cryo.api.correlation_middleware import CorrelationMiddleware
from cryo.api.cost_insights_routes import router as cost_insights_router
from cryo.api.dashboard_routes import router as dashboard_router
from cryo.api.deps import require_viewer
from cryo.api.evidence_routes import router as evidence_router
from cryo.api.extensions import router as extensions_router
from cryo.api.maintenance_control import router as maintenance_control_router
from cryo.api.maintenance_safety import router as maintenance_safety_router
from cryo.api.metrics_routes import ops_router as ops_metrics_router
from cryo.api.metrics_routes import router as metrics_router
from cryo.api.middleware import RateLimitMiddleware
from cryo.api.phase8_routes import router as phase8_router
from cryo.api.routes import router as api_router
from cryo.api.routes_alerts import router as alerts_router
from cryo.api.routes_archive import router as archive_router
from cryo.api.routes_audit import router as audit_router
from cryo.api.routes_batch import router as batch_router
from cryo.api.routes_column_usage import router as column_usage_router
from cryo.api.routes_cost_attribution import router as cost_attribution_router
from cryo.api.routes_dark_tables import router as dark_tables_router
from cryo.api.routes_dedup import router as dedup_router
from cryo.api.routes_engine_load import router as engine_load_router
from cryo.api.routes_exclusions import router as maintenance_exclusions_router
from cryo.api.routes_exclusions import table_router as table_hold_router
from cryo.api.routes_explorer_views import router as explorer_views_router
from cryo.api.routes_funnel import router as funnel_router
from cryo.api.routes_impact import router as impact_router
from cryo.api.routes_partition_access import router as partition_access_router
from cryo.api.routes_path_to_a import router as path_to_a_router
from cryo.api.routes_preset_lifecycle import router as preset_lifecycle_router
from cryo.api.routes_property_recommendations import router as property_recommendations_router
from cryo.api.routes_quality_constraints import router as quality_constraints_router
from cryo.api.routes_skip_history import router as skip_history_router
from cryo.api.routes_smart_maintenance import router as smart_maintenance_router
from cryo.api.routes_snapshots import router as snapshots_router
from cryo.api.routes_sources import router as sources_router
from cryo.api.routes_table_config import router as table_config_router
from cryo.api.routes_table_maintenance import router as table_maintenance_router
from cryo.api.s3_log_config_routes import router as s3_log_config_router
from cryo.api.search_routes import router as search_router
from cryo.api.slack_routes import router as slack_interactions_router
from cryo.build_info import build_info, cached_schema_revision
from cryo.config import Settings, get_settings
from cryo.mcp.http import mount_mcp
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
    docs_served = settings.api_docs_served()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        # #262: the schema lists every route and model; prod keeps it private by default.
        docs_url="/docs" if docs_served else None,
        redoc_url="/redoc" if docs_served else None,
        openapi_url="/openapi.json" if docs_served else None,
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
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        # Liveness only: no database call, and no build identity for unauthenticated callers.
        return {"status": "ok", "environment": settings.environment}

    @app.get("/api/v1/version", tags=["health"], dependencies=[Depends(require_viewer)])
    def version() -> dict[str, str | None]:
        """Which build is running (#263). Needs a viewer token: the exact build and schema
        revision tell an attacker which advisories apply. schema_revision is the Alembic
        revision in the database, null when the database can't be reached."""
        return {**build_info().as_dict(), "schema_revision": cached_schema_revision()}

    @app.get("/readyz", tags=["health"])
    def readyz() -> dict[str, str]:
        return {"status": "ready", "environment": settings.environment}

    @app.get("/scale-metric", tags=["health"])
    def scale_metric() -> dict[str, int]:
        """Scanner-worker demand, in the same {"value": N} shape the CLI health server serves.

        The scanner-worker Deployment scales to zero, so its KEDA trigger cannot poll the
        scanner-worker Service -- at 0 replicas that Service has no endpoints and the metric
        would be unreachable exactly when it needs to report "work arrived, scale up",
        wedging the Deployment at zero forever. The API is always up and is where ad-hoc
        scans are created in the first place (create_scan_trigger), so it can always answer.

        Unauthenticated like /healthz and /readyz: KEDA sends no credentials, and the body
        is a single integer queue depth.
        """
        from cryo.db.repositories.scanner import pending_scan_chunk_count
        from cryo.db.session import SessionLocal

        with SessionLocal() as session:
            return {"value": pending_scan_chunk_count(session)}

    app.include_router(api_router)
    app.include_router(preset_lifecycle_router)
    app.include_router(assistant_router)
    app.include_router(property_recommendations_router)
    app.include_router(path_to_a_router)
    app.include_router(table_config_router)
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
    app.include_router(ops_metrics_router)
    app.include_router(maintenance_control_router)
    app.include_router(maintenance_exclusions_router)
    app.include_router(table_hold_router)
    app.include_router(column_usage_router)
    app.include_router(maintenance_safety_router)
    app.include_router(cost_insights_router)
    app.include_router(s3_log_config_router)
    app.include_router(evidence_router)
    app.include_router(dedup_router)
    app.include_router(dark_tables_router)
    app.include_router(cost_attribution_router)
    app.include_router(anomalies_router)
    app.include_router(partition_access_router)
    app.include_router(archive_router)
    app.include_router(quality_constraints_router)
    app.include_router(explorer_views_router)
    app.include_router(alerts_router)
    app.include_router(audit_router)
    app.include_router(impact_router)
    app.include_router(snapshots_router)
    app.include_router(skip_history_router)
    app.include_router(engine_load_router)
    app.include_router(batch_router)
    app.include_router(funnel_router)
    app.include_router(table_maintenance_router)
    if settings.slack_signing_secret:
        app.include_router(slack_interactions_router)
    mount_mcp(app, settings)
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
