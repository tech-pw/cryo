"""Mandatory tenant scoping for multi-tenant deployments."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, Select, or_, select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import (
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    Source,
    Table,
)

TENANT_SCOPED = (Source, Destination, Table, Preset)


def bind_tenant(session: Session, tenant_id: uuid.UUID | None) -> None:
    session.info["tenant_id"] = str(tenant_id) if tenant_id else None


def current_tenant_id(session: Session) -> uuid.UUID | None:
    info = getattr(session, "info", None)
    raw = info.get("tenant_id") if isinstance(info, dict) else None
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def require_tenant(session: Session) -> uuid.UUID:
    tenant_id = current_tenant_id(session)
    if tenant_id is not None:
        return tenant_id
    settings = get_settings()
    if settings.enforce_tenant_isolation:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_id claim required for this deployment",
        )
    return uuid.UUID(int=0)  # sentinel: no filter when isolation disabled


def scope_by_tenant[RowT: tuple[Any, ...]](
    stmt: Select[RowT],
    model: type[Any],
    tenant_id: uuid.UUID,
) -> Select[RowT]:
    if tenant_id.int != 0 and hasattr(model, "tenant_id"):
        return stmt.where(model.tenant_id == tenant_id)
    return stmt


def assert_resource_tenant(
    session: Session,
    resource_tenant_id: uuid.UUID | None,
    *,
    resource_type: str,
) -> None:
    tenant_id = current_tenant_id(session)
    if tenant_id is None:
        return
    settings = get_settings()
    if not settings.enforce_tenant_isolation:
        return
    if resource_tenant_id is None or resource_tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{resource_type} not found",
        )


def scoped_table_query(session: Session) -> Select[tuple[Table]]:
    tenant_id = require_tenant(session)
    stmt: Select[tuple[Table]] = select(Table)
    return scope_by_tenant(stmt, Table, tenant_id)


def scoped_select[T](
    session: Session,
    model: type[T],
    *order_by: Any,
) -> Select[tuple[T]]:
    tenant_id = require_tenant(session)
    stmt: Select[tuple[T]] = select(model)
    if order_by:
        stmt = stmt.order_by(*order_by)
    return scope_by_tenant(stmt, model, tenant_id)


def get_scoped_resource[T](
    session: Session,
    model: type[T],
    resource_id: uuid.UUID,
    *,
    resource_type: str,
) -> T:
    resource = session.get(model, resource_id)
    if resource is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{resource_type} not found",
        )
    tenant_id = getattr(resource, "tenant_id", None)
    assert_resource_tenant(session, tenant_id, resource_type=resource_type)
    return resource


def tenant_id_for_create(session: Session) -> uuid.UUID | None:
    tenant_id = require_tenant(session)
    if tenant_id.int == 0:
        return None
    return tenant_id


def tenant_filter_id(session: Session) -> uuid.UUID | None:
    """The tenant to filter list/lookup queries by, or ``None`` when unscoped.

    Same rule as :func:`scope_by_tenant`: a bound tenant always filters; with no tenant
    bound, isolation-enforcing deployments refuse (403) and others are unscoped.
    """
    tenant_id = require_tenant(session)
    return None if tenant_id.int == 0 else tenant_id


def run_tenant_clause(session: Session, tenant_id: uuid.UUID) -> ColumnElement[bool]:
    """WHERE clause matching runs that belong to ``tenant_id``.

    Runs carry no tenant column, so ownership is derived the same way the assistant's
    ``tool_scope._run_clause`` does it: a run belongs to a tenant when one of its jobs
    targets that tenant's table, or its preset is that tenant's. Scan runs have neither,
    so they also match when their summary's ``source_id`` is one of the tenant's sources.
    """
    via_job = select(MaintenanceJob.run_id).where(
        MaintenanceJob.table_id.in_(select(Table.id).where(Table.tenant_id == tenant_id))
    )
    via_preset = select(Preset.id).where(Preset.tenant_id == tenant_id)
    source_ids = [
        str(source_id)
        for source_id in session.scalars(select(Source.id).where(Source.tenant_id == tenant_id))
    ]
    clauses: list[ColumnElement[bool]] = [
        MaintenanceRun.id.in_(via_job),
        MaintenanceRun.preset_id.in_(via_preset),
    ]
    if source_ids:
        clauses.append(MaintenanceRun.summary["source_id"].astext.in_(source_ids))
    return or_(*clauses)


def scope_runs[RowT: tuple[Any, ...]](session: Session, stmt: Select[RowT]) -> Select[RowT]:
    """Restrict a ``MaintenanceRun`` query to the session's tenant (no-op when unscoped)."""
    tenant_id = tenant_filter_id(session)
    if tenant_id is None:
        return stmt
    return stmt.where(run_tenant_clause(session, tenant_id))


def run_visible(session: Session, run_id: uuid.UUID) -> bool:
    """True when the run exists and belongs to the session's tenant (or scoping is off).

    Never raises: a session that may not see any run (no tenant on an isolation-enforcing
    deployment) just sees nothing, which suits callers such as the run websocket that
    cannot answer with an HTTP error.
    """
    try:
        stmt = scope_runs(session, select(MaintenanceRun.id).where(MaintenanceRun.id == run_id))
    except HTTPException:
        return False
    return session.scalar(stmt.limit(1)) is not None


def get_scoped_run(session: Session, run_id: uuid.UUID) -> MaintenanceRun:
    """Load a run by ID, answering 404 when it is missing or another tenant's."""
    run = session.scalar(
        scope_runs(session, select(MaintenanceRun).where(MaintenanceRun.id == run_id)).limit(1)
    )
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


def get_scoped_job(session: Session, job_id: uuid.UUID) -> MaintenanceJob:
    """Load a job by ID, answering 404 when it is missing or targets another tenant's table."""
    stmt = select(MaintenanceJob).where(MaintenanceJob.id == job_id)
    tenant_id = tenant_filter_id(session)
    if tenant_id is not None:
        stmt = stmt.where(
            MaintenanceJob.table_id.in_(select(Table.id).where(Table.tenant_id == tenant_id))
        )
    job = session.scalar(stmt.limit(1))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job
