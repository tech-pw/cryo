from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from cryo.api.deps import require_viewer
from cryo.api.security import Principal
from cryo.capabilities import CAPABILITY_MANIFEST, CapabilityManifest

router = APIRouter(prefix="/api/v1", tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilityManifest)
def get_capabilities(
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> CapabilityManifest:
    """Return Cryo's versioned catalog and engine capability contract."""
    return CAPABILITY_MANIFEST
