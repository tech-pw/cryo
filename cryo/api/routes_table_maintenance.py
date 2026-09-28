"""Read-only nightly eligibility for one table.

``GET /api/v1/tables/{table_id}/maintenance-status`` (viewer). Reasons come from the
per-table predicates in ``load_candidates``, ``is_eligible``, and ``scan_retry_due_at``.
The router is not mounted here; ``app.py`` is owned by other work.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.api.deps import SessionDep, require_viewer
from cryo.config import get_settings
from cryo.db.models import MaintenanceJob, Source, Table, TableStats
from cryo.maintenance.selection import (
    COOLDOWN_JOB_STATUSES,
    DEFAULT_MIN_DELETE_FILE_PCT,
    BlockingReason,
    MaintenanceEligibilityFacts,
    maintenance_blocking_reasons,
    nightly_selection_eligible,
)
from cryo.scanner.persistence import scan_retry_due_at

router = APIRouter(prefix="/api/v1", tags=["tables"])

LAST_ACTIONS_NOTE = (
    "No existing repository function returns the latest audit or job row per operation "
    "without an unbounded scan. table_audit is bounded by limit but does not group by "
    "operation, so last_actions is empty."
)
NEXT_NIGHTLY_NOTE = (
    "The nightly cron is Helm maintenance.schedule "
    "(deploy/helm/cryo/templates/maintenance-cronjob.yaml), not an application setting. "
    "next_nightly_at is null because it is not derivable without new config."
)
ELIGIBILITY_BASIS = (
    "Nightly batch: load_candidates with cooldown applied, then is_eligible "
    "(rank_candidates require_eligible=True, min_delete_file_pct default). "
    "eligible is false only when a blocking reason has blocks_nightly_selection. "
    "scan_retry_backoff comes from scan_retry_due_at and does not by itself fail selection. "
    "An ad-hoc run that passes table_ids skips cooldown and the eligibility filter."
)
OVER_BUDGET_WHERE = "cryo.maintenance.selection.fit_budget, applied by BatchMaintenance._select"
OVER_BUDGET_WHY = (
    "Not a load_candidates predicate. It lives in fit_budget, which "
    "BatchMaintenance._select applies after ranking. A table is over budget only "
    "relative to the run's remaining GB·hr, so this endpoint does not invent a per-table rule."
)


class BlockingReasonRead(BaseModel):
    code: str
    summary: str
    predicate: str
    blocks_nightly_selection: bool
    until: datetime | None = None
    values: dict[str, Any] = Field(default_factory=dict)


class OmittedGateRead(BaseModel):
    code: str
    where: str
    why: str


class MaintenanceThresholdsRead(BaseModel):
    cooldown_hours: float
    max_stats_age_days: float
    min_delete_file_pct: float


class MaintenanceStatusRead(BaseModel):
    table_id: uuid.UUID
    eligible: bool
    evaluated_at: datetime
    eligibility_basis: str
    blocking_reasons: list[BlockingReasonRead]
    thresholds: MaintenanceThresholdsRead
    last_actions: list[dict[str, Any]]
    last_actions_note: str
    next_nightly_at: datetime | None
    next_nightly_note: str
    omitted: list[OmittedGateRead]


def _omitted_gates() -> list[OmittedGateRead]:
    return [
        OmittedGateRead(
            code="over_budget_last_run",
            where=OVER_BUDGET_WHERE,
            why=OVER_BUDGET_WHY,
        )
    ]


def _latest_successful_finished_at(session: Session, table_id: uuid.UUID) -> datetime | None:
    return session.scalar(
        select(MaintenanceJob.finished_at)
        .where(
            MaintenanceJob.table_id == table_id,
            MaintenanceJob.status.in_(COOLDOWN_JOB_STATUSES),
            MaintenanceJob.finished_at.is_not(None),
        )
        .order_by(MaintenanceJob.finished_at.desc())
        .limit(1)
    )


def _load_facts(session: Session, table: Table) -> MaintenanceEligibilityFacts:
    source = session.execute(
        select(Source.enabled, Source.maintenance_schema_names).where(Source.id == table.source_id)
    ).one_or_none()
    stats = session.execute(
        select(
            TableStats.scan_at,
            TableStats.needs_compaction,
            TableStats.delete_file_pct,
        ).where(TableStats.table_id == table.id)
    ).one_or_none()
    return MaintenanceEligibilityFacts(
        is_active=table.is_active is True,
        source_enabled=bool(source.enabled) if source is not None else False,
        source_present=source is not None,
        last_scan_error_kind=table.last_scan_error_kind,
        maintenance_retry_after=table.maintenance_retry_after,
        stats_present=stats is not None,
        scan_at=stats.scan_at if stats is not None else None,
        schema_name=table.schema_name,
        maintenance_schema_names=(source.maintenance_schema_names if source is not None else None),
        needs_compaction=bool(stats.needs_compaction) if stats is not None else False,
        delete_file_pct=float(stats.delete_file_pct or 0.0) if stats is not None else 0.0,
        last_successful_finished_at=_latest_successful_finished_at(session, table.id),
        consecutive_scan_failures=table.consecutive_scan_failures or 0,
        last_scan_error_at=table.last_scan_error_at,
        scan_retry_due_at=scan_retry_due_at(table),
    )


def _reason_read(reason: BlockingReason) -> BlockingReasonRead:
    return BlockingReasonRead(
        code=reason.code.value,
        summary=reason.summary,
        predicate=reason.predicate.value,
        blocks_nightly_selection=reason.blocks_nightly_selection,
        until=reason.until,
        values=reason.values,
    )


@router.get(
    "/tables/{table_id}/maintenance-status",
    response_model=MaintenanceStatusRead,
    dependencies=[Depends(require_viewer)],
)
def get_table_maintenance_status(
    table_id: uuid.UUID,
    session: SessionDep,
) -> MaintenanceStatusRead:
    """Why this table would or would not enter the nightly batch."""
    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")

    settings = get_settings()
    now = datetime.now(UTC)
    cooldown_hours = float(settings.maintenance_cooldown_hours)
    max_stats_age_days = float(settings.maintenance_max_stats_age_days)
    reasons = maintenance_blocking_reasons(
        _load_facts(session, table),
        now=now,
        cooldown_hours=cooldown_hours,
        max_stats_age_days=max_stats_age_days,
    )
    return MaintenanceStatusRead(
        table_id=table_id,
        eligible=nightly_selection_eligible(reasons),
        evaluated_at=now,
        eligibility_basis=ELIGIBILITY_BASIS,
        blocking_reasons=[_reason_read(reason) for reason in reasons],
        thresholds=MaintenanceThresholdsRead(
            cooldown_hours=cooldown_hours,
            max_stats_age_days=max_stats_age_days,
            min_delete_file_pct=DEFAULT_MIN_DELETE_FILE_PCT,
        ),
        last_actions=[],
        last_actions_note=LAST_ACTIONS_NOTE,
        next_nightly_at=None,
        next_nightly_note=NEXT_NIGHTLY_NOTE,
        omitted=_omitted_gates(),
    )
