"""Why an active job is waiting, and a rough ETA, for the table run grid."""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from cryo.api.deps import SessionDep, require_viewer
from cryo.db.models import MaintenanceJob

router = APIRouter(prefix="/api/v1", tags=["runs"])

_ACTIVE = ("queued", "admitted", "running", "deferred")
_THROUGHPUT_WINDOW = timedelta(hours=1)
_DURATION_SAMPLE = 10


class JobQueueStatus(BaseModel):
    job_id: UUID
    status: str
    reason: str
    jobs_ahead: int | None = None
    eta_seconds: float | None = None
    eta_basis: str | None = None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _typical_duration(session: SessionDep, table_id: UUID) -> float | None:
    rows = session.execute(
        select(MaintenanceJob.started_at, MaintenanceJob.finished_at)
        .where(
            MaintenanceJob.table_id == table_id,
            MaintenanceJob.status == "succeeded",
            MaintenanceJob.started_at.is_not(None),
            MaintenanceJob.finished_at.is_not(None),
        )
        .order_by(MaintenanceJob.finished_at.desc())
        .limit(_DURATION_SAMPLE)
    ).all()
    durations = [
        (_aware(end) - _aware(start)).total_seconds()  # type: ignore[operator]
        for start, end in rows
        if start and end
    ]
    return statistics.median(durations) if durations else None


def _finished_per_hour(session: SessionDep, destination_id: UUID, now: datetime) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(MaintenanceJob)
            .where(
                MaintenanceJob.destination_id == destination_id,
                MaintenanceJob.finished_at >= now - _THROUGHPUT_WINDOW,
                MaintenanceJob.status.in_(("succeeded", "failed", "partial", "skipped")),
            )
        )
        or 0
    )


def _count_on_destination(
    session: SessionDep, destination_id: UUID, statuses: tuple[str, ...]
) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(MaintenanceJob)
            .where(
                MaintenanceJob.destination_id == destination_id,
                MaintenanceJob.status.in_(statuses),
            )
        )
        or 0
    )


@router.get(
    "/jobs/{job_id}/queue-status",
    response_model=JobQueueStatus,
    dependencies=[Depends(require_viewer)],
)
def get_job_queue_status(job_id: UUID, session: SessionDep) -> JobQueueStatus:
    job = session.get(MaintenanceJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    status = job.status
    if status not in _ACTIVE:
        return JobQueueStatus(job_id=job.id, status=status, reason="The job has finished.")
    now = datetime.now(UTC)
    typical = _typical_duration(session, job.table_id)
    rate = _finished_per_hour(session, job.destination_id, now)

    def wait_eta(ahead: int) -> float | None:
        if rate <= 0:
            return None
        return (ahead + 1) / rate * 3600 + (typical or 0)

    if status == "running":
        started = _aware(job.started_at)
        if typical is None or started is None:
            return JobQueueStatus(
                job_id=job.id, status=status, reason="Running on the engine.", eta_basis=None
            )
        elapsed = (now - started).total_seconds()
        return JobQueueStatus(
            job_id=job.id,
            status=status,
            reason="Running on the engine.",
            eta_seconds=max(0.0, typical - elapsed),
            eta_basis=f"median of this table's last {_DURATION_SAMPLE} successful runs",
        )
    if status == "admitted":
        ahead = _count_on_destination(session, job.destination_id, ("admitted",)) - 1
        return JobQueueStatus(
            job_id=job.id,
            status=status,
            reason="Admitted; waiting for a free worker to pick it up.",
            jobs_ahead=max(0, ahead),
            eta_seconds=wait_eta(max(0, ahead)),
            eta_basis="jobs this engine finished in the last hour" if rate else None,
        )
    if status == "deferred":
        reason = (
            job.error or ""
        ).strip() or "A safety gate deferred it; it is retried automatically."
        return JobQueueStatus(
            job_id=job.id,
            status=status,
            reason=f"Deferred: {reason}"[:400],
        )
    ahead = _count_on_destination(session, job.destination_id, ("queued", "admitted")) - 1
    return JobQueueStatus(
        job_id=job.id,
        status=status,
        reason="Queued; waiting for the governor to admit it when the engine has a free slot.",
        jobs_ahead=max(0, ahead),
        eta_seconds=wait_eta(max(0, ahead)),
        eta_basis="jobs this engine finished in the last hour" if rate else None,
    )
