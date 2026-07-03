from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cryo import __version__
from cryo.api.routes import router as api_router
from cryo.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        openapi_tags=[
            {"name": "health", "description": "Service health checks."},
            {"name": "dashboards", "description": "Aggregated datalake health and cost views."},
            {"name": "tables", "description": "Iceberg table inventory and detail views."},
            {"name": "scans", "description": "Scanner trigger requests."},
            {"name": "runs", "description": "Maintenance run and job tracking."},
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

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    app.include_router(api_router)
    return app
