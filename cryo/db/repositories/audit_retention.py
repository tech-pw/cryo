from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog


@dataclass(frozen=True)
class AuditRetentionResult:
    deleted: int
    remaining: int
    cutoff: datetime


def prune_audit_logs(
    session: Session,
    retention_days: int,
    *,
    now: datetime | None = None,
    batch_size: int = 10_000,
    max_rows: int = 100_000,
) -> AuditRetentionResult:
    """Delete expired audit rows without holding one unbounded transaction."""
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
                select(AuditLog.id)
                .where(AuditLog.ts < cutoff)
                .order_by(AuditLog.ts, AuditLog.id)
                .limit(limit)
            )
        )
        if not ids:
            break

        result = session.execute(delete(AuditLog).where(AuditLog.id.in_(ids)))
        session.commit()
        deleted = int(getattr(result, "rowcount", 0) or 0)
        deleted_total += deleted
        if deleted < len(ids):
            break

    remaining = int(
        session.scalar(select(func.count(AuditLog.id)).where(AuditLog.ts < cutoff)) or 0
    )
    return AuditRetentionResult(
        deleted=deleted_total,
        remaining=remaining,
        cutoff=cutoff,
    )
