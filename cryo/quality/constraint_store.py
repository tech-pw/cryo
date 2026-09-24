"""Evaluate and record declarative quality constraints against a table's stored scan stats."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from cryo.db.models import QualityConstraint, QualityConstraintResult, TableStats
from cryo.quality.constraints import (
    ConstraintKind,
    ConstraintResult,
    ConstraintSpec,
    ConstraintStatus,
    MetricsView,
    evaluate,
)

logger = logging.getLogger(__name__)

RESULT_RETENTION = 100


@dataclass(frozen=True)
class Evaluation:
    constraint: QualityConstraint
    result: ConstraintResult


def metrics_view(stats: TableStats) -> MetricsView:
    return MetricsView.from_stats(
        raw=stats.raw if isinstance(stats.raw, dict) else None,
        total_records=stats.total_records,
        scanned_at=stats.scan_at,
        position_delete_files=stats.position_delete_files,
        equality_delete_files=stats.equality_delete_files,
    )


def spec_of(constraint: QualityConstraint) -> ConstraintSpec:
    return ConstraintSpec(
        kind=ConstraintKind(constraint.kind),
        params=dict(constraint.params or {}),
        field_id=constraint.field_id,
        column_name=constraint.column_name,
    )


def evaluate_table_constraints(
    session: Session,
    table_id: uuid.UUID,
    *,
    persist: bool = True,
    now: datetime | None = None,
) -> list[Evaluation]:
    constraints = list(
        session.scalars(
            select(QualityConstraint)
            .where(QualityConstraint.table_id == table_id, QualityConstraint.enabled.is_(True))
            .order_by(QualityConstraint.name)
        )
    )
    if not constraints:
        return []
    stats = session.get(TableStats, table_id)
    if stats is None:
        return [
            Evaluation(c, ConstraintResult(ConstraintStatus.UNKNOWN, reason="table not scanned"))
            for c in constraints
        ]
    view = metrics_view(stats)
    at = now or datetime.now(UTC)
    evaluations = []
    for constraint in constraints:
        baseline = (
            _baseline(session, constraint.id, view.snapshot_id)
            if constraint.kind == ConstraintKind.SIZE_CHANGE
            else None
        )
        result = evaluate(spec_of(constraint), view, baseline=baseline)
        if persist:
            _record(session, constraint, result, view, at)
        evaluations.append(Evaluation(constraint, result))
    return evaluations


def evaluate_after_scan(session: Session, table_id: uuid.UUID) -> None:
    """Scan-time hook: a constraint failure must never fail the scan that fed it."""
    try:
        with session.begin_nested():
            evaluate_table_constraints(session, table_id)
    except Exception:
        logger.warning("Quality constraint evaluation failed for table %s", table_id, exc_info=True)


def latest_results(
    session: Session, table_id: uuid.UUID
) -> dict[uuid.UUID, QualityConstraintResult]:
    newest = (
        select(
            QualityConstraintResult.constraint_id,
            func.max(QualityConstraintResult.evaluated_at).label("at"),
        )
        .where(QualityConstraintResult.table_id == table_id)
        .group_by(QualityConstraintResult.constraint_id)
        .subquery()
    )
    rows = session.scalars(
        select(QualityConstraintResult).join(
            newest,
            (QualityConstraintResult.constraint_id == newest.c.constraint_id)
            & (QualityConstraintResult.evaluated_at == newest.c.at),
        )
    )
    return {row.constraint_id: row for row in rows}


def failing_constraints(
    session: Session, table_id: uuid.UUID, *, severity: str = "error"
) -> list[tuple[QualityConstraint, QualityConstraintResult]]:
    results = latest_results(session, table_id)
    if not results:
        return []
    constraints = session.scalars(
        select(QualityConstraint).where(
            QualityConstraint.id.in_(list(results)),
            QualityConstraint.enabled.is_(True),
            QualityConstraint.severity == severity,
        )
    )
    return [
        (c, results[c.id])
        for c in constraints
        if results[c.id].status == ConstraintStatus.FAILED.value
    ]


def _baseline(
    session: Session, constraint_id: uuid.UUID, snapshot_id: int | None
) -> dict[str, Any] | None:
    query = (
        select(QualityConstraintResult)
        .where(QualityConstraintResult.constraint_id == constraint_id)
        .order_by(QualityConstraintResult.evaluated_at.desc())
        .limit(RESULT_RETENTION)
    )
    for row in session.scalars(query):
        if row.snapshot_id is not None and row.snapshot_id == snapshot_id:
            continue
        rows = (row.observed or {}).get("live_rows")
        if isinstance(rows, int):
            return {"live_rows": rows, "snapshot_id": row.snapshot_id}
    return None


def _record(
    session: Session,
    constraint: QualityConstraint,
    result: ConstraintResult,
    view: MetricsView,
    at: datetime,
) -> None:
    last = session.scalars(
        select(QualityConstraintResult)
        .where(QualityConstraintResult.constraint_id == constraint.id)
        .order_by(QualityConstraintResult.evaluated_at.desc())
        .limit(1)
    ).first()
    if (
        last is not None
        and last.snapshot_id == view.snapshot_id
        and last.status == result.status.value
        and (last.observed or {}) == result.observed
        and last.reason == result.reason
    ):
        last.evaluated_at = at
        return
    session.add(
        QualityConstraintResult(
            id=uuid.uuid4(),
            constraint_id=constraint.id,
            table_id=constraint.table_id,
            evaluated_at=at,
            snapshot_id=view.snapshot_id,
            scanned_at=view.scanned_at,
            status=result.status.value,
            observed=result.observed,
            reason=result.reason,
            evidence=result.evidence,
        )
    )
    session.flush()
    keep = (
        select(QualityConstraintResult.id)
        .where(QualityConstraintResult.constraint_id == constraint.id)
        .order_by(QualityConstraintResult.evaluated_at.desc())
        .limit(RESULT_RETENTION)
    )
    session.execute(
        delete(QualityConstraintResult).where(
            QualityConstraintResult.constraint_id == constraint.id,
            QualityConstraintResult.id.not_in(keep),
        ),
        execution_options={"synchronize_session": False},
    )
