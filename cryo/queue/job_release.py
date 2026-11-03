"""Release what a job held once it has reached its final status."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cryo.domain.enums import JobStatus

if TYPE_CHECKING:
    from cryo.queue.locks import LockStore
    from cryo.queue.redis_queue import MaintenanceQueue


def release_job_claims(
    queue: MaintenanceQueue | None,
    lock_store: LockStore | None,
    *,
    job_id: str,
    table_id: str,
    status: JobStatus,
) -> None:
    """Return the job's in-flight slot and its table lock.

    Safe to call more than once and from more than one path: ``complete`` only returns a
    slot the queue still shows as held, and the lock release is compare-and-delete on the
    job id, so it never frees a lock another job has since taken on the same table.
    """
    if queue is not None:
        queue.complete(job_id, status)
    if lock_store is not None:
        lock_store.release_table_lock(table_id, job_id)
