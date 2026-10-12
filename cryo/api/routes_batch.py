"""Maintenance-batch selection receipts.

Resolve freezes membership and does not scan, enqueue, or call an engine.
``router`` is not mounted: ``cryo.api.app`` is mid-edit, so registration is pending.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from cryo.api.deps import SessionDep, require_operator, require_viewer
from cryo.api.schemas import AssignmentSelection
from cryo.api.security import Principal
from cryo.db.tenant_scope import require_tenant
from cryo.maintenance.batch_selection import (
    BatchSelectionCapExceeded,
    BatchSelectionError,
    BatchSelectionExpired,
    BatchSelectionInvalid,
    BatchSelectionNotFound,
    BatchSelectionReceipt,
    default_store,
    read_batch_selection,
    resolve_batch_selection,
)

router = APIRouter(prefix="/api/v1", tags=["maintenance"])

OperatorDep = Annotated[Principal, Depends(require_operator)]
ViewerDep = Annotated[Principal, Depends(require_viewer)]


class BatchTableRefRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: uuid.UUID
    catalog: str
    schema_: str = Field(alias="schema")
    name: str


class BatchSelectionReceiptRead(BaseModel):
    receipt_id: uuid.UUID
    expires_at: datetime
    unique_count: int
    tables: list[BatchTableRefRead]
    overflow: bool


class ResolveBatchSelectionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    table_ids: list[uuid.UUID] = Field(default_factory=list)
    filter: dict[str, Any] | str | None = None
    predicate: dict[str, Any] | str | None = None
    selection: AssignmentSelection | None = None
    receipt_ids: list[uuid.UUID] = Field(default_factory=list)
    exclude_ids: list[uuid.UUID] = Field(default_factory=list)
    exclusion_ids: list[uuid.UUID] = Field(default_factory=list)
    freshness_breach: bool = False
    maintenance_overdue: bool = False
    p95_breach: bool = False
    no_reads_days: int | None = Field(default=None, ge=1, le=3660)

    def exclusions(self) -> list[uuid.UUID]:
        return list(dict.fromkeys([*self.exclude_ids, *self.exclusion_ids]))

    def resolved_filter(self) -> dict[str, Any] | str | None:
        if self.filter is not None and self.predicate is not None and self.filter != self.predicate:
            raise BatchSelectionInvalid("filter and predicate disagree")
        if self.filter is not None:
            return self.filter
        return self.predicate

    def matches_list(self) -> bool:
        return (
            "filter" in self.model_fields_set
            or "predicate" in self.model_fields_set
            or self.freshness_breach
            or self.maintenance_overdue
            or self.p95_breach
            or self.no_reads_days is not None
        )


def _inputs(
    payload: ResolveBatchSelectionRequest,
) -> tuple[list[uuid.UUID], dict[str, Any] | str | None, bool, list[uuid.UUID]]:
    filter_spec = payload.resolved_filter()
    table_ids = list(payload.table_ids)
    match_filter = payload.matches_list()
    selection = payload.selection
    if selection is not None and selection.mode == "ids":
        table_ids.extend(selection.ids)
    elif selection is not None:
        match_filter = True
        if selection.filter is not None:
            if filter_spec is not None and filter_spec != selection.filter:
                raise BatchSelectionInvalid("filter and selection.filter disagree")
            filter_spec = selection.filter
    return table_ids, filter_spec, match_filter, payload.exclusions()


def _to_read(receipt: BatchSelectionReceipt) -> BatchSelectionReceiptRead:
    return BatchSelectionReceiptRead(
        receipt_id=receipt.receipt_id,
        expires_at=receipt.expires_at,
        unique_count=receipt.unique_count,
        tables=[
            BatchTableRefRead(
                id=row.id,
                catalog=row.catalog,
                schema=row.schema,
                name=row.name,
            )
            for row in receipt.tables
        ],
        overflow=receipt.overflow,
    )


def _http_error(exc: BatchSelectionError) -> HTTPException:
    if isinstance(exc, BatchSelectionCapExceeded):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "overflow": True, "limit": exc.limit},
        )
    if isinstance(exc, BatchSelectionExpired):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, BatchSelectionNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/maintenance/batch/resolve", response_model=BatchSelectionReceiptRead)
def resolve_maintenance_batch(
    payload: ResolveBatchSelectionRequest,
    session: SessionDep,
    principal: OperatorDep,
) -> BatchSelectionReceiptRead:
    try:
        table_ids, filter_spec, match_filter, exclude_ids = _inputs(payload)
        receipt = resolve_batch_selection(
            session,
            principal=principal.subject,
            tenant_id=principal.tenant_id,
            query_tenant_id=require_tenant(session),
            table_ids=table_ids,
            filter_spec=filter_spec,
            match_filter=match_filter,
            freshness_breach=payload.freshness_breach,
            maintenance_overdue=payload.maintenance_overdue,
            p95_breach=payload.p95_breach,
            no_reads_days=payload.no_reads_days,
            receipt_ids=payload.receipt_ids,
            exclude_ids=exclude_ids,
            store=default_store,
        )
    except BatchSelectionError as exc:
        raise _http_error(exc) from exc
    return _to_read(receipt)


@router.get(
    "/maintenance/batch/receipts/{receipt_id}",
    response_model=BatchSelectionReceiptRead,
)
def read_maintenance_batch_receipt(
    receipt_id: uuid.UUID,
    session: SessionDep,
    principal: ViewerDep,
) -> BatchSelectionReceiptRead:
    try:
        receipt = read_batch_selection(
            session,
            receipt_id,
            principal=principal.subject,
            tenant_id=principal.tenant_id,
            query_tenant_id=require_tenant(session),
            store=default_store,
        )
    except BatchSelectionError as exc:
        raise _http_error(exc) from exc
    return _to_read(receipt)
