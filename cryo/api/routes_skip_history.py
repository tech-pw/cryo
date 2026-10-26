"""Paginated skip-evaluation reads. Mount with ``app.include_router(router)``."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from cryo.api.deps import SessionDep, require_viewer
from cryo.db.models import Table
from cryo.db.tenant_scope import (
    current_tenant_id,
    get_scoped_resource,
    get_scoped_run,
    require_tenant,
)
from cryo.maintenance.skip_history import SkipEvaluation, list_skip_evaluations

router = APIRouter(prefix="/api/v1", tags=["decisions"])


class SkipEvaluationRead(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    table_id: uuid.UUID
    stage: str
    reason: str | None
    evaluated_at: datetime


class SkipEvaluationPage(BaseModel):
    items: list[SkipEvaluationRead]
    total: int
    limit: int
    offset: int


def _tenant_filter(session: SessionDep) -> uuid.UUID | None:
    require_tenant(session)
    tenant_id = current_tenant_id(session)
    return tenant_id


def _page(
    rows: list[SkipEvaluation],
    total: int,
    *,
    limit: int,
    offset: int,
) -> SkipEvaluationPage:
    return SkipEvaluationPage(
        items=[SkipEvaluationRead.model_validate(row, from_attributes=True) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/runs/{run_id}/skip-evaluations",
    response_model=SkipEvaluationPage,
    dependencies=[Depends(require_viewer)],
)
def list_run_skip_evaluations(
    run_id: uuid.UUID,
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> SkipEvaluationPage:
    get_scoped_run(session, run_id)
    rows, total = list_skip_evaluations(
        session,
        run_id=run_id,
        tenant_id=_tenant_filter(session),
        limit=limit,
        offset=offset,
    )
    return _page(rows, total, limit=limit, offset=offset)


@router.get(
    "/tables/{table_id}/skip-evaluations",
    response_model=SkipEvaluationPage,
    dependencies=[Depends(require_viewer)],
)
def list_table_skip_evaluations(
    table_id: uuid.UUID,
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> SkipEvaluationPage:
    get_scoped_resource(session, Table, table_id, resource_type="table")
    rows, total = list_skip_evaluations(
        session,
        table_id=table_id,
        tenant_id=_tenant_filter(session),
        limit=limit,
        offset=offset,
    )
    return _page(rows, total, limit=limit, offset=offset)
