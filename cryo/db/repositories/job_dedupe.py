"""Database side of duplicate-maintenance detection.

``active_coverage`` answers "what is already queued or running for these tables";
``supersede_covered_jobs`` retires queued duplicates once a job has done their work. The rules
for what counts as covered live in ``cryo.planner.job_coverage`` so the scheduler and API share
them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, MaintenanceJob, MaintenanceRun
from cryo.domain.enums import JobStatus
from cryo.planner.job_coverage import (
    ACTIVE_JOB_STATUSES,
    ActiveJob,
    effective_operations,
)

_SUPERSEDABLE_STATUSES = (JobStatus.QUEUED.value, JobStatus.DEFERRED.value)
_TABLE_ID_CHUNK = 1000


def _job_operations(value: Any) -> list[str] | None:
    return [str(op) for op in value] if isinstance(value, list) and value else None


def _run_operations(summary: Any) -> list[str] | None:
    if not isinstance(summary, dict):
        return None
    operations = summary.get("operations")
    if isinstance(operations, list) and operations:
        return [str(op) for op in operations]
    return None


def active_coverage(
    session: Session,
    table_ids: Iterable[uuid.UUID | str] | None = None,
) -> dict[str, list[ActiveJob]]:
    """Active (queued/admitted/running/deferred) jobs grouped by table id.

    ``table_ids=None`` loads the whole fleet in one query, which the scheduler needs once per
    tick; callers that already know their tables should pass them.
    """
    query = (
        select(
            MaintenanceJob.id,
            MaintenanceJob.table_id,
            MaintenanceJob.mode,
            MaintenanceJob.operations,
            MaintenanceRun.summary,
        )
        .join(MaintenanceRun, MaintenanceRun.id == MaintenanceJob.run_id)
        .where(MaintenanceJob.status.in_(ACTIVE_JOB_STATUSES))
    )
    coverage: dict[str, list[ActiveJob]] = {}

    def collect(rows: Iterable[Any]) -> None:
        for job_id, table_id, mode, job_operations, summary in rows:
            coverage.setdefault(str(table_id), []).append(
                ActiveJob(
                    job_id=str(job_id),
                    operations=effective_operations(
                        mode,
                        job_operations=_job_operations(job_operations),
                        run_operations=_run_operations(summary),
                    ),
                )
            )

    if table_ids is None:
        collect(session.execute(query).all())
        return coverage

    ids = [
        table_id if isinstance(table_id, uuid.UUID) else uuid.UUID(str(table_id))
        for table_id in table_ids
    ]
    for start in range(0, len(ids), _TABLE_ID_CHUNK):
        chunk = ids[start : start + _TABLE_ID_CHUNK]
        collect(session.execute(query.where(MaintenanceJob.table_id.in_(chunk))).all())
    return coverage


def supersede_covered_jobs(
    session: Session, job_id: uuid.UUID | str, *, commit: bool = True
) -> list[tuple[str, str]]:
    """Skip queued/deferred duplicates that a just-succeeded job already did the work for.

    A candidate is superseded only when it is for the same table, is not forced (a forced job
    has no dedupe_key and is exempt by design), was created no later than the succeeded job
    started (so it would have planned from the same pre-maintenance stats), and asks for a
    subset of what the succeeded job covered. Returns (job_id, destination_id) pairs so the
    caller can drop their Redis pending entries.
    """
    pk = job_id if isinstance(job_id, uuid.UUID) else uuid.UUID(str(job_id))
    done = session.get(MaintenanceJob, pk)
    if done is None:
        return []
    done_run = session.get(MaintenanceRun, done.run_id)
    covered = effective_operations(
        done.mode,
        job_operations=_job_operations(done.operations),
        run_operations=_run_operations(done_run.summary if done_run else None),
    )
    if not covered:
        return []
    cutoff = done.started_at or datetime.now(UTC)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)

    rows = session.execute(
        select(MaintenanceJob, MaintenanceRun)
        .join(MaintenanceRun, MaintenanceRun.id == MaintenanceJob.run_id)
        .where(
            MaintenanceJob.table_id == done.table_id,
            MaintenanceJob.id != done.id,
            MaintenanceJob.status.in_(_SUPERSEDABLE_STATUSES),
            MaintenanceJob.dedupe_key.is_not(None),
            MaintenanceRun.created_at <= cutoff,
        )
    ).all()

    now = datetime.now(UTC)
    superseded: list[tuple[str, str]] = []
    for candidate, candidate_run in rows:
        wanted = effective_operations(
            candidate.mode,
            job_operations=_job_operations(candidate.operations),
            run_operations=_run_operations(candidate_run.summary),
        )
        if not wanted or not wanted <= covered:
            continue
        candidate.status = JobStatus.SKIPPED.value
        candidate.error = f"superseded_by:{done.id}"
        candidate.finished_at = now
        session.add(
            AuditLog(
                table_id=candidate.table_id,
                job_id=candidate.id,
                action="job_superseded",
                status="SKIPPED",
                detail=f"superseded_by={done.id}; operations={','.join(sorted(wanted))}",
                actor="system:dedupe",
            )
        )
        superseded.append((str(candidate.id), str(candidate.destination_id)))
    if commit:
        session.commit()
    else:
        session.flush()
    return superseded
