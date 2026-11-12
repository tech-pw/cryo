"""Set a table's lifecycle (#519) and re-grade its latest scan to match."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, Table, TableStats
from cryo.domain.lifecycle import TableLifecycle
from cryo.scanner.health import apply_lifecycle

AUDIT_ACTION = "table_lifecycle"


@dataclass(frozen=True, slots=True)
class LifecycleChange:
    before: str
    after: str
    audit_id: int | None
    score_before: int | None
    score_after: int | None


def set_table_lifecycle(
    session: Session,
    table: Table,
    lifecycle: TableLifecycle,
    *,
    reason: str,
    actor: str,
    now: datetime | None = None,
) -> LifecycleChange:
    before = table.lifecycle or TableLifecycle.ACTIVE.value
    after = lifecycle.value
    stats = session.get(TableStats, table.id)
    score_before = stats.overall_score if stats is not None else None
    if before == after:
        return LifecycleChange(before, after, None, score_before, score_before)
    current = now or datetime.now(UTC)
    table.lifecycle = after
    table.lifecycle_changed_at = current
    table.lifecycle_changed_by = actor
    table.lifecycle_reason = reason
    score_after = _regrade(stats, after) if stats is not None else None
    audit = AuditLog(
        table_id=table.id,
        action=AUDIT_ACTION,
        status="OK",
        detail=f"lifecycle {before} -> {after}; reason={reason}",
        actor=actor,
        result_metrics={
            "before": before,
            "after": after,
            "reason": reason,
            "score_before": score_before,
            "score_after": score_after,
        },
    )
    session.add(audit)
    session.flush()
    return LifecycleChange(before, after, int(audit.id), score_before, score_after)


def _regrade(stats: TableStats, lifecycle: str) -> int | None:
    """Apply the lifecycle to the stored grade now, rather than waiting for the next scan."""
    raw = dict(stats.raw) if isinstance(stats.raw, dict) else {}
    deductions = raw.get("health_deductions")
    if not isinstance(deductions, list):
        return stats.overall_score
    projected: dict[str, Any] = dict(raw)
    for column in TableStats.__table__.columns.keys():
        if column != "raw":
            projected[column] = getattr(stats, column, None)
    score, grade, kept = apply_lifecycle(
        deductions, lifecycle=lifecycle, stats=projected, now=stats.scan_at
    )
    raw["health_deductions"] = kept
    stats.raw = raw
    stats.overall_score = score
    stats.grade = grade
    return score
