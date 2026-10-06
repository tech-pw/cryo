from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import DeletionTracking


@dataclass(frozen=True)
class PendingDeletionSummary:
    table_id: uuid.UUID
    pending_rows: int
    earliest_soft_deleted_at: datetime
    blocked_until: datetime
    reason: str


class DeletionTrackingRepository:
    """DB-backed read/write for GDPR/CCPA deletion tracking.

    Move 19 from docs/strategy-next-moves-2026-07.md. The in-memory
    PendingDeletion / track_pending_deletions helpers in cryo.compliance
    only report a single table's stats-derived pending count. This
    repository persists each soft-delete event so the fleet-wide
    \"N pending hard deletions, blocked until expiry after <date>\"
    summary is queryable, auditable, and survives process restarts.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def record(
        self,
        *,
        table_id: uuid.UUID,
        subject_id: str,
        soft_deleted_at: datetime,
        pending_rows: int,
        blocked_until: datetime,
        reason: str,
    ) -> DeletionTracking:
        row = DeletionTracking(
            table_id=table_id,
            subject_id=subject_id,
            soft_deleted_at=soft_deleted_at,
            pending_rows=pending_rows,
            blocked_until=blocked_until,
            reason=reason,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def record_many(self, entries: Iterable[DeletionTracking]) -> int:
        rows = list(entries)
        if not rows:
            return 0
        self._session.add_all(rows)
        self._session.commit()
        return len(rows)

    def mark_resolved(self, table_id: uuid.UUID, *, up_to: datetime) -> int:
        rows = list(
            self._session.scalars(
                select(DeletionTracking).where(
                    DeletionTracking.table_id == table_id,
                    DeletionTracking.resolved_at.is_(None),
                    DeletionTracking.blocked_until <= up_to,
                )
            ).all()
        )
        now = datetime.now(UTC)
        for row in rows:
            row.resolved_at = now
        self._session.commit()
        return len(rows)

    def pending_for_table(self, table_id: uuid.UUID) -> list[DeletionTracking]:
        return list(
            self._session.scalars(
                select(DeletionTracking)
                .where(
                    DeletionTracking.table_id == table_id,
                    DeletionTracking.resolved_at.is_(None),
                )
                .order_by(DeletionTracking.blocked_until.asc())
            ).all()
        )

    def fleet_summary(self) -> list[PendingDeletionSummary]:
        stmt = (
            select(
                DeletionTracking.table_id,
                func.coalesce(func.sum(DeletionTracking.pending_rows), 0).label("rows"),
                func.min(DeletionTracking.soft_deleted_at).label("earliest"),
                DeletionTracking.blocked_until,
                DeletionTracking.reason,
            )
            .where(DeletionTracking.resolved_at.is_(None))
            .group_by(
                DeletionTracking.table_id,
                DeletionTracking.blocked_until,
                DeletionTracking.reason,
            )
            .order_by(DeletionTracking.blocked_until.asc())
        )
        return [
            PendingDeletionSummary(
                table_id=row.table_id,
                pending_rows=int(row.rows or 0),
                earliest_soft_deleted_at=row.earliest,
                blocked_until=row.blocked_until,
                reason=row.reason,
            )
            for row in self._session.execute(stmt).all()
        ]

    def total_pending_rows(self) -> int:
        stmt = select(func.coalesce(func.sum(DeletionTracking.pending_rows), 0)).where(
            DeletionTracking.resolved_at.is_(None)
        )
        return int(self._session.execute(stmt).scalar() or 0)
