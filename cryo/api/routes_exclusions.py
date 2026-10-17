from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from cryo.api.deps import CurrentPrincipal, SessionDep, require_operator, require_viewer
from cryo.db.models import MaintenanceExclusion, Source
from cryo.db.repositories.exclusions import (
    ExclusionExistsError,
    create_exclusion,
    delete_exclusion,
    excluded_table_count,
    list_exclusions,
)

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
        table_count=excluded_table_count(session, row),
    )


@router.get(
    "",
    response_model=list[MaintenanceExclusionRead],
    dependencies=[Depends(require_viewer)],
)
def get_exclusions(session: SessionDep) -> list[MaintenanceExclusionRead]:
    rows = list_exclusions(session)
    names: dict[uuid.UUID, str] = (
        dict(session.execute(select(Source.id, Source.name)).tuples().all()) if rows else {}
    )
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
    source = session.get(Source, payload.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Lakehouse not found")
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
    delete_exclusion(session, row, actor=principal.subject)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
