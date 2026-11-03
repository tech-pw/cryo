"""Pause/resume and default-preset switches (#119).

Paused holds a preset's tables: they keep it as their effective preset but no scheduled run
fires. Pausing also cancels the preset's jobs that are not yet admitted (queued or deferred);
admitted and running jobs finish (#330). Unsetting the default leaves unassigned tables with
no effective preset. Every change is audited with the actor, before/after and the number of
tables the preset currently covers.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.api.deps import CurrentPrincipal, SessionDep, require_admin
from cryo.api.schemas import PresetRead
from cryo.db.models import AuditLog, MaintenanceJob, MaintenanceRun, Preset
from cryo.db.repositories import presets as presets_repo
from cryo.db.tenant_scope import get_scoped_resource, tenant_filter_id
from cryo.domain.enums import JobStatus
from cryo.infra.wiring import build_runtime_wiring

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/presets", tags=["presets"])

PRESET_PAUSED_JOB_ERROR = "cancelled: preset paused"
PAUSE_CANCELS_DESCRIPTION = (
    "Pausing cancels the preset's jobs that are not yet admitted (status queued or deferred): "
    f"they become skipped with error '{PRESET_PAUSED_JOB_ERROR}' and leave the queue. Admitted "
    "and running jobs finish. A run left with no active job is marked cancelled. The counts "
    "are recorded on the preset_paused audit row."
)

_OPEN_RUN_STATUSES = ("scheduled", "queued", "running")
_UNADMITTED_JOB_STATUSES = (JobStatus.QUEUED.value, JobStatus.DEFERRED.value)
_ACTIVE_JOB_STATUSES = (
    JobStatus.QUEUED.value,
    JobStatus.ADMITTED.value,
    JobStatus.RUNNING.value,
    JobStatus.DEFERRED.value,
)


@dataclass(frozen=True)
class PauseCancellation:
    jobs: int
    runs: int
    # (job_id, destination_id) to drop from the Redis pending queue after commit.
    queue_entries: tuple[tuple[str, str], ...]


def cancel_unadmitted_jobs(session: Session, preset: Preset) -> PauseCancellation:
    """Skip the preset's queued and deferred jobs in open runs. Call before commit.

    Neither status holds a table lock: a deferred job is one whose lock or admission was
    refused, so there is nothing to release beyond the queue entry.
    """
    now = datetime.now(UTC)
    jobs = list(
        session.scalars(
            select(MaintenanceJob)
            .join(MaintenanceRun, MaintenanceRun.id == MaintenanceJob.run_id)
            .where(
                MaintenanceRun.preset_id == preset.id,
                MaintenanceRun.status.in_(_OPEN_RUN_STATUSES),
                MaintenanceJob.status.in_(_UNADMITTED_JOB_STATUSES),
            )
        )
    )
    if not jobs:
        return PauseCancellation(jobs=0, runs=0, queue_entries=())
    run_ids = {job.run_id for job in jobs}
    for job in jobs:
        job.status = JobStatus.SKIPPED.value
        job.error = PRESET_PAUSED_JOB_ERROR
        job.finished_at = now
        job.deferred_since = None
    session.flush()
    still_active = set(
        session.scalars(
            select(MaintenanceJob.run_id)
            .where(
                MaintenanceJob.run_id.in_(run_ids),
                MaintenanceJob.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .distinct()
        )
    )
    emptied = 0
    for run in session.scalars(select(MaintenanceRun).where(MaintenanceRun.id.in_(run_ids))):
        if run.id in still_active:
            continue
        # A run whose jobs were all skipped would otherwise finalize as "succeeded".
        run.status = "cancelled"
        run.finished_at = run.finished_at or now
        run.summary = {**(run.summary or {}), "cancelled_reason": "preset_paused"}
        emptied += 1
    return PauseCancellation(
        jobs=len(jobs),
        runs=emptied,
        queue_entries=tuple((str(job.id), str(job.destination_id)) for job in jobs),
    )


def drop_cancelled_from_queue(cancellation: PauseCancellation, preset_id: uuid.UUID) -> None:
    """Best-effort after commit. A leftover entry is purged by the governor, which drops
    pending entries whose job row is terminal."""
    if not cancellation.queue_entries:
        return
    try:
        queue = build_runtime_wiring().queue
        for job_id, destination_id in cancellation.queue_entries:
            queue.cancel_pending(job_id, destination_id)
    except Exception:
        logger.warning(
            "Could not drop jobs of paused preset %s from the queue", preset_id, exc_info=True
        )


DEFAULT_REQUIRES_SCHEDULE = (
    "A default preset needs a schedule (schedule_cron): the scheduler only fires presets "
    "with a cron, so without one the tables it catches are never maintained."
)


def ensure_default_has_schedule(*, is_default: bool, schedule_cron: str | None) -> None:
    if is_default and not (schedule_cron or "").strip():
        raise HTTPException(status_code=400, detail=DEFAULT_REQUIRES_SCHEDULE)


def lifecycle_state(preset: Preset) -> dict[str, bool]:
    return {
        "enabled": bool(preset.enabled),
        "paused": bool(preset.paused),
        "is_default": bool(preset.is_default),
    }


def current_default_id(session: Session, preset: Preset) -> uuid.UUID | None:
    from sqlalchemy import select

    stmt = select(Preset.id).where(Preset.is_default.is_(True), Preset.id != preset.id)
    tenant_id = getattr(preset, "tenant_id", None)
    stmt = (
        stmt.where(Preset.tenant_id.is_(None))
        if tenant_id is None
        else stmt.where(Preset.tenant_id == tenant_id)
    )
    return session.scalar(stmt.limit(1))


def covered_table_count(session: Session, preset: Preset) -> int:
    """Tables whose effective preset is this one (paused presets still count)."""
    session.flush()
    _, estimate = presets_repo.simulate_preset(
        session, preset, sample_limit=0, tenant_id=tenant_filter_id(session)
    )
    return int(estimate["matched_count"])


def audit_lifecycle_change(
    session: Session,
    preset: Preset,
    before: Mapping[str, bool],
    *,
    covered_before: int,
    actor: str | None,
    previous_default: uuid.UUID | None = None,
    cancellation: PauseCancellation | None = None,
) -> None:
    """One AuditLog row per changed switch. Call after the change is applied, before commit."""
    after = lifecycle_state(preset)
    changes: list[tuple[str, str]] = []
    if before["paused"] != after["paused"]:
        changes.append(("paused", "preset_paused" if after["paused"] else "preset_resumed"))
    if before["is_default"] != after["is_default"]:
        changes.append(
            ("is_default", "preset_default_set" if after["is_default"] else "preset_default_unset")
        )
    if before["enabled"] != after["enabled"]:
        changes.append(("enabled", "preset_enabled" if after["enabled"] else "preset_disabled"))
    if not changes:
        return
    covered_after = covered_table_count(session, preset)
    for field, action in changes:
        detail = (
            f"preset={preset.name} preset_id={preset.id} "
            f"{field}={before[field]}->{after[field]} "
            f"affected_tables={covered_before} tables_after={covered_after}"
        )
        if field == "is_default" and after["is_default"] and previous_default is not None:
            detail += f" previous_default={previous_default}"
        if field == "paused" and after["paused"] and cancellation is not None:
            detail += f" cancelled_jobs={cancellation.jobs} cancelled_runs={cancellation.runs}"
        session.add(AuditLog(action=action, status="OK", engine="cryo", detail=detail, actor=actor))


def _transition(
    session: Session,
    preset_id: uuid.UUID,
    principal_subject: str | None,
    *,
    paused: bool | None = None,
    is_default: bool | None = None,
) -> Preset:
    preset = get_scoped_resource(session, Preset, preset_id, resource_type="Preset")
    before = lifecycle_state(preset)
    covered_before = covered_table_count(session, preset)
    previous_default: uuid.UUID | None = None
    if paused is not None:
        preset.paused = paused
    if is_default is not None:
        if is_default:
            ensure_default_has_schedule(is_default=True, schedule_cron=preset.schedule_cron)
            if not preset.enabled:
                raise HTTPException(
                    status_code=400, detail="A disabled preset cannot be the default."
                )
            previous_default = current_default_id(session, preset)
            presets_repo.clear_other_default_presets(
                session,
                tenant_id=getattr(preset, "tenant_id", None),
                keep_preset_id=preset.id,
            )
        preset.is_default = is_default
    cancellation = (
        cancel_unadmitted_jobs(session, preset) if paused and not before["paused"] else None
    )
    audit_lifecycle_change(
        session,
        preset,
        before,
        covered_before=covered_before,
        actor=principal_subject,
        previous_default=previous_default,
        cancellation=cancellation,
    )
    session.commit()
    if cancellation is not None:
        drop_cancelled_from_queue(cancellation, preset.id)
    session.refresh(preset)
    return preset


@router.post(
    "/{preset_id}/pause",
    response_model=PresetRead,
    dependencies=[Depends(require_admin)],
    description=PAUSE_CANCELS_DESCRIPTION,
)
def pause_preset(preset_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal) -> Preset:
    return _transition(session, preset_id, principal.subject, paused=True)


@router.post(
    "/{preset_id}/resume", response_model=PresetRead, dependencies=[Depends(require_admin)]
)
def resume_preset(preset_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal) -> Preset:
    return _transition(session, preset_id, principal.subject, paused=False)


@router.post(
    "/{preset_id}/set-default", response_model=PresetRead, dependencies=[Depends(require_admin)]
)
def set_default_preset(
    preset_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal
) -> Preset:
    return _transition(session, preset_id, principal.subject, is_default=True)


@router.post(
    "/{preset_id}/unset-default",
    response_model=PresetRead,
    dependencies=[Depends(require_admin)],
)
def unset_default_preset(
    preset_id: uuid.UUID, session: SessionDep, principal: CurrentPrincipal
) -> Preset:
    return _transition(session, preset_id, principal.subject, is_default=False)
