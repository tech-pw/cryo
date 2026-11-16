"""Set a table's own health-grade limits (#552) and re-grade its latest scan to match."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, Table, TableStats
from cryo.scanner.health import (
    HEALTH_THRESHOLDS,
    THRESHOLD_OVERRIDES_KEY,
    compute_health_score,
    threshold_override_sources,
    validate_threshold_overrides,
)

AUDIT_ACTION = "table_health_thresholds"


@dataclass(frozen=True, slots=True)
class ThresholdChange:
    before: dict[str, int | float]
    after: dict[str, int | float]
    audit_id: int | None
    score_before: int | None
    score_after: int | None


def fleet_base() -> dict[str, int | float]:
    from cryo.config import get_settings

    return {**HEALTH_THRESHOLDS, **get_settings().health_threshold_overrides}


def set_table_health_thresholds(
    session: Session,
    table: Table,
    overrides: Mapping[str, Any],
    *,
    reason: str,
    actor: str,
    now: datetime | None = None,
) -> ThresholdChange:
    """Replace the table's overrides (an empty map clears them). Raises ValueError if invalid."""
    after = validate_threshold_overrides(overrides, base=fleet_base())
    before: dict[str, int | float] = dict(table.health_threshold_overrides or {})
    stats = session.get(TableStats, table.id)
    score_before = stats.overall_score if stats is not None else None
    if before == after:
        return ThresholdChange(before, after, None, score_before, score_before)
    table.health_threshold_overrides = after or None
    score_after = _regrade(stats, after, table.lifecycle) if stats is not None else None
    audit = AuditLog(
        table_id=table.id,
        action=AUDIT_ACTION,
        status="OK",
        detail=f"health thresholds {before or 'default'} -> {after or 'default'}; reason={reason}",
        actor=actor,
        result_metrics={
            "before": before,
            "after": after,
            "reason": reason,
            "score_before": score_before,
            "score_after": score_after,
            "at": (now or datetime.now(UTC)).isoformat(),
        },
    )
    session.add(audit)
    session.flush()
    return ThresholdChange(before, after, int(audit.id), score_before, score_after)


def _regrade(
    stats: TableStats, overrides: Mapping[str, int | float], lifecycle: str | None
) -> int | None:
    """Re-score the stored scan now, rather than waiting for the next scan."""
    raw = dict(stats.raw) if isinstance(stats.raw, dict) else {}
    if not isinstance(raw.get("health_deductions"), list):
        return stats.overall_score
    if overrides:
        raw[THRESHOLD_OVERRIDES_KEY] = dict(overrides)
    else:
        raw.pop(THRESHOLD_OVERRIDES_KEY, None)
    projected: dict[str, Any] = dict(raw)
    for column in TableStats.__table__.columns.keys():
        if column != "raw":
            projected[column] = getattr(stats, column, None)
    score, grade, deductions = compute_health_score(
        projected, int(stats.small_files_count or 0), now=stats.scan_at, lifecycle=lifecycle
    )
    raw["health_deductions"] = deductions
    sources = threshold_override_sources(raw)
    if sources:
        raw["health_threshold_sources"] = sources
    else:
        raw.pop("health_threshold_sources", None)
    stats.raw = raw
    stats.overall_score = score
    stats.grade = grade
    return score
