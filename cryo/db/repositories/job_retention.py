from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, MaintenanceJob


@dataclass(frozen=True)
class JobRetentionResult:
    deleted: int
    remaining: int
    cutoff: datetime


def prune_finished_jobs(
    session: Session,
    retention_days: int,
    *,
    now: datetime | None = None,
    batch_size: int = 10_000,
    max_rows: int = 100_000,
) -> JobRetentionResult:
    """Delete terminal job records older than the retention window.

    Only ever touches jobs with a finished_at timestamp -- queued/admitted/running jobs have
    finished_at = NULL and are never eligible, so an in-flight job can't be pruned mid-run.

    audit_log.job_id has no ondelete cascade (deliberately -- the audit trail outlives the job
    it references, and audit_log_retention_days defaults to 365d, far longer than any sane job
    retention window). Deleting a job a surviving audit_log row still points at would otherwise
    hit a foreign-key violation, so each batch nulls out that reference first; the audit row's
    own fields (table_id, action, status, bytes freed) carry the record on their own.
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if max_rows <= 0:
        raise ValueError("max_rows must be positive")

    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    deleted_total = 0

    while deleted_total < max_rows:
        limit = min(batch_size, max_rows - deleted_total)
        ids = list(
            session.scalars(
                select(MaintenanceJob.id)
                .where(
                    MaintenanceJob.finished_at.is_not(None),
                    MaintenanceJob.finished_at < cutoff,
                )
                .order_by(MaintenanceJob.finished_at, MaintenanceJob.id)
                .limit(limit)
            )
        )
        if not ids:
            break

        session.execute(update(AuditLog).where(AuditLog.job_id.in_(ids)).values(job_id=None))
        result = session.execute(delete(MaintenanceJob).where(MaintenanceJob.id.in_(ids)))
        session.commit()
        deleted = int(getattr(result, "rowcount", 0) or 0)
        deleted_total += deleted
        if deleted < len(ids):
            break

    remaining = int(
        session.scalar(
            select(func.count(MaintenanceJob.id)).where(
                MaintenanceJob.finished_at.is_not(None),
                MaintenanceJob.finished_at < cutoff,
            )
        )
        or 0
    )
    return JobRetentionResult(
        deleted=deleted_total,
        remaining=remaining,
        cutoff=cutoff,
    )
