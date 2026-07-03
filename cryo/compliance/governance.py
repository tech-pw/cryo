from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class PendingDeletion:
    table_ref: str
    pending_rows: int
    blocked_until: datetime | None
    reason: str


def track_pending_deletions(
    table_ref: str,
    stats: Mapping[str, Any],
    *,
    retention_days: int = 7,
) -> PendingDeletion | None:
    pending_rows = int(
        stats.get("pending_soft_delete_rows") or stats.get("deleted_rows_pending") or 0
    )
    if pending_rows <= 0:
        return None
    blocked_until = datetime.now(UTC)
    return PendingDeletion(
        table_ref=table_ref,
        pending_rows=pending_rows,
        blocked_until=blocked_until,
        reason=f"Hard deletion blocked until snapshot expiry after {retention_days} days.",
    )


@dataclass(frozen=True)
class ProtectedSnapshot:
    snapshot_id: int
    label: str
    protected_at: datetime


class ProtectedSnapshotRegistry:
    def __init__(self) -> None:
        self._protected: dict[str, set[int]] = {}

    def protect(self, table_ref: str, snapshot_id: int) -> None:
        self._protected.setdefault(table_ref, set()).add(snapshot_id)

    def is_protected(self, table_ref: str, snapshot_id: int) -> bool:
        return snapshot_id in self._protected.get(table_ref, set())

    def expire_candidates(
        self,
        table_ref: str,
        snapshot_ids: list[int],
    ) -> tuple[list[int], list[int]]:
        protected = self._protected.get(table_ref, set())
        kept = [snapshot_id for snapshot_id in snapshot_ids if snapshot_id in protected]
        expirable = [snapshot_id for snapshot_id in snapshot_ids if snapshot_id not in protected]
        return expirable, kept
