from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import MaintenanceJob, MaintenanceRun
from cryo.domain.enums import JobStatus

TERMINAL = {
    JobStatus.SUCCEEDED.value,
    JobStatus.FAILED.value,
    JobStatus.SKIPPED.value,
    JobStatus.PARTIAL.value,
}


def finalize_open_runs(session_factory: Callable[[], Session]) -> int:
    """Mark runs finished when all child jobs reached a terminal state."""
    finalized = 0
    with session_factory() as session:
        runs = session.scalars(select(MaintenanceRun).where(MaintenanceRun.status != "cancelled"))
        for run in runs:
            rows = session.execute(
                select(MaintenanceJob.status, func.count())
                .where(MaintenanceJob.run_id == run.id)
                .group_by(MaintenanceJob.status)
            ).all()
            counts: dict[str, int] = {str(row[0]): int(row[1]) for row in rows}
            if not counts:
                continue
            if any(status not in TERMINAL for status in counts):
                if run.status != "running":
                    run.status = "running"
                continue
            succeeded_count = counts.get(JobStatus.SUCCEEDED.value, 0)
            failed_count = counts.get(JobStatus.FAILED.value, 0)
            skipped_count = counts.get(JobStatus.SKIPPED.value, 0)
            partial_count = counts.get(JobStatus.PARTIAL.value, 0)
            if failed_count == 0 and partial_count == 0:
                run.status = "succeeded"
            elif succeeded_count > 0 or partial_count > 0:
                run.status = "partial"
            else:
                run.status = "failed"
            run.finished_at = datetime.now(UTC)
            run.summary = {
                "jobs_total": sum(counts.values()),
                "succeeded": succeeded_count,
                "failed": failed_count,
                "skipped": skipped_count,
                "partial": partial_count,
                "deferred": counts.get(JobStatus.DEFERRED.value, 0),
            }
            finalized += 1
        session.commit()
    return finalized
