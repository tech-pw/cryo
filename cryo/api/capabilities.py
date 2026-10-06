from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from cryo.api.deps import require_viewer
from cryo.api.security import Principal
from cryo.capabilities import CAPABILITY_MANIFEST, CapabilityManifest

router = APIRouter(prefix="/api/v1", tags=["capabilities"])

NATIVE_ENGINE_SQL_FALLBACK = "the configured Spark SQL fallback"


class RuntimeFlags(BaseModel):
    native_engine_enabled: bool
    maintenance_writes_enabled: bool
    native_engine_fallback: str


@router.get("/capabilities", response_model=CapabilityManifest)
def get_capabilities(
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> CapabilityManifest:
    """Return Cryo's versioned catalog and engine capability contract."""
    return CAPABILITY_MANIFEST


@router.get("/runtime", response_model=RuntimeFlags)
def get_runtime(
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> RuntimeFlags:
    """Return read-only process flags for this install."""
    from cryo.config import get_settings
    from cryo.maintenance.writes import maintenance_writes_enabled

    settings = get_settings()
    return RuntimeFlags(
        native_engine_enabled=settings.native_engine_enabled,
        maintenance_writes_enabled=maintenance_writes_enabled(),
        native_engine_fallback=NATIVE_ENGINE_SQL_FALLBACK,
    )
