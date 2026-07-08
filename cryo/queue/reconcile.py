from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast

from cryo.domain.enums import JobStatus
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


def purge_stale_queue_entries(store: JobStore, queue: MaintenanceQueue) -> int:
    """Remove Redis pending entries that reference missing jobs or destinations."""
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
                store.get_job(job_id)
            except KeyError:
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
        offpeak_end_ist=int(config.get("offpeak_end_ist", 7)),
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
