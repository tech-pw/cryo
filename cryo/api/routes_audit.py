from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.api.deps import SessionDep, require_viewer
from cryo.api.schemas import AuditLogRead
from cryo.db.models import AuditLog, Table
from cryo.db.tenant_scope import require_tenant

AUDIT_LIST_MAX = 500

router = APIRouter(prefix="/api/v1", tags=["audit"])


class FleetAuditItem(AuditLogRead):
    schema_name: str | None = None
    table_name: str | None = None


class FleetAuditResponse(BaseModel):
    items: list[FleetAuditItem]
    limit: int


def _clamp_limit(limit: int) -> int:
    return min(max(limit, 1), AUDIT_LIST_MAX)


def fleet_audit_query(
    session: Session,
    *,
    limit: int,
    action: str | None,
    status: str | None,
    engine: str | None,
) -> Any:
    capped = _clamp_limit(limit)
    stmt = (
        select(AuditLog, Table.schema_name, Table.table_name)
        .outerjoin(Table, AuditLog.table_id == Table.id)
        .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
        .limit(capped)
    )
    # Sign-in audit rows carry attempted usernames; they are admin-only via /auth/audit.
    stmt = stmt.where(~AuditLog.action.startswith("auth."))
    tenant_id = require_tenant(session)
    if tenant_id.int != 0:
        stmt = stmt.where(Table.tenant_id == tenant_id)
    stmt = stmt.where(~AuditLog.action.like("auth.%"))
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if status:
        stmt = stmt.where(AuditLog.status == status)
    if engine:
        stmt = stmt.where(AuditLog.engine == engine)
    return stmt


def list_fleet_audit(
    session: Session,
    *,
    limit: int,
    action: str | None = None,
    status: str | None = None,
    engine: str | None = None,
) -> FleetAuditResponse:
    capped = _clamp_limit(limit)
    rows = session.execute(
        fleet_audit_query(session, limit=capped, action=action, status=status, engine=engine)
    ).all()
    items: list[FleetAuditItem] = []
    for row, schema_name, table_name in rows:
        item = FleetAuditItem.model_validate(row)
        item.schema_name = schema_name
        item.table_name = table_name
        items.append(item)
    return FleetAuditResponse(items=items, limit=capped)


@router.get("/audit", response_model=FleetAuditResponse, dependencies=[Depends(require_viewer)])
def get_fleet_audit(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=AUDIT_LIST_MAX)] = 100,
    action: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    engine: Annotated[str | None, Query()] = None,
) -> FleetAuditResponse:
    return list_fleet_audit(session, limit=limit, action=action, status=status, engine=engine)
