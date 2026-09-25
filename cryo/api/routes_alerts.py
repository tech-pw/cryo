"""In-app fleet cost alerts. Drift stays on /advanced/anomalies."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from cryo.api.deps import SessionDep, require_viewer
from cryo.api.security import Principal
from cryo.reporting.alerts import collect_cost_alert

router = APIRouter(prefix="/api/v1", tags=["alerts"])


class FleetAlertRead(BaseModel):
    kind: Literal["cost"]
    severity: Literal["low", "high"]
    summary: str
    explained: bool


@router.get("/alerts", response_model=list[FleetAlertRead])
def list_fleet_alerts(
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> list[FleetAlertRead]:
    alert = collect_cost_alert(session)
    if alert is None:
        return []
    return [
        FleetAlertRead(
            kind=alert.kind,
            severity=alert.severity,
            summary=alert.summary,
            explained=alert.explained,
        )
    ]
