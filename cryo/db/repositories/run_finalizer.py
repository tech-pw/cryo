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
    # DEFERRED means the governor chose not to admit this job this cycle (capacity/cooldown/
    # guardrail reasons, see governor.py's _defer_native_job) -- it does not requeue itself or
    # transition back to QUEUED under the same job row, so a job sitting at DEFERRED will never
    # become non-DEFERRED on its own. Treating it as non-terminal here left every run with any
    # deferred jobs stuck re-affirming status="running" forever: confirmed live 2026-08-24, 20
    # runs stuck open up to 321h (13+ days) despite 100% of their jobs already being in a
    # terminal state -- e.g. one run had 9,236 total jobs, all accounted for across
    # succeeded/failed/deferred, zero actually in flight.
    JobStatus.DEFERRED.value,
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
            deferred_count = counts.get(JobStatus.DEFERRED.value, 0)
            if failed_count == 0 and partial_count == 0 and deferred_count == 0:
                run.status = "succeeded"
            elif succeeded_count > 0 or partial_count > 0 or deferred_count > 0:
                # A deferred job means the governor chose not to admit it this cycle
                # (capacity/cooldown/guardrails) -- not a failure of anything that ran, but also
                # not the full intended scope, so "succeeded" would overstate it. "partial"
                # covers both: some real failures alongside successes, and admitted-but-
                # incomplete-scope runs with nothing but successes and deferrals.
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
