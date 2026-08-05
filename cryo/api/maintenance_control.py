from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select

from cryo.api.deps import CurrentPrincipal, SessionDep, require_operator, require_viewer
from cryo.api.security import Role
from cryo.db.models import Destination, MaintenanceControlState, Tenant
from cryo.db.repositories.intents import set_pause_state
from cryo.observability.metrics import maintenance_pause_changes_total

router = APIRouter(prefix="/api/v1/maintenance/control", tags=["maintenance-control"])


class ControlScope(StrEnum):
    GLOBAL = "global"
    TENANT = "tenant"
    DESTINATION = "destination"


class MaintenanceControlWrite(BaseModel):
    scope_type: ControlScope
    scope_id: str | None = None
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def validate_scope_id(self) -> MaintenanceControlWrite:
        if self.scope_type is ControlScope.GLOBAL:
            if self.scope_id not in {None, "global"}:
                raise ValueError("global pause scope cannot have a custom scope_id")
        elif not self.scope_id:
            raise ValueError(f"{self.scope_type.value} pause scope requires scope_id")
        return self


class MaintenanceControlRead(BaseModel):
    scope_type: ControlScope
    scope_id: str
    paused: bool
    generation: int
    reason: str | None
    changed_by: str
    updated_at: datetime


@router.get(
    "",
    response_model=list[MaintenanceControlRead],
    dependencies=[Depends(require_viewer)],
)
def list_maintenance_control(
    session: SessionDep,
    principal: CurrentPrincipal,
) -> list[MaintenanceControlRead]:
    query = select(MaintenanceControlState)
    if not principal.can(Role.ADMIN):
        visible = [
            (
                MaintenanceControlState.scope_type == ControlScope.GLOBAL.value,
                MaintenanceControlState.scope_id == "global",
            )
        ]
        if principal.tenant_id is not None:
            visible.append(
                (
                    MaintenanceControlState.scope_type == ControlScope.TENANT.value,
                    MaintenanceControlState.scope_id == str(principal.tenant_id),
                )
            )
            destination_ids = [
                str(destination_id)
                for destination_id in session.scalars(
                    select(Destination.id).where(Destination.tenant_id == principal.tenant_id)
                )
            ]
            visible.append(
                (
                    MaintenanceControlState.scope_type == ControlScope.DESTINATION.value,
                    MaintenanceControlState.scope_id.in_(destination_ids),
                )
            )
        query = query.where(or_(*(left & right for left, right in visible)))
    rows = session.scalars(
        query.order_by(
            MaintenanceControlState.scope_type,
            MaintenanceControlState.scope_id,
        )
    )
    return [_control_read(row) for row in rows]


@router.post(
    "/pause",
    response_model=MaintenanceControlRead,
    dependencies=[Depends(require_operator)],
)
def pause_maintenance(
    payload: MaintenanceControlWrite,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> MaintenanceControlRead:
    return _set_control(payload, paused=True, session=session, principal=principal)


@router.post(
    "/resume",
    response_model=MaintenanceControlRead,
    dependencies=[Depends(require_operator)],
)
def resume_maintenance(
    payload: MaintenanceControlWrite,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> MaintenanceControlRead:
    return _set_control(payload, paused=False, session=session, principal=principal)


def _set_control(
    payload: MaintenanceControlWrite,
    *,
    paused: bool,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> MaintenanceControlRead:
    scope_id = _authorize_scope(payload, session=session, principal=principal)
    row = set_pause_state(
        session,
        scope_type=payload.scope_type.value,
        scope_id=scope_id,
        paused=paused,
        changed_by=principal.subject,
        reason=payload.reason,
    )
    session.commit()
    session.refresh(row)
    maintenance_pause_changes_total.labels(
        scope_type=payload.scope_type.value,
        action="pause" if paused else "resume",
    ).inc()
    return _control_read(row)


def _authorize_scope(
    payload: MaintenanceControlWrite,
    *,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> str:
    if payload.scope_type is ControlScope.GLOBAL:
        if not principal.can(Role.ADMIN):
            raise HTTPException(status_code=403, detail="Global pause requires admin role")
        return "global"
    assert payload.scope_id is not None
    if payload.scope_type is ControlScope.TENANT:
        try:
            tenant_id = UUID(payload.scope_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid tenant scope_id") from exc
        if session.get(Tenant, tenant_id) is None:
            raise HTTPException(status_code=404, detail="Tenant not found")
        if not principal.can(Role.ADMIN) and principal.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="Cannot control another tenant")
        return str(tenant_id)
    try:
        destination_id = UUID(payload.scope_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid destination scope_id") from exc
    destination = session.get(Destination, destination_id)
    if destination is None:
        raise HTTPException(status_code=404, detail="Destination not found")
    if not principal.can(Role.ADMIN) and destination.tenant_id != principal.tenant_id:
        raise HTTPException(status_code=403, detail="Cannot control another tenant's destination")
    return str(destination_id)


def _control_read(row: MaintenanceControlState) -> MaintenanceControlRead:
    return MaintenanceControlRead(
        scope_type=ControlScope(row.scope_type),
        scope_id=row.scope_id,
        paused=row.paused,
        generation=row.generation,
        reason=row.reason,
        changed_by=row.changed_by,
        updated_at=row.updated_at,
    )
