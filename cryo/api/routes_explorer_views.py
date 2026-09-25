"""Named catalog filters for the signed-in principal.

These are view bookmarks, not schedules. A saved view does not enqueue maintenance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cryo.api.deps import SessionDep, require_viewer
from cryo.api.security import Principal
from cryo.db.models import SavedExplorerView

router = APIRouter(prefix="/api/v1", tags=["explorer-views"])

ViewerDep = Annotated[Principal, Depends(require_viewer)]
_GROUP_BY = {
    "catalog",
    "schema_name",
    "grade",
    "compaction_priority",
    "needs_compaction",
    "size_bucket",
}
_MAX_FILTER_BYTES = 16_384


class ExplorerViewWrite(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: str | None = Field(default=None, max_length=80)
    group_by: str | None = None
    savings_view: bool = False


class ExplorerViewRead(BaseModel):
    id: uuid.UUID
    name: str
    filters: dict[str, Any]
    sort: str | None = None
    group_by: str | None = None
    savings_view: bool
    updated_at: datetime


def _validate(payload: ExplorerViewWrite) -> ExplorerViewWrite:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if payload.group_by is not None and payload.group_by not in _GROUP_BY:
        raise HTTPException(status_code=422, detail="unsupported group_by")
    encoded = str(payload.filters)
    if len(encoded) > _MAX_FILTER_BYTES:
        raise HTTPException(status_code=422, detail="filters are too large")
    return payload.model_copy(update={"name": name})


def _read(row: SavedExplorerView) -> ExplorerViewRead:
    return ExplorerViewRead(
        id=row.id,
        name=row.name,
        filters=dict(row.filters or {}),
        sort=row.sort,
        group_by=row.group_by,
        savings_view=row.savings_view,
        updated_at=row.updated_at,
    )


@router.get("/explorer-views", response_model=list[ExplorerViewRead])
def list_explorer_views(session: SessionDep, principal: ViewerDep) -> list[ExplorerViewRead]:
    rows = session.scalars(
        select(SavedExplorerView)
        .where(SavedExplorerView.owner_subject == principal.subject)
        .order_by(SavedExplorerView.name)
    ).all()
    return [_read(row) for row in rows]


@router.post("/explorer-views", response_model=ExplorerViewRead)
def save_explorer_view(
    payload: ExplorerViewWrite,
    session: SessionDep,
    principal: ViewerDep,
) -> ExplorerViewRead:
    payload = _validate(payload)
    row = session.scalar(
        select(SavedExplorerView).where(
            SavedExplorerView.owner_subject == principal.subject,
            SavedExplorerView.name == payload.name,
        )
    )
    if row is None:
        row = SavedExplorerView(owner_subject=principal.subject, name=payload.name, filters={})
        session.add(row)
    row.filters = payload.filters
    row.sort = payload.sort
    row.group_by = payload.group_by
    row.savings_view = payload.savings_view
    row.updated_at = datetime.now(UTC)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="view name already exists") from exc
    session.refresh(row)
    return _read(row)


@router.delete("/explorer-views/{view_id}", status_code=204)
def delete_explorer_view(
    view_id: uuid.UUID,
    session: SessionDep,
    principal: ViewerDep,
) -> None:
    row = session.get(SavedExplorerView, view_id)
    if row is None or row.owner_subject != principal.subject:
        raise HTTPException(status_code=404, detail="view not found")
    session.delete(row)
    session.commit()
