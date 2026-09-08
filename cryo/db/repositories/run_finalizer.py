from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

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

# Live-confirmed 2026-09-08: most DEFERRED reasons are transient and actively expected to
# resolve within a wave or two -- approval-pending (once ensure_pending_approval_requests
# surfaces it and an operator acts), fleet-budget packing losing to a bigger job this wave,
# table-lock contention, QPP p95 refusal, "no_replica_slots". reconcile_deferred_jobs
# (cryo/queue/reconcile.py) re-examines every DEFERRED job every wave (~30s) specifically to
# catch these and re-queue them. But finalize_open_runs runs *first* in the same run_wave tick
# (governor.py) and, with DEFERRED in TERMINAL above, could close a run out the very next wave
# after its last job went DEFERRED -- before reconcile_deferred_jobs ever got a real chance to
# un-defer it. When it later did clear, reconcile_deferred_jobs found the parent run already
# "partial"/finished and fast-failed the job as "orphaned_deferred: parent run already
# finalized" (a76f9fc) -- 309 jobs in 48h on one destination alone, all collateral damage from
# this race, not genuinely-abandoned work. A DEFERRED job only counts toward "run is done" once
# it has sat deferred for at least this long, giving reconcile_deferred_jobs several real
# chances to un-defer it first.
_DEFERRED_FINALIZE_GRACE = timedelta(minutes=10)


def finalize_open_runs(session_factory: Callable[[], Session], *, now: datetime | None = None) -> int:
    """Mark runs finished when all child jobs reached a terminal state.

    A run with only DEFERRED jobs left is not finalized until each of those jobs has been
    deferred for at least _DEFERRED_FINALIZE_GRACE -- see that constant's docstring for the
    race this avoids.
    """
    finalized = 0
    current = now or datetime.now(UTC)
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
            if counts.get(JobStatus.DEFERRED.value, 0) > 0:
                recently_deferred = session.execute(
                    select(func.count())
                    .select_from(MaintenanceJob)
                    .where(
                        MaintenanceJob.run_id == run.id,
                        MaintenanceJob.status == JobStatus.DEFERRED.value,
                        MaintenanceJob.finished_at > current - _DEFERRED_FINALIZE_GRACE,
                    )
                ).scalar_one()
                if recently_deferred:
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
