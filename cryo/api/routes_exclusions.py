from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from cryo.api.deps import CurrentPrincipal, SessionDep, require_operator, require_viewer
from cryo.db.models import MaintenanceExclusion, Source, Table
from cryo.db.repositories.exclusions import (
    ExclusionExistsError,
    create_exclusion,
    delete_exclusion,
    excluded_table_count,
    exclusions_covering,
    list_exclusions,
    set_table_hold,
)
from cryo.db.tenant_scope import get_scoped_resource, tenant_filter_id

router = APIRouter(prefix="/api/v1/maintenance/exclusions", tags=["maintenance-control"])


class MaintenanceExclusionWrite(BaseModel):
    source_id: uuid.UUID
    schema_name: str = Field(min_length=1, max_length=512)
    table_name: str | None = Field(default=None, max_length=512)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("schema_name", "reason")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("table_name")
    @classmethod
    def _strip_table(cls, value: str | None) -> str | None:
        stripped = (value or "").strip()
        return stripped or None


class MaintenanceExclusionRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    source_name: str | None
    schema_name: str
    table_name: str | None
    reason: str
    created_by: str
    created_at: datetime
    expires_at: datetime | None = None
    table_count: int


def _read(
    session: SessionDep, row: MaintenanceExclusion, source_name: str | None
) -> MaintenanceExclusionRead:
    return MaintenanceExclusionRead(
        id=row.id,
        source_id=row.source_id,
        source_name=source_name,
        schema_name=row.schema_name,
        table_name=row.table_name,
        reason=row.reason,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
        table_count=excluded_table_count(session, row),
    )


@router.get(
    "",
    response_model=list[MaintenanceExclusionRead],
    dependencies=[Depends(require_viewer)],
)
def get_exclusions(session: SessionDep) -> list[MaintenanceExclusionRead]:
    tenant_id = tenant_filter_id(session)
    rows = list_exclusions(session, tenant_id=tenant_id)
    names_stmt = select(Source.id, Source.name)
    if tenant_id is not None:
        names_stmt = names_stmt.where(Source.tenant_id == tenant_id)
    names: dict[uuid.UUID, str] = dict(session.execute(names_stmt).tuples().all()) if rows else {}
    return [_read(session, row, names.get(row.source_id)) for row in rows]


@router.post(
    "",
    response_model=MaintenanceExclusionRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_operator)],
)
def post_exclusion(
    payload: MaintenanceExclusionWrite,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> MaintenanceExclusionRead:
    if len(payload.reason) < 3:
        raise HTTPException(status_code=422, detail="reason must be at least 3 characters")
    source = get_scoped_resource(session, Source, payload.source_id, resource_type="Lakehouse")
    try:
        row = create_exclusion(
            session,
            source_id=source.id,
            schema_name=payload.schema_name,
            table_name=payload.table_name,
            reason=payload.reason,
            actor=principal.subject,
        )
    except ExclusionExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    session.refresh(row)
    return _read(session, row, source.name)


table_router = APIRouter(prefix="/api/v1/tables", tags=["maintenance-control"])

MAX_HOLD_DAYS = 90


class TableHoldWrite(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    expires_at: datetime

    @field_validator("reason")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class TableHoldRead(BaseModel):
    held: bool
    scope: Literal["table", "schema"] | None = None
    reason: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    max_hold_days: int = MAX_HOLD_DAYS


def _hold_read(session: SessionDep, table: Table) -> TableHoldRead:
    covering = exclusions_covering(session, table)
    if not covering:
        return TableHoldRead(held=False)
    row = covering[0]
    return TableHoldRead(
        held=True,
        scope="table" if row.table_name is not None else "schema",
        reason=row.reason,
        created_by=row.created_by,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


@table_router.get(
    "/{table_id}/maintenance-hold",
    response_model=TableHoldRead,
    dependencies=[Depends(require_viewer)],
)
def get_table_hold(table_id: uuid.UUID, session: SessionDep) -> TableHoldRead:
    table = get_scoped_resource(session, Table, table_id, resource_type="table")
    return _hold_read(session, table)


@table_router.put(
    "/{table_id}/maintenance-hold",
    response_model=TableHoldRead,
    dependencies=[Depends(require_operator)],
)
def put_table_hold(
    table_id: uuid.UUID,
    payload: TableHoldWrite,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> TableHoldRead:
    table = get_scoped_resource(session, Table, table_id, resource_type="table")
    expires_at = payload.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    if expires_at <= now:
        raise HTTPException(status_code=422, detail="The hold must end in the future.")
    if expires_at > now + timedelta(days=MAX_HOLD_DAYS):
        raise HTTPException(
            status_code=422, detail=f"A hold can last at most {MAX_HOLD_DAYS} days."
        )
    set_table_hold(
        session, table, reason=payload.reason, expires_at=expires_at, actor=principal.subject
    )
    session.commit()
    return _hold_read(session, table)


@table_router.delete(
    "/{table_id}/maintenance-hold",
    response_model=TableHoldRead,
    dependencies=[Depends(require_operator)],
)
def delete_table_hold(
    table_id: uuid.UUID,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> TableHoldRead:
    table = get_scoped_resource(session, Table, table_id, resource_type="table")
    own = next(
        (row for row in exclusions_covering(session, table) if row.table_name is not None), None
    )
    if own is None:
        raise HTTPException(status_code=404, detail="This table has no hold of its own.")
    delete_exclusion(session, own, actor=principal.subject)
    session.commit()
    return _hold_read(session, table)


@router.delete(
    "/{exclusion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_operator)],
)
def remove_exclusion(
    exclusion_id: uuid.UUID,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> Response:
    row = session.get(MaintenanceExclusion, exclusion_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Exclusion not found")
    # An exclusion belongs to its source's tenant; another tenant's looks like a missing one.
    get_scoped_resource(session, Source, row.source_id, resource_type="Exclusion")
    delete_exclusion(session, row, actor=principal.subject)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
