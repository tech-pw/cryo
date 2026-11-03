"""Hard-delete tables tombstoned as removed from the catalog (#330).

The scanner tombstones a table its catalog no longer lists (#120). Once
``removed_table_purge_after_days`` have passed, this deletes the row; the database cascades its
per-table children and clears ``table_id`` on audit and diagnostic rows (migration
20261103_1200). Each purge commits on its own and writes a ``table_purged`` audit entry that
names the table, so one failure never rolls back the rest.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, and_, delete, exists, func, not_, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, MaintenanceIntent, MaintenanceJob, Source, Table
from cryo.domain.enums import JobStatus
from cryo.domain.intents import IntentStatus

logger = logging.getLogger(__name__)

TABLE_PURGED_ACTION = "table_purged"

_ACTIVE_JOB_STATUSES = (
    JobStatus.QUEUED.value,
    JobStatus.ADMITTED.value,
    JobStatus.RUNNING.value,
    JobStatus.DEFERRED.value,
)
_OPEN_INTENT_STATUSES = (
    IntentStatus.PENDING_APPROVAL.value,
    IntentStatus.READY.value,
    IntentStatus.EXECUTING.value,
    IntentStatus.RECONCILE_REQUIRED.value,
)


@dataclass(frozen=True)
class PurgeCandidate:
    id: uuid.UUID
    source_name: str
    schema_name: str
    table_name: str
    removed_at: datetime


@dataclass(frozen=True)
class RemovedTablePurgeResult:
    purged: int
    failed: int
    # Past the window but holding an active job or open intent; retried next tick.
    held: int
    # Past the window and not held, left for a later tick by max_tables.
    remaining: int
    cutoff: datetime


def _past_window(cutoff: datetime) -> ColumnElement[bool]:
    return and_(
        Table.removed_from_catalog_at.is_not(None),
        Table.removed_from_catalog_at < cutoff,
        Table.is_active.is_(False),
    )


def _busy() -> ColumnElement[bool]:
    active_job = exists().where(
        MaintenanceJob.table_id == Table.id, MaintenanceJob.status.in_(_ACTIVE_JOB_STATUSES)
    )
    open_intent = exists().where(
        MaintenanceIntent.table_id == Table.id,
        MaintenanceIntent.status.in_(_OPEN_INTENT_STATUSES),
    )
    return active_job | open_intent


def _count(session: Session, *criteria: ColumnElement[bool]) -> int:
    return int(session.scalar(select(func.count(Table.id)).where(*criteria)) or 0)


def purge_candidates(session: Session, cutoff: datetime, limit: int) -> list[PurgeCandidate]:
    rows = session.execute(
        select(
            Table.id,
            Source.name,
            Table.schema_name,
            Table.table_name,
            Table.removed_from_catalog_at,
        )
        .join(Source, Source.id == Table.source_id)
        .where(_past_window(cutoff), not_(_busy()))
        .order_by(Table.removed_from_catalog_at, Table.id)
        .limit(limit)
    ).all()
    return [PurgeCandidate(*row) for row in rows]


def purge_removed_tables(
    session: Session,
    retention_days: int,
    *,
    now: datetime | None = None,
    max_tables: int = 500,
    dry_run: bool = False,
    actor: str = "scheduler",
) -> RemovedTablePurgeResult:
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    if max_tables <= 0:
        raise ValueError("max_tables must be positive")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    held = _count(session, _past_window(cutoff), _busy())
    candidates = purge_candidates(session, cutoff, max_tables)
    session.rollback()
    if dry_run:
        eligible = _count(session, _past_window(cutoff), not_(_busy()))
        return RemovedTablePurgeResult(
            purged=0, failed=0, held=held, remaining=eligible, cutoff=cutoff
        )

    purged = failed = 0
    for candidate in candidates:
        try:
            # Re-checked in the DELETE: a rescan may have restored the table, or a job may
            # have been queued for it, since the candidates were read.
            result = session.execute(
                delete(Table).where(Table.id == candidate.id, _past_window(cutoff), not_(_busy()))
            )
            if not int(getattr(result, "rowcount", 0) or 0):
                session.rollback()
                continue
            session.add(
                AuditLog(
                    action=TABLE_PURGED_ACTION,
                    status="OK",
                    engine="cryo",
                    actor=actor,
                    detail=(
                        f"table={candidate.source_name}.{candidate.schema_name}."
                        f"{candidate.table_name} table_id={candidate.id} "
                        f"removed_from_catalog_at={candidate.removed_at.isoformat()} "
                        f"retention_days={retention_days}"
                    ),
                )
            )
            session.commit()
            purged += 1
        except Exception:
            session.rollback()
            failed += 1
            logger.exception("Could not purge removed table %s", candidate.id)
    remaining = _count(session, _past_window(cutoff), not_(_busy()))
    session.rollback()
    return RemovedTablePurgeResult(
        purged=purged, failed=failed, held=held, remaining=remaining, cutoff=cutoff
    )
