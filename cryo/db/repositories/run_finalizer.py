from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import MaintenanceJob, MaintenanceRun
from cryo.domain.enums import JobStatus, is_capacity_defer_reason

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

# Real incident, 2026-09-15/16: the 10-minute grace above assumes deferrals resolve "within a
# wave or two" -- true for the job-specific reasons it was built for (cooldown, table lock,
# per-job guardrails), but false for fleet-wide *capacity* gates. max_concurrent_jobs_per_
# metastore (native_admit.py's _MetastoreHeadroom) stayed over its 150-job cap for multiple
# HOURS during a metastore-congestion incident -- every native-engine job deferred with
# "metastore_capacity:..." kept re-deferring wave after wave, exactly as designed, but after
# 10 minutes finalize_open_runs closed their parent runs out from under them anyway, and
# reconcile_deferred_jobs then fast-failed all of them as "orphaned_deferred" the moment it
# next looked -- 29,832 jobs failed this way in 24h, ~99% of that day's total failures, while
# the underlying capacity constraint was still real and would have cleared on its own given
# more time. A job deferred for a genuinely fleet-wide capacity reason gets this much longer
# grace before its run is allowed to finalize out from under it -- the failure mode of waiting
# too long (a run stays "running" a bit longer than ideal) is far cheaper than the failure mode
# of giving up too soon (thousands of legitimately-recoverable jobs permanently failed).
_CAPACITY_DEFERRED_FINALIZE_GRACE = timedelta(hours=6)

# Root-cause redesign, 2026-09-16: this used to be a locally-declared prefix tuple, duplicated
# by hand from native_admit.py's own reason strings -- exactly the "two systems designed
# independently, agreeing only by string convention" gap that let the two disagree in the first
# place. Now imported from cryo.domain.enums, the one shared classification both the admission
# side (native_admit.py, the reason strings' actual source) and this module (the sole consumer)
# rely on. See is_capacity_defer_reason's docstring for the fuller incident history.


def finalize_open_runs(
    session_factory: Callable[[], Session], *, now: datetime | None = None
) -> int:
    """Mark runs finished when all child jobs reached a terminal state.

    A run with only DEFERRED jobs left is not finalized until each of those jobs has been
    deferred for at least _DEFERRED_FINALIZE_GRACE -- see that constant's docstring for the
    race this avoids. Jobs deferred for a fleet-wide capacity reason (metastore_capacity,
    native_cluster_capacity, fleet_admission_rate_cap) get the much longer
    _CAPACITY_DEFERRED_FINALIZE_GRACE instead -- see that constant's docstring for the 2026-09-15
    incident this fixes (29,832 jobs mass-failed as "orphaned_deferred" while the capacity
    constraint that deferred them was still genuinely active, not abandoned).
    """
    finalized = 0
    current = now or datetime.now(UTC)
    with session_factory() as session:
        # Recovery plan §4, 2026-09-20: `status != "cancelled"` re-selected every already-
        # terminal run (succeeded/failed/partial) on every tick forever, not just genuinely open
        # ones -- each one recomputed its full job-status breakdown for nothing, and (the real
        # bug, not just wasted work) could still fall through to the `run.finished_at =
        # datetime.now(UTC)` assignment below and silently overwrite an already-recorded
        # completion time with the current tick's timestamp, corrupting run duration history.
        # "running" is this table's actual open-run marker (see the column's server_default and
        # the `if run.status != "running": run.status = "running"` still-in-grace branches
        # below, which only ever move a run *into* "running", never a terminal run back out of
        # it) -- restrict the query to it so a terminal run is never reconsidered at all.
        #
        # Review finding F09, 2026-09-20: restricting to status=="running" and null-guarding
        # finished_at (both above) close the *sequential re-entry* case (a run genuinely already
        # terminal from a prior tick), but do nothing about two finalize_open_runs invocations
        # (two governor replicas, or overlapping ticks) racing the SAME still-open run
        # concurrently -- both could load it with status="running"/finished_at=None before
        # either commits, and Postgres's default read-committed isolation does not itself
        # prevent that. FOR UPDATE SKIP LOCKED makes this finalizer instance's own selection
        # exclusive: a run row already locked by a concurrent finalize_open_runs call for this
        # same tick is skipped entirely rather than raced -- the other instance will finish
        # deciding it (or not) on its own, but never both.
        runs = session.scalars(
            select(MaintenanceRun)
            .where(MaintenanceRun.status == "running")
            .with_for_update(skip_locked=True)
        )
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
                deferred_jobs = session.execute(
                    select(MaintenanceJob.finished_at, MaintenanceJob.error).where(
                        MaintenanceJob.run_id == run.id,
                        MaintenanceJob.status == JobStatus.DEFERRED.value,
                    )
                ).all()
                still_in_grace = any(
                    finished_at is not None
                    and (finished_at if finished_at.tzinfo else finished_at.replace(tzinfo=UTC))
                    > current
                    - (
                        _CAPACITY_DEFERRED_FINALIZE_GRACE
                        if is_capacity_defer_reason(error)
                        else _DEFERRED_FINALIZE_GRACE
                    )
                    for finished_at, error in deferred_jobs
                )
                if still_in_grace:
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
            # Immutable once set (recovery plan §4): even though the query above already
            # excludes non-"running" runs, guard finished_at directly too -- a second
            # finalize_open_runs invocation racing this one (two governor replicas, or a retry
            # after a partial commit) must never get a chance to stamp a later timestamp over
            # the run's real completion time.
            if run.finished_at is None:
                run.finished_at = current
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
