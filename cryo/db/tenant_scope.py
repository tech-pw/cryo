"""Mandatory tenant scoping for multi-tenant deployments."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import Destination, Preset, Source, Table

TENANT_SCOPED = (Source, Destination, Table, Preset)


def bind_tenant(session: Session, tenant_id: uuid.UUID | None) -> None:
    session.info["tenant_id"] = str(tenant_id) if tenant_id else None


def current_tenant_id(session: Session) -> uuid.UUID | None:
    raw = session.info.get("tenant_id")
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
