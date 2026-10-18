"""Per-table nightly selection outcomes.

Retention is 90 days. ``prune_skip_evaluations`` deletes older rows; do not call it
from a request path.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    delete,
    event,
    func,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import TIMESTAMP

from cryo.db.base import Base
from cryo.db.models import MaintenanceRun, Table

logger = logging.getLogger(__name__)

REASON_COOLDOWN = "cooldown"
REASON_STALE_STATS = "stale_stats"
REASON_EXCLUDED = "excluded"
REASON_HELD = "held"
REASON_BUDGET_CAP = "budget_cap"
REASON_BACKOFF = "backoff"
REASON_NO_WORK = "no_work"

STAGE_EVALUATED = "evaluated"
STAGE_NEEDS_WORK = "needs_work"
STAGE_ELIGIBLE = "eligible"
STAGE_SELECTED = "selected"

RETENTION_DAYS = 90
_INSERT_CHUNK_POSTGRES = 1000
_INSERT_CHUNK_OTHER = 80

_PHASE_LOADED = "loaded"
_PHASE_RANKED = "ranked"
_PHASE_COMPLETE = "complete"


class SkipEvaluation(Base):
    __tablename__ = "skip_evaluations"
    __table_args__ = (
        UniqueConstraint("run_id", "table_id", name="uq_skip_evaluations_run_table"),
        Index("ix_skip_evaluations_run_id", "run_id"),
        Index("ix_skip_evaluations_table_evaluated", "table_id", "evaluated_at"),
        Index("ix_skip_evaluations_evaluated_at", "evaluated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cryo.runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    table_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cryo.tables.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    evaluated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


@dataclass
class _Pending:
    engine_id: int
    evaluated_at: datetime
    phase: str
    candidate_ids: frozenset[uuid.UUID]
    rows: dict[uuid.UUID, tuple[str, str | None]] = field(default_factory=dict)
    ranked_ids: frozenset[uuid.UUID] = frozenset()
    provisional: set[uuid.UUID] = field(default_factory=set)


_PENDING: ContextVar[_Pending | None] = ContextVar("cryo_skip_history", default=None)
_LISTENER = False


def engine_id_for(bind: Any) -> int:
    engine = getattr(bind, "engine", None)
    if engine is None or engine is bind:
        return id(bind)
    return id(engine)


def clear_pending() -> None:
    _PENDING.set(None)


def remember_loaded(
    *,
    engine_id: int,
    evaluated_at: datetime,
    candidate_ids: Sequence[uuid.UUID],
    exclusions: Sequence[tuple[uuid.UUID, str, bool]],
) -> None:
    rows: dict[uuid.UUID, tuple[str, str | None]] = {}
    for table_id, reason, has_work in exclusions:
        stage = STAGE_NEEDS_WORK if has_work else STAGE_EVALUATED
        rows[table_id] = (stage, reason)
    _PENDING.set(
        _Pending(
            engine_id=engine_id,
            evaluated_at=evaluated_at,
            phase=_PHASE_LOADED,
            candidate_ids=frozenset(candidate_ids),
            rows=rows,
        )
    )


def remember_ranked(
    *,
    candidate_ids: Sequence[uuid.UUID],
    ranked_ids: Sequence[uuid.UUID],
    no_work_ids: Sequence[uuid.UUID],
    limit_cut_ids: Sequence[uuid.UUID],
) -> None:
    pending = _PENDING.get()
    if pending is None or pending.phase != _PHASE_LOADED:
        return
    if frozenset(candidate_ids) != pending.candidate_ids:
        return
    for table_id in no_work_ids:
        pending.rows[table_id] = (STAGE_EVALUATED, REASON_NO_WORK)
    for table_id in limit_cut_ids:
        pending.rows[table_id] = (STAGE_ELIGIBLE, REASON_BUDGET_CAP)
    provisional: set[uuid.UUID] = set()
    for table_id in ranked_ids:
        pending.rows[table_id] = (STAGE_ELIGIBLE, None)
        provisional.add(table_id)
    pending.ranked_ids = frozenset(ranked_ids)
    pending.provisional = provisional
    pending.phase = _PHASE_RANKED


def remember_budget(
    *,
    ranked_ids: Sequence[uuid.UUID],
    kept_ids: Sequence[uuid.UUID],
    over_ids: Sequence[uuid.UUID],
) -> None:
    pending = _PENDING.get()
    if pending is None or pending.phase != _PHASE_RANKED:
        return
    if frozenset(ranked_ids) != pending.ranked_ids:
        return
    for table_id in over_ids:
        pending.rows[table_id] = (STAGE_ELIGIBLE, REASON_BUDGET_CAP)
        pending.provisional.discard(table_id)
    for table_id in kept_ids:
        pending.rows[table_id] = (STAGE_SELECTED, None)
        pending.provisional.discard(table_id)
    pending.phase = _PHASE_COMPLETE


def insert_skip_evaluations(connection: Connection, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    postgres = connection.dialect.name == "postgresql"
    size = _INSERT_CHUNK_POSTGRES if postgres else _INSERT_CHUNK_OTHER
    pending = list(rows)
    for start in range(0, len(pending), size):
        connection.execute(insert(SkipEvaluation).values(pending[start : start + size]))


def prune_skip_evaluations(session: Session, *, older_than: datetime) -> int:
    result = session.execute(delete(SkipEvaluation).where(SkipEvaluation.evaluated_at < older_than))
    return int(getattr(result, "rowcount", 0) or 0)


def list_skip_evaluations(
    session: Session,
    *,
    run_id: uuid.UUID | None = None,
    table_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    limit: int,
    offset: int,
) -> tuple[list[SkipEvaluation], int]:
    stmt = select(SkipEvaluation)
    if tenant_id is not None:
        stmt = stmt.join(Table, Table.id == SkipEvaluation.table_id).where(
            Table.tenant_id == tenant_id
        )
    if run_id is not None:
        stmt = stmt.where(SkipEvaluation.run_id == run_id)
    if table_id is not None:
        stmt = stmt.where(SkipEvaluation.table_id == table_id)
    total = int(session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = session.scalars(
        stmt.order_by(SkipEvaluation.evaluated_at.desc(), SkipEvaluation.table_id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def _row_dicts(pending: _Pending) -> list[dict[str, Any]]:
    promote = pending.phase == _PHASE_RANKED
    rows: list[dict[str, Any]] = []
    for table_id, (stage, reason) in pending.rows.items():
        if promote and table_id in pending.provisional:
            stage, reason = STAGE_SELECTED, None
        rows.append(
            {
                "id": uuid.uuid4(),
                "run_id": None,
                "table_id": table_id,
                "stage": stage,
                "reason": reason,
                "evaluated_at": pending.evaluated_at,
            }
        )
    return rows


def _should_attach(target: MaintenanceRun) -> bool:
    summary = target.summary if isinstance(target.summary, dict) else None
    if not summary or summary.get("pipeline") != "batch":
        return False
    if target.status == "running":
        return True
    return "trigger" in summary


def _attach_skip_history(mapper: Any, connection: Connection, target: MaintenanceRun) -> None:
    pending = _PENDING.get()
    if pending is None or engine_id_for(connection) != pending.engine_id:
        return
    if not _should_attach(target):
        return
    try:
        if pending.phase in {_PHASE_RANKED, _PHASE_COMPLETE}:
            rows = _row_dicts(pending)
            for row in rows:
                row["run_id"] = target.id
            insert_skip_evaluations(connection, rows)
    except Exception:
        logger.exception("skip history insert failed for run %s", target.id)
    finally:
        _PENDING.set(None)


def install_skip_history_listener() -> None:
    global _LISTENER
    if _LISTENER or event.contains(MaintenanceRun, "after_insert", _attach_skip_history):
        _LISTENER = True
        return
    event.listen(MaintenanceRun, "after_insert", _attach_skip_history)
    _LISTENER = True


install_skip_history_listener()
