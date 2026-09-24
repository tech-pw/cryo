"""Declarative data-quality constraints over stored scan metrics (cryo.quality.constraints).

Evaluation reads only the latest stored scan; nothing here touches the catalog or data files.
Scans evaluate a table's constraints as they persist new stats; ``/evaluate`` re-runs them
against the stored stats on demand.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from cryo.api.deps import SessionDep, require_operator, require_viewer
from cryo.api.security import Principal
from cryo.db.models import (
    AuditLog,
    QualityConstraint,
    QualityConstraintResult,
    Table,
    TableStats,
)
from cryo.quality.constraint_store import evaluate_table_constraints, latest_results
from cryo.quality.constraints import (
    COLUMN_KINDS,
    ConstraintKind,
    ConstraintResult,
    ConstraintSpec,
    ConstraintStatus,
    column_kinds,
    validate_spec,
)

router = APIRouter(prefix="/api/v1", tags=["quality-constraints"])

OperatorDep = Annotated[Principal, Depends(require_operator)]

ConstraintKindLiteral = Literal["size", "completeness", "range", "size_change", "freshness"]
SeverityLiteral = Literal["warn", "error"]
StatusLiteral = Literal["passed", "failed", "unknown"]


class ConstraintResultRead(BaseModel):
    status: StatusLiteral
    evaluated_at: datetime | None = None
    snapshot_id: int | None = None
    scanned_at: datetime | None = None
    observed: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class QualityConstraintRead(BaseModel):
    id: uuid.UUID
    table_id: uuid.UUID
    name: str
    kind: ConstraintKindLiteral
    field_id: int | None
    column_name: str | None
    params: dict[str, Any]
    severity: SeverityLiteral
    enabled: bool
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    latest: ConstraintResultRead | None = None


class ConstraintColumn(BaseModel):
    field_id: int
    name: str
    type: str | None
    kinds: list[ConstraintKindLiteral]


class QualityConstraintsResponse(BaseModel):
    table_id: uuid.UUID
    constraints: list[QualityConstraintRead]
    columns: list[ConstraintColumn]


class QualityConstraintCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: ConstraintKindLiteral
    field_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    severity: SeverityLiteral = "error"
    enabled: bool = True


class QualityConstraintUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    params: dict[str, Any] | None = None
    severity: SeverityLiteral | None = None
    enabled: bool | None = None


class ConstraintResultsResponse(BaseModel):
    constraint_id: uuid.UUID
    results: list[ConstraintResultRead]


@router.get(
    "/tables/{table_id}/quality-constraints",
    response_model=QualityConstraintsResponse,
    dependencies=[Depends(require_viewer)],
)
def list_quality_constraints(
    table_id: uuid.UUID, session: SessionDep
) -> QualityConstraintsResponse:
    _require_table(session, table_id)
    constraints = session.scalars(
        select(QualityConstraint)
        .where(QualityConstraint.table_id == table_id)
        .order_by(QualityConstraint.name)
    ).all()
    latest = latest_results(session, table_id)
    return QualityConstraintsResponse(
        table_id=table_id,
        constraints=[_read(c, _stored(latest.get(c.id))) for c in constraints],
        columns=_columns(session, table_id),
    )


@router.post(
    "/tables/{table_id}/quality-constraints",
    response_model=QualityConstraintRead,
    status_code=201,
)
def create_quality_constraint(
    table_id: uuid.UUID,
    payload: QualityConstraintCreate,
    session: SessionDep,
    principal: OperatorDep,
) -> QualityConstraintRead:
    _require_table(session, table_id)
    kind = ConstraintKind(payload.kind)
    column = _column_entry(session, table_id, payload.field_id) if kind in COLUMN_KINDS else None
    if kind in COLUMN_KINDS and column is None:
        raise HTTPException(422, "field_id is not a column in the table's latest scan")
    spec = ConstraintSpec(kind=kind, params=payload.params, field_id=payload.field_id)
    _validate(spec, column)
    constraint = QualityConstraint(
        id=uuid.uuid4(),
        table_id=table_id,
        name=payload.name,
        kind=kind.value,
        field_id=payload.field_id if column else None,
        column_name=str(column["column_name"]) if column else None,
        params=payload.params,
        severity=payload.severity,
        enabled=payload.enabled,
        created_by=principal.subject,
    )
    session.add(constraint)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, f"a constraint named {payload.name!r} already exists") from exc
    _audit(session, constraint, "create_quality_constraint", principal)
    result = _evaluate_one(session, constraint)
    session.commit()
    session.refresh(constraint)
    return _read(constraint, result)


@router.patch("/quality-constraints/{constraint_id}", response_model=QualityConstraintRead)
def update_quality_constraint(
    constraint_id: uuid.UUID,
    payload: QualityConstraintUpdate,
    session: SessionDep,
    principal: OperatorDep,
) -> QualityConstraintRead:
    constraint = _require_constraint(session, constraint_id)
    if payload.params is not None:
        column = (
            _column_entry(session, constraint.table_id, constraint.field_id)
            if constraint.field_id is not None
            else None
        )
        _validate(
            ConstraintSpec(
                kind=ConstraintKind(constraint.kind),
                params=payload.params,
                field_id=constraint.field_id,
            ),
            column,
        )
        constraint.params = payload.params
    if payload.name is not None:
        constraint.name = payload.name
    if payload.severity is not None:
        constraint.severity = payload.severity
    if payload.enabled is not None:
        constraint.enabled = payload.enabled
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, f"a constraint named {payload.name!r} already exists") from exc
    _audit(session, constraint, "update_quality_constraint", principal)
    result = _evaluate_one(session, constraint) if constraint.enabled else None
    session.commit()
    session.refresh(constraint)
    if result is None:
        result = _stored(latest_results(session, constraint.table_id).get(constraint.id))
    return _read(constraint, result)


@router.delete("/quality-constraints/{constraint_id}", status_code=204)
def delete_quality_constraint(
    constraint_id: uuid.UUID, session: SessionDep, principal: OperatorDep
) -> None:
    constraint = _require_constraint(session, constraint_id)
    _audit(session, constraint, "delete_quality_constraint", principal)
    session.execute(
        delete(QualityConstraintResult).where(
            QualityConstraintResult.constraint_id == constraint_id
        )
    )
    session.delete(constraint)
    session.commit()


@router.get(
    "/quality-constraints/{constraint_id}/results",
    response_model=ConstraintResultsResponse,
    dependencies=[Depends(require_viewer)],
)
def list_constraint_results(
    constraint_id: uuid.UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ConstraintResultsResponse:
    _require_constraint(session, constraint_id)
    rows = session.scalars(
        select(QualityConstraintResult)
        .where(QualityConstraintResult.constraint_id == constraint_id)
        .order_by(QualityConstraintResult.evaluated_at.desc())
        .limit(limit)
    ).all()
    return ConstraintResultsResponse(
        constraint_id=constraint_id,
        results=[r for row in rows if (r := _stored(row)) is not None],
    )


@router.post(
    "/tables/{table_id}/quality-constraints/evaluate",
    response_model=QualityConstraintsResponse,
)
def evaluate_quality_constraints(
    table_id: uuid.UUID, session: SessionDep, principal: OperatorDep
) -> QualityConstraintsResponse:
    del principal
    _require_table(session, table_id)
    evaluate_table_constraints(session, table_id)
    session.commit()
    return list_quality_constraints(table_id, session)


def _evaluate_one(session: SessionDep, constraint: QualityConstraint) -> ConstraintResultRead:
    for evaluation in evaluate_table_constraints(session, constraint.table_id):
        if evaluation.constraint.id == constraint.id:
            return _fresh(evaluation.result)
    return _fresh(ConstraintResult(ConstraintStatus.UNKNOWN))


def _validate(spec: ConstraintSpec, column: dict[str, Any] | None) -> None:
    try:
        validate_spec(spec, column)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _require_table(session: SessionDep, table_id: uuid.UUID) -> None:
    if session.get(Table, table_id) is None:
        raise HTTPException(404, "table not found")


def _require_constraint(session: SessionDep, constraint_id: uuid.UUID) -> QualityConstraint:
    constraint = session.get(QualityConstraint, constraint_id)
    if constraint is None:
        raise HTTPException(404, "constraint not found")
    return constraint


def _column_entries(session: SessionDep, table_id: uuid.UUID) -> list[dict[str, Any]]:
    stats = session.get(TableStats, table_id)
    raw = stats.raw if stats is not None and isinstance(stats.raw, dict) else {}
    return [
        entry
        for entry in raw.get("column_quality") or []
        if isinstance(entry, dict) and isinstance(entry.get("field_id"), int)
    ]


def _column_entry(
    session: SessionDep, table_id: uuid.UUID, field_id: int | None
) -> dict[str, Any] | None:
    if field_id is None:
        return None
    return next((e for e in _column_entries(session, table_id) if e["field_id"] == field_id), None)


def _columns(session: SessionDep, table_id: uuid.UUID) -> list[ConstraintColumn]:
    columns = []
    for entry in _column_entries(session, table_id):
        kinds = column_kinds(entry)
        if kinds:
            columns.append(
                ConstraintColumn(
                    field_id=entry["field_id"],
                    name=str(entry.get("column_name") or entry["field_id"]),
                    type=entry.get("column_type"),
                    kinds=[kind.value for kind in kinds],
                )
            )
    return columns


def _audit(
    session: SessionDep, constraint: QualityConstraint, action: str, principal: Principal
) -> None:
    session.add(
        AuditLog(
            table_id=constraint.table_id,
            action=action,
            status="OK",
            engine="cryo",
            detail=(
                f"name={constraint.name} kind={constraint.kind} "
                f"column={constraint.column_name} params={constraint.params} "
                f"severity={constraint.severity} enabled={constraint.enabled}"
            )[:4000],
            actor=principal.subject,
        )
    )


def _fresh(result: ConstraintResult) -> ConstraintResultRead:
    return ConstraintResultRead(
        status=result.status.value,
        observed=result.observed,
        reason=result.reason,
        evidence=result.evidence,
    )


def _stored(row: QualityConstraintResult | None) -> ConstraintResultRead | None:
    if row is None:
        return None
    return ConstraintResultRead(
        status=row.status,  # type: ignore[arg-type]
        evaluated_at=row.evaluated_at,
        snapshot_id=row.snapshot_id,
        scanned_at=row.scanned_at,
        observed=row.observed or {},
        reason=row.reason,
        evidence=row.evidence or {},
    )


def _read(
    constraint: QualityConstraint, latest: ConstraintResultRead | None
) -> QualityConstraintRead:
    return QualityConstraintRead(
        id=constraint.id,
        table_id=constraint.table_id,
        name=constraint.name,
        kind=constraint.kind,  # type: ignore[arg-type]
        field_id=constraint.field_id,
        column_name=constraint.column_name,
        params=dict(constraint.params or {}),
        severity=constraint.severity,  # type: ignore[arg-type]
        enabled=constraint.enabled,
        created_by=constraint.created_by,
        created_at=constraint.created_at,
        updated_at=constraint.updated_at,
        latest=latest,
    )
