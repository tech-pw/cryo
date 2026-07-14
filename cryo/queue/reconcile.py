from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast

from cryo.domain.enums import JobStatus
from cryo.queue.locks import LockStore
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.worker.worker import DestinationConfig, JobStore, MaintenanceJobRecord

logger = logging.getLogger(__name__)

_ACTIVE_QUEUE_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.ADMITTED,
    JobStatus.RUNNING,
}


def reconcile_queued_jobs(store: JobStore, queue: MaintenanceQueue) -> int:
    """Re-enqueue DB jobs stuck in queued state that are missing from Redis."""
    list_queued = getattr(store, "list_queued_jobs", None)
    destination_exists = getattr(store, "destination_exists", None)
    if not callable(list_queued):
        return 0

    re_enqueued = 0
    for job in list_queued():
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
        if queue_status in _ACTIVE_QUEUE_STATUSES:
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
    """Return deferred jobs to the queue when admission would admit them now."""
    from cryo.governor.admission import AdmissionConfig, AdmissionJob, decide_job_admission

    list_deferred = getattr(store, "list_deferred_jobs", None)
    if not callable(list_deferred):
        return 0

    current = now or datetime.now(UTC)
    requeued = 0
    for job in list_deferred():
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
) -> int:
    """Fail jobs stuck in RUNNING past `stale_after_seconds` and free their slot.

    A worker that dies mid-job (OOM, node eviction, rollout) never gets to call
    `store.mark_status(..., FAILED)` or `queue.complete(...)`, so the job's DB row stays
    RUNNING and its Redis in-flight/table-lock ownership is never released -- forever,
    since nothing else transitions a RUNNING job out of that state. Seen in production:
    5 jobs stuck RUNNING for hours (oldest ~20h) across two worker restarts, silently
    eating destination concurrency slots that never got reclaimed.
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
    )
