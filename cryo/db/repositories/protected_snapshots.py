from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import ProtectedSnapshot


class ProtectedSnapshotRepository:
    """DB-backed read/write for protected snapshot labels.

    The protected_snapshots table (migration 20260703_0003) tags specific
    Iceberg snapshot IDs so the planner / runner can keep them out of
    expire_snapshots -- the Move 17 DR feature.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_table(self, table_id: uuid.UUID) -> list[ProtectedSnapshot]:
        stmt = (
            select(ProtectedSnapshot)
            .where(ProtectedSnapshot.table_id == table_id)
            .order_by(ProtectedSnapshot.snapshot_id.asc())
        )
        return list(self._session.scalars(stmt).all())

    def list_snapshot_ids(self, table_id: uuid.UUID) -> set[int]:
        stmt = select(ProtectedSnapshot.snapshot_id).where(
            ProtectedSnapshot.table_id == table_id
        )
        return {row[0] for row in self._session.execute(stmt).all()}

    def protect(
        self,
        *,
        table_id: uuid.UUID,
        snapshot_id: int,
        label: str | None,
    ) -> ProtectedSnapshot:
        existing = self._session.scalar(
            select(ProtectedSnapshot).where(
                ProtectedSnapshot.table_id == table_id,
                ProtectedSnapshot.snapshot_id == snapshot_id,
            )
        )
        if existing is not None:
            if label is not None:
                existing.label = label
                existing.protected_at = datetime.now(UTC)
                self._session.commit()
                self._session.refresh(existing)
            return existing
        row = ProtectedSnapshot(
            table_id=table_id,
            snapshot_id=snapshot_id,
            label=label,
        )
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)
        return row

    def unprotect(
        self,
        *,
        table_id: uuid.UUID,
        snapshot_id: int,
    ) -> bool:
        row = self._session.scalar(
            select(ProtectedSnapshot).where(
                ProtectedSnapshot.table_id == table_id,
                ProtectedSnapshot.snapshot_id == snapshot_id,
            )
        )
        if row is None:
            return False
        self._session.delete(row)
        self._session.commit()
        return True

    def partition_expirable(
        self,
        table_id: uuid.UUID,
        candidate_ids: Iterable[int],
    ) -> tuple[set[int], set[int]]:
        protected = self.list_snapshot_ids(table_id)
        candidates = set(candidate_ids)
        expirable = candidates - protected
        kept = candidates & protected
        return expirable, kept
