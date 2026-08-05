from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from cryo.api.deps import CurrentPrincipal, SessionDep, require_operator, require_viewer
from cryo.db.models import (
    Destination,
    EffectReceipt,
    MaintenanceAttempt,
    MaintenanceControlState,
    MaintenanceIntent,
)
from cryo.db.repositories.intents import IntentStore
from cryo.domain.intents import AttemptStatus, EffectState

router = APIRouter(
    prefix="/api/v1/maintenance/safety",
    tags=["maintenance-safety"],
    dependencies=[Depends(require_viewer)],
)


class MaintenanceSafetySummary(BaseModel):
    intents_by_status: dict[str, int]
    effects_by_state: dict[str, int]
    ambiguous_effects: int
    active_attempts: int
    active_pauses: int


class MaintenanceIntentRead(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    table_id: uuid.UUID
    table_ref: str
    destination_id: uuid.UUID
    version: int
    status: str
    actor_type: str
    actor_id: str
    operation_scope: list[str]
    plan_hash: str
    fencing_epoch: int
    containment_action: str
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class EffectReceiptRead(BaseModel):
    id: uuid.UUID
    intent_id: uuid.UUID
    job_id: uuid.UUID
    table_id: uuid.UUID
    table_ref: str
    destination_id: uuid.UUID
    step_index: int
    operation_type: str
    strategy: str
    state: str
    external_reference: str | None
    probe_count: int
    next_probe_at: datetime | None
    submitted_at: datetime | None
    resolved_at: datetime | None
    error: dict[str, object] | None
    created_at: datetime
    updated_at: datetime


class EffectResolutionWrite(BaseModel):
    outcome: Literal["succeeded", "failed"]
    reason: str = Field(min_length=3, max_length=1000)
    evidence_reference: str | None = Field(default=None, min_length=1, max_length=1000)
    continue_job: bool = True


class EffectResolutionRead(BaseModel):
    receipt: EffectReceiptRead
    continuation: Literal["queued_for_admission", "manual_review", "superseded"]
    message: str


class EffectBatchResolutionWrite(BaseModel):
    receipt_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    outcome: Literal["succeeded", "failed"]
    reason: str = Field(min_length=3, max_length=1000)
    continue_job: bool = True

    @field_validator("receipt_ids")
    @classmethod
    def unique_receipts(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("receipt_ids must be unique")
        return value


class EffectBatchResolutionItem(BaseModel):
    receipt_id: uuid.UUID
    ok: bool
    resolution: EffectResolutionRead | None = None
    error: str | None = None


class EffectBatchResolutionRead(BaseModel):
    requested: int
    resolved: int
    failed: int
    items: list[EffectBatchResolutionItem]


@router.get("/summary", response_model=MaintenanceSafetySummary)
def maintenance_safety_summary(
    session: SessionDep,
    principal: CurrentPrincipal,
) -> MaintenanceSafetySummary:
    intent_scope = _intent_scope(principal)
    intent_counts = {
        status: count
        for status, count in session.execute(
            select(MaintenanceIntent.status, func.count())
            .where(*intent_scope)
            .group_by(MaintenanceIntent.status)
        )
    }
    effect_counts = {
        state: count
        for state, count in session.execute(
            select(EffectReceipt.state, func.count())
            .join(
                MaintenanceIntent,
                MaintenanceIntent.id == EffectReceipt.intent_id,
            )
            .where(*intent_scope)
            .group_by(EffectReceipt.state)
        )
    }
    ambiguous_effects = session.scalar(
        select(func.count())
        .select_from(EffectReceipt)
        .join(
            MaintenanceIntent,
            MaintenanceIntent.id == EffectReceipt.intent_id,
        )
        .where(
            *intent_scope,
            EffectReceipt.state.in_([EffectState.SUBMITTED.value, EffectState.UNKNOWN.value]),
        )
    )
    active_attempts = session.scalar(
        select(func.count())
        .select_from(MaintenanceAttempt)
        .join(
            MaintenanceIntent,
            MaintenanceIntent.id == MaintenanceAttempt.intent_id,
        )
        .where(
            *intent_scope,
            MaintenanceAttempt.status == AttemptStatus.STARTED.value,
        )
    )
    active_pauses = session.scalar(
        select(func.count())
        .select_from(MaintenanceControlState)
        .where(
            MaintenanceControlState.paused.is_(True),
            _control_visibility(session, principal),
        )
    )
    return MaintenanceSafetySummary(
        intents_by_status=intent_counts,
        effects_by_state=effect_counts,
        ambiguous_effects=int(ambiguous_effects or 0),
        active_attempts=int(active_attempts or 0),
        active_pauses=int(active_pauses or 0),
    )


@router.get("/intents", response_model=list[MaintenanceIntentRead])
def list_maintenance_intents(
    session: SessionDep,
    principal: CurrentPrincipal,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[MaintenanceIntentRead]:
    statement = select(MaintenanceIntent).where(*_intent_scope(principal))
    if status is not None:
        statement = statement.where(MaintenanceIntent.status == status)
    rows = session.scalars(statement.order_by(MaintenanceIntent.created_at.desc()).limit(limit))
    return [
        MaintenanceIntentRead(
            id=row.id,
            job_id=row.job_id,
            table_id=row.table_id,
            table_ref=row.table_ref,
            destination_id=row.destination_id,
            version=row.version,
            status=row.status,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            operation_scope=row.operation_scope,
            plan_hash=row.plan_hash,
            fencing_epoch=row.fencing_epoch,
            containment_action=row.containment_action,
            expires_at=row.expires_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.get("/effects", response_model=list[EffectReceiptRead])
def list_effect_receipts(
    session: SessionDep,
    principal: CurrentPrincipal,
    state: str | None = None,
    ambiguous_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[EffectReceiptRead]:
    statement = (
        select(EffectReceipt, MaintenanceIntent)
        .join(
            MaintenanceIntent,
            MaintenanceIntent.id == EffectReceipt.intent_id,
        )
        .where(*_intent_scope(principal))
    )
    if ambiguous_only:
        statement = statement.where(
            EffectReceipt.state.in_([EffectState.SUBMITTED.value, EffectState.UNKNOWN.value])
        )
    elif state is not None:
        statement = statement.where(EffectReceipt.state == state)
    rows = session.execute(statement.order_by(EffectReceipt.created_at.desc()).limit(limit))
    return [_effect_read(effect, intent) for effect, intent in rows]


@router.post(
    "/effects/{receipt_id}/resolve",
    response_model=EffectResolutionRead,
    dependencies=[Depends(require_operator)],
)
def resolve_effect(
    receipt_id: uuid.UUID,
    payload: EffectResolutionWrite,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> EffectResolutionRead:
    return _resolve_effect_for_principal(
        receipt_id=receipt_id,
        payload=payload,
        session=session,
        principal=principal,
    )


@router.post(
    "/effects/resolve-batch",
    response_model=EffectBatchResolutionRead,
    dependencies=[Depends(require_operator)],
)
def resolve_effect_batch(
    payload: EffectBatchResolutionWrite,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> EffectBatchResolutionRead:
    items: list[EffectBatchResolutionItem] = []
    for receipt_id in payload.receipt_ids:
        try:
            resolution = _resolve_effect_for_principal(
                receipt_id=receipt_id,
                payload=EffectResolutionWrite(
                    outcome=payload.outcome,
                    reason=payload.reason,
                    continue_job=payload.continue_job,
                ),
                session=session,
                principal=principal,
            )
            items.append(
                EffectBatchResolutionItem(
                    receipt_id=receipt_id,
                    ok=True,
                    resolution=resolution,
                )
            )
        except HTTPException as exc:
            items.append(
                EffectBatchResolutionItem(
                    receipt_id=receipt_id,
                    ok=False,
                    error=str(exc.detail),
                )
            )
    resolved = sum(item.ok for item in items)
    return EffectBatchResolutionRead(
        requested=len(items),
        resolved=resolved,
        failed=len(items) - resolved,
        items=items,
    )


def _resolve_effect_for_principal(
    *,
    receipt_id: uuid.UUID,
    payload: EffectResolutionWrite,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> EffectResolutionRead:
    row = session.execute(
        select(EffectReceipt, MaintenanceIntent)
        .join(MaintenanceIntent, MaintenanceIntent.id == EffectReceipt.intent_id)
        .where(
            EffectReceipt.id == receipt_id,
            *_intent_scope(principal),
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Effect receipt not found")
    receipt, intent = row
    if receipt.state not in {EffectState.SUBMITTED.value, EffectState.UNKNOWN.value}:
        raise HTTPException(
            status_code=409,
            detail=f"Effect is already resolved as {receipt.state}",
        )
    if (
        payload.evidence_reference is not None
        and receipt.external_reference is not None
        and payload.evidence_reference != receipt.external_reference
    ):
        raise HTTPException(
            status_code=409,
            detail="Evidence reference does not match the immutable submission reference",
        )

    manual_only = (
        receipt.strategy == "manual_reconciliation"
        or (receipt.submission or {}).get("continuation") == "manual_review"
    )
    make_ready = payload.continue_job and not manual_only
    store = IntentStore(
        sessionmaker(
            bind=session.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        )
    )
    if payload.evidence_reference is not None and receipt.external_reference is None:
        store.set_effect_external_reference(str(receipt.id), payload.evidence_reference)
    outcome = EffectState.SUCCEEDED if payload.outcome == "succeeded" else EffectState.FAILED
    current = store.complete_reconciliation(
        str(receipt.id),
        state=outcome,
        detail=payload.reason,
        make_ready=make_ready,
        actor=principal.subject,
        resolution_source="operator_attestation",
    )

    session.expire_all()
    refreshed = session.get(EffectReceipt, receipt.id)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Effect receipt not found")
    if not current:
        continuation: Literal["queued_for_admission", "manual_review", "superseded"] = "superseded"
        message = "Outcome recorded; the intent has been superseded and will not continue."
    elif manual_only or not payload.continue_job:
        continuation = "manual_review"
        message = "Outcome recorded; the job remains held for manual continuation."
    else:
        continuation = "queued_for_admission"
        message = "Outcome recorded; remaining work is queued for Governor admission."
    return EffectResolutionRead(
        receipt=_effect_read(refreshed, intent),
        continuation=continuation,
        message=message,
    )


def _effect_read(
    effect: EffectReceipt,
    intent: MaintenanceIntent,
) -> EffectReceiptRead:
    return EffectReceiptRead(
        id=effect.id,
        intent_id=effect.intent_id,
        job_id=intent.job_id,
        table_id=intent.table_id,
        table_ref=intent.table_ref,
        destination_id=effect.destination_id,
        step_index=effect.step_index,
        operation_type=effect.operation_type,
        strategy=effect.strategy,
        state=effect.state,
        external_reference=effect.external_reference,
        probe_count=effect.probe_count,
        next_probe_at=effect.next_probe_at,
        submitted_at=effect.submitted_at,
        resolved_at=effect.resolved_at,
        error=effect.error,
        created_at=effect.created_at,
        updated_at=effect.updated_at,
    )


def _intent_scope(
    principal: CurrentPrincipal,
) -> tuple[ColumnElement[bool], ...]:
    if principal.tenant_id is None:
        return ()
    return (MaintenanceIntent.tenant_id == principal.tenant_id,)


def _control_visibility(
    session: SessionDep,
    principal: CurrentPrincipal,
) -> ColumnElement[bool]:
    if principal.tenant_id is None:
        return MaintenanceControlState.id.is_not(None)
    destination_ids = [
        str(destination_id)
        for destination_id in session.scalars(
            select(Destination.id).where(Destination.tenant_id == principal.tenant_id)
        )
    ]
    conditions = [
        (MaintenanceControlState.scope_type == "global")
        & (MaintenanceControlState.scope_id == "global"),
        (MaintenanceControlState.scope_type == "tenant")
        & (MaintenanceControlState.scope_id == str(principal.tenant_id)),
    ]
    if destination_ids:
        conditions.append(
            (MaintenanceControlState.scope_type == "destination")
            & MaintenanceControlState.scope_id.in_(destination_ids)
        )
    return or_(*conditions)
