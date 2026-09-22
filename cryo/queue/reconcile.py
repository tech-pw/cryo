from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

from cryo.config import get_settings
from cryo.domain.enums import JobStatus
from cryo.queue.locks import LockStore
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.scheduler.scheduler import fifo_priority
from cryo.worker.worker import DestinationConfig, JobStore, MaintenanceJobRecord

logger = logging.getLogger(__name__)

_ACTIVE_QUEUE_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.ADMITTED,
    JobStatus.RUNNING,
}

# Live-verified 2026-09-03: a job whose parent MaintenanceRun already reached one of these
# terminal statuses (finalize_open_runs already decided the run is done, independent of this
# specific job) can never legitimately un-defer -- its run's deadline_at is permanently in the
# past, and nothing about "trying again later" changes that. Excludes 'running'/'scheduled'
# (the run may still genuinely need this job) and 'deferred' is not a MaintenanceRun status at
# all (only MaintenanceJob has it). Matches the manual live cleanup that unblocked the
# governor after this bug's discovery: 31,902 of 40,785 deferred jobs belonged to already-
# terminal runs, permanently occupying reconcile_deferred_jobs's oldest-first bounded window
# (see MaintenanceJobRecord.run_status's own docstring for the full incident).
_TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "skipped", "partial", "cancelled"})

# Live-verified 2026-09-02: reconcile_deferred_jobs previously called list_deferred_jobs()
# unbounded on every governor wave (every 30s) -- with 4,227 deferred jobs and growing, each
# wave's reconcile step took long enough to stall the governor entirely (admitted-per-wave
# declined 15->8->7->5->5->5->2->2 across consecutive waves, then no wave completed at all
# for 3+ minutes against the 30s interval; a pod restart did not help, confirming the
# backlog's own size -- not pod state -- was the bottleneck). Matches
# _MAX_DISPATCHES_PER_TICK's own bounding pattern (native/watcher.py) -- a wave's own runtime
# must stay bounded regardless of how large the deferred backlog grows; the remainder is
# picked up on the next wave 30s later, oldest-first (see list_deferred_jobs's limit param).
_MAX_DEFERRED_RECONCILE_PER_WAVE = 500

# Same incident, same day: fixing reconcile_deferred_jobs alone was not enough -- a governor
# pod restarted onto that fix still failed to complete a single wave for 2.5+ minutes, because
# reconcile_queued_jobs (below) has the identical unbounded-scan pattern and, at 28,445 queued
# jobs (far exceeding the 4,227 deferred that first surfaced this bug class), was the larger
# bottleneck once the deferred side was bounded. Larger cap than the deferred one -- re-
# enqueuing a queued-but-Redis-missing job is cheap (no admission decision, just a queue
# write), so more can be processed per wave within the same time budget.
_MAX_QUEUED_RECONCILE_PER_WAVE = 2000


def reconcile_queued_jobs(store: JobStore, queue: MaintenanceQueue) -> int:
    """Re-enqueue DB jobs stuck in queued state that are missing from Redis.

    2026-09-07 fix: previously used queue.status(job.id) (the job hash's own `status`
    field) to decide whether a QUEUED-in-DB job was "already active" in Redis and could be
    skipped. That field can desync from the pending zset it's meant to describe -- nothing
    in cancel_pending()/purge_stale_queue_entries() updates the hash back off "queued" when
    an entry is removed from the zset, so a job can carry status()=='queued' forever while
    genuinely absent from the only structure the governor actually reads
    (pending_by_destination(), which iterates the zset, not the hash). Confirmed live:
    3,046 jobs across 2 runs sat in exactly this state for 21+ hours -- this reconcile
    function ran every wave, saw status()=='queued', and skipped them every single time,
    while the destination itself kept dispatching other runs' work the whole time (so
    nothing about the destination, admission budget, or fairness ordering was actually
    stuck -- only these specific jobs were invisible). Now checks queue.is_pending(), which
    asks the zset directly, for the QUEUED case specifically; ADMITTED/RUNNING are still
    trusted from the hash since those statuses correctly mean "not in the pending zset by
    design", not desync.
    """
    list_queued = getattr(store, "list_queued_jobs", None)
    destination_exists = getattr(store, "destination_exists", None)
    if not callable(list_queued):
        return 0

    re_enqueued = 0
    for job in list_queued(limit=_MAX_QUEUED_RECONCILE_PER_WAVE):
        queue_status = queue.status(job.id)
        if callable(destination_exists) and not destination_exists(job.destination_id):
            if queue_status in _ACTIVE_QUEUE_STATUSES:
                queue.cancel_pending(job.id, job.destination_id)
            store.mark_status(job.id, JobStatus.FAILED, error="destination_not_found")
            logger.warning(
                "Marked queued job %s failed: destination %s missing or disabled",
                job.id,
                job.destination_id,
            )
            continue
        if queue_status in (JobStatus.ADMITTED, JobStatus.RUNNING):
            continue
        if queue_status is JobStatus.QUEUED and queue.is_pending(job.id, job.destination_id):
            continue
        queue.enqueue(job.id, job.destination_id)
        re_enqueued += 1
        logger.info("Re-enqueued queued job %s for destination %s", job.id, job.destination_id)

    if re_enqueued:
        logger.info("Re-enqueued %d queued job(s) missing from Redis", re_enqueued)
    return re_enqueued


def reconcile_deferred_jobs(
    store: JobStore,
    queue: MaintenanceQueue,
    *,
    now: datetime | None = None,
) -> int:
    """Return deferred jobs to the queue when admission would admit them now.

    Also fails a deferred job outright if its parent run already reached a terminal status --
    see _TERMINAL_RUN_STATUSES's own docstring for the live incident this fixes: a job whose
    run is already done can never legitimately un-defer (its deadline is permanently past),
    but re-deferring it doesn't refresh finished_at, so it stayed the *oldest* deferred job
    forever and, combined with list_deferred_jobs's oldest-first bounded ordering, monopolized
    every wave's entire reconcile budget instead of ever reaching genuinely-reconsiderable
    jobs behind it.
    """
    from cryo.governor.admission import AdmissionConfig, AdmissionJob, decide_job_admission

    list_deferred = getattr(store, "list_deferred_jobs", None)
    if not callable(list_deferred):
        return 0

    current = now or datetime.now(UTC)
    requeued = 0
    failed_orphaned = 0
    for job in list_deferred(limit=_MAX_DEFERRED_RECONCILE_PER_WAVE):
        if job.run_status in _TERMINAL_RUN_STATUSES:
            store.mark_status(job.id, JobStatus.FAILED, error="orphaned_deferred: parent run already finalized")
            failed_orphaned += 1
            continue
        destination = _try_get_destination(store, job.destination_id)
        if destination is None:
            store.mark_status(job.id, JobStatus.FAILED, error="destination_not_found")
            continue
        config = cast(AdmissionConfig, _admission_config(destination, AdmissionConfig))
        admission_job = cast(AdmissionJob, _admission_job(job, AdmissionJob))
        decision = decide_job_admission(admission_job, config, now=current)
        if not decision.admissible and decision.defer:
            continue
        if queue.status(job.id) in _ACTIVE_QUEUE_STATUSES:
            store.mark_status(job.id, JobStatus.QUEUED)
            requeued += 1
            continue
        queue.enqueue(job.id, job.destination_id)
        store.mark_status(job.id, JobStatus.QUEUED)
        requeued += 1
        logger.info("Re-queued deferred job %s (was %s)", job.id, job.error)

    if failed_orphaned:
        logger.warning(
            "Failed %d deferred job(s) whose parent run already finalized", failed_orphaned
        )
    if requeued:
        logger.info("Re-queued %d deferred job(s)", requeued)
    return requeued


def reconcile_stale_running_jobs(
    store: JobStore,
    queue: MaintenanceQueue,
    lock_store: LockStore,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 6 * 60 * 60,
    step_ledger: Any | None = None,
    engine_activity_checker: Callable[[MaintenanceJobRecord], bool] | None = None,
) -> int:
    """Fail jobs stuck in RUNNING past `stale_after_seconds` and free their slot.

    A worker that dies mid-job (OOM, node eviction, rollout) never gets to call
    `store.mark_status(..., FAILED)` or `queue.complete(...)`, so the job's DB row stays
    RUNNING and its Redis in-flight/table-lock ownership is never released -- forever,
    since nothing else transitions a RUNNING job out of that state. Seen in production:
    5 jobs stuck RUNNING for hours (oldest ~20h) across two worker restarts, silently
    eating destination concurrency slots that never got reclaimed.

    Skips jobs that still have an adopt lease, an open write-ahead step, or live engine
    maintenance activity — those are owned by orphan recovery, not this reaper.
    """
    list_running = getattr(store, "list_running_jobs", None)
    if not callable(list_running):
        return 0

    current = now or datetime.now(UTC)
    reaped = 0
    for job in list_running():
        if job.started_at is None:
            continue
        started_at = job.started_at if job.started_at.tzinfo else job.started_at.replace(tzinfo=UTC)
        if (current - started_at).total_seconds() <= stale_after_seconds:
            continue
        get_owner = getattr(lock_store, "get_job_owner", None)
        if callable(get_owner) and get_owner(job.id):
            logger.debug("Skipping stale reap for job %s: adopt lease held", job.id)
            continue
        if step_ledger is not None:
            try:
                has_started_step = step_ledger.has_started_step(job.id)
            except Exception:
                # A single job's step-ledger lookup failing (DB hiccup, malformed id) must not
                # abort the reap sweep for every other stale job in this wave -- reaping when
                # we're not actually sure is the unsafe direction (it force-fails a job that
                # might still be legitimately running), so treat "couldn't check" the same as
                # "found an open step": skip reaping, matching the other checks' conservative
                # default in this loop.
                logger.warning(
                    "Skipping stale reap for job %s: step ledger check failed",
                    job.id,
                    exc_info=True,
                )
                continue
            if has_started_step:
                logger.debug("Skipping stale reap for job %s: started step open", job.id)
                continue
        if callable(engine_activity_checker) and engine_activity_checker(job):
            logger.debug("Skipping stale reap for job %s: engine still active", job.id)
            continue
        store.mark_status(job.id, JobStatus.FAILED, error="stale_running_reaped")
        queue.complete(job.id, JobStatus.FAILED)
        lock_store.release_table_lock(job.table_id, job.id)
        reaped += 1
        logger.warning(
            "Reaped stale RUNNING job %s (table %s): running since %s with no owning worker",
            job.id,
            job.table_id,
            started_at.isoformat(),
        )

    if reaped:
        logger.warning("Reaped %d stale RUNNING job(s)", reaped)
    return reaped


def reconcile_stuck_admitted_jobs(
    store: JobStore,
    queue: MaintenanceQueue,
    lock_store: LockStore,
    *,
    now: datetime | None = None,
    stuck_after_seconds: int | None = None,
) -> int:
    """Return ADMITTED jobs to QUEUED when the queue has no record of them at all.

    Since admitted jobs travel over a Redis Streams consumer group, a worker dying between
    claiming a job and starting it is handled by the queue itself: the entry stays in the
    group's pending list and another worker reclaims it. What this pass still covers is the
    one case the queue cannot repair from the inside -- the entry being gone outright, as
    after a Redis restart without persistence. The DB row says ADMITTED, the in-flight slot
    is still held, and nothing in Redis will ever produce that job again.

    `queue.awaiting_claim()` is an exact test now rather than a heuristic, so there is no
    guessing left here. Before Streams this pass had to infer loss from age plus a missing
    engine_ref, which also describes a job merely queued behind more work than the fleet can
    drain -- and failing those was the single largest source of recorded failures in the
    fleet: live 2026-09-21, 5,952 of one run's 6,117 jobs were failed as
    `stuck_admitted_no_dispatch` while workers were healthy and still working through the very
    same list. The age window and engine_ref check are kept only to stay clear of jobs
    mid-dispatch, whose stream entry is legitimately already acknowledged.
    """
    list_admitted = getattr(store, "list_admitted_jobs", None)
    awaiting_claim = getattr(queue, "awaiting_claim", None)
    return_to_pending = getattr(queue, "return_to_pending", None)
    if not callable(list_admitted) or not callable(awaiting_claim):
        return 0
    if not callable(return_to_pending):
        return 0

    current = now or datetime.now(UTC)
    if stuck_after_seconds is None:
        stuck_after_seconds = get_settings().stuck_admitted_after_seconds
    requeued = 0
    for job in list_admitted():
        if job.engine_ref:
            continue
        admitted_at = job.admitted_at
        if admitted_at is None:
            continue
        admitted_at = admitted_at if admitted_at.tzinfo else admitted_at.replace(tzinfo=UTC)
        if (current - admitted_at).total_seconds() <= stuck_after_seconds:
            continue
        if awaiting_claim(job.id):
            continue

        store.mark_status(job.id, JobStatus.QUEUED)
        return_to_pending(job.id, job.destination_id, fifo_priority(admitted_at))
        lock_store.release_table_lock(job.table_id, job.id)
        requeued += 1
        logger.warning(
            "Requeued job %s (table %s, destination %s): admitted %s but the queue has no "
            "record of it",
            job.id,
            job.table_id,
            job.destination_id,
            admitted_at.isoformat(),
        )

    if requeued:
        logger.warning("Requeued %d admitted job(s) the queue had lost", requeued)
    return requeued


def purge_stale_queue_entries(store: JobStore, queue: MaintenanceQueue) -> int:
    """Remove Redis pending entries that reference missing jobs, missing destinations,
    or jobs whose DB status has already gone terminal.

    The DB row for a job can reach a terminal status (skipped/cancelled/failed/succeeded)
    without ever calling `queue.cancel_pending()` or `queue.complete()` -- e.g. a direct
    status update outside the normal admit/complete path. `store.get_job(job_id)` still
    succeeds for those rows, so without this check they survive every wave forever,
    forcing `pending_by_destination()` to keep re-fetching and re-evaluating them ahead of
    genuinely queued work. Seen in production: a bulk cancellation issued as a direct SQL
    UPDATE left ~22k terminal jobs in the Redis pending zset, drowning out 27 real ones.
    """
    destination_exists = getattr(store, "destination_exists", None)
    purged = 0

    for destination_id, job_ids in queue.pending_by_destination().items():
        if callable(destination_exists) and not destination_exists(destination_id):
            for job_id in job_ids:
                queue.cancel_pending(job_id, destination_id)
                purged += 1
            continue

        for job_id in job_ids:
            if not _is_valid_job_id(job_id):
                queue.cancel_pending(job_id, destination_id)
                purged += 1
                continue
            try:
                job = store.get_job(job_id)
            except KeyError:
                queue.cancel_pending(job_id, destination_id)
                purged += 1
                continue
            if job.status not in _ACTIVE_QUEUE_STATUSES and job.status != JobStatus.DEFERRED:
                queue.cancel_pending(job_id, destination_id)
                purged += 1

    if purged:
        logger.warning("Purged %d stale queue entr(ies) from Redis", purged)
    return purged


def _is_valid_job_id(job_id: str) -> bool:
    return bool(job_id) and job_id.lower() != "none"


def _try_get_destination(store: JobStore, destination_id: str) -> DestinationConfig | None:
    getter = getattr(store, "try_get_destination", None)
    if callable(getter):
        return cast(DestinationConfig | None, getter(destination_id))
    try:
        return store.get_destination(destination_id)
    except KeyError:
        return None


def _admission_config(destination: DestinationConfig, admission_config: type[Any]) -> object:
    config = destination.config
    per_replica_cap = config.get("per_replica_cap", config.get("slot_cap", 5))
    return admission_config(
        offpeak_start_ist=int(config.get("offpeak_start_ist", 0)),
        offpeak_end_ist=int(config.get("offpeak_end_ist", 0)),
        per_replica_cap=int(per_replica_cap),
        wave_wait_sec=int(config.get("wave_wait_sec", 300)),
        adaptive_budget=bool(config.get("adaptive_budget", True)),
        high_load_ratio=float(config.get("high_load_ratio", 0.8)),
        high_load_fraction=float(config.get("high_load_fraction", 0.5)),
    )


def _admission_job(job: MaintenanceJobRecord, admission_job: type[Any]) -> object:
    return admission_job(
        job_id=job.id,
        table_id=job.table_id,
        destination_id=job.destination_id,
        mode=job.mode,
        cooldown_hours=job.cooldown_hours,
        last_maintained_at=job.last_maintained_at,
        deadline_at=job.deadline_at,
        force_admit=job.force_admit,
    )
