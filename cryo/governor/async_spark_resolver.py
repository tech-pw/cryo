"""Hand parked async Spark jobs back to the workers when their Spark Job ends (#435).

A worker with async dispatch on submits a Spark-on-K8s statement, parks the job in the
AsyncSparkRegistry and moves on. The job keeps its RUNNING status, its in-flight slot and
its table lock. This resolver, run by the governor leader each tick, is the "watched
separately" half, the same shape as the fallback sweep (fallback_sweep.py) and the native
watcher. It does not record outcomes. It only decides when the job has something to do and
puts it back on the admitted stream with ``requeue_for_continuation``; the worker that
claims it collects the result and records it through the blocking path's own code.

Bounded: one call reads at most ``limit`` due entries, oldest first. Idempotent: it runs
only on the governor leader, it skips a job already waiting on the stream or being resumed
(job-owner lease), and a hand-back is rescheduled ``resume_grace_seconds`` out, so a worker
that dies mid-resume gets the job handed back again rather than losing it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cryo.domain.enums import JobStatus
from cryo.engines.spark.async_state import SparkAsyncState
from cryo.queue.async_spark import AsyncSparkRegistry
from cryo.queue.locks import LockStore
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.worker.worker import JobStore

logger = logging.getLogger(__name__)

# Looks a Spark Job up by name; returns whether it has ended.
TerminalProbe = Callable[[str, str], bool]


@dataclass(frozen=True, slots=True)
class AsyncSparkResolveResult:
    examined: int = 0
    handed_back: int = 0
    still_running: int = 0
    dropped: int = 0
    lookup_errors: int = 0


def resolve_parked_spark_jobs(
    registry: AsyncSparkRegistry,
    queue: MaintenanceQueue,
    lock_store: LockStore,
    store: JobStore,
    *,
    is_terminal: TerminalProbe | None = None,
    now: float | None = None,
    limit: int = 200,
    poll_interval_seconds: float = 30.0,
    resume_grace_seconds: float = 600.0,
    lock_ttl_seconds: int = 6 * 60 * 60,
) -> AsyncSparkResolveResult:
    current = time.time() if now is None else now
    probe = is_terminal or _spark_k8s_terminal_probe(store)
    entries = registry.due(current, limit)
    if not entries:
        return AsyncSparkResolveResult()
    registry.mark_checked([entry.job_id for entry in entries], current)
    statuses = _job_statuses(store, [entry.job_id for entry in entries])
    handed_back = still_running = dropped = lookup_errors = 0
    for entry in entries:
        if statuses.get(entry.job_id) is not JobStatus.RUNNING:
            # Ended some other way (cancelled, reaped, deleted). Its claims went with it.
            registry.clear(entry.job_id)
            dropped += 1
            continue
        # The lock is held for the whole job, not released at submit.
        lock_store.renew_table_lock(entry.table_id, entry.job_id, lock_ttl_seconds)
        if queue.awaiting_claim(entry.job_id) or lock_store.get_job_owner(entry.job_id):
            registry.reschedule(entry.job_id, current + poll_interval_seconds)
            still_running += 1
            continue
        state = SparkAsyncState.from_dict(entry.spark)
        terminal = False
        if state.ref is not None:
            try:
                terminal = probe(entry.destination_id, state.ref)
            except Exception:
                logger.warning(
                    "Async Spark resolve: lookup failed for %s (job %s)",
                    state.ref,
                    entry.job_id,
                    exc_info=True,
                )
                registry.reschedule(entry.job_id, current + poll_interval_seconds)
                lookup_errors += 1
                continue
        if not state.ready_to_resume(now=current, run_terminal=terminal):
            registry.reschedule(
                entry.job_id,
                state.next_check_at(now=current, poll_interval=poll_interval_seconds),
            )
            still_running += 1
            continue
        registry.reschedule(entry.job_id, current + resume_grace_seconds)
        queue.requeue_for_continuation(entry.job_id)
        handed_back += 1
    return AsyncSparkResolveResult(
        examined=len(entries),
        handed_back=handed_back,
        still_running=still_running,
        dropped=dropped,
        lookup_errors=lookup_errors,
    )


def _job_statuses(store: JobStore, job_ids: list[str]) -> dict[str, JobStatus]:
    """One batched lookup where the store has it (the SQL store does)."""
    batched = getattr(store, "job_statuses", None)
    if callable(batched):
        return dict(batched(job_ids))
    statuses: dict[str, JobStatus] = {}
    for job_id in job_ids:
        try:
            statuses[job_id] = store.get_job(job_id).status
        except KeyError:
            continue
    return statuses


def _spark_k8s_terminal_probe(store: JobStore) -> TerminalProbe:
    """One Spark-on-K8s client per destination for the duration of one resolve call."""
    clients: dict[str, Any] = {}

    def probe(destination_id: str, ref: str) -> bool:
        from cryo.engines.spark.k8s import TERMINAL_STATES, SparkK8sClient, SparkK8sConfig

        client = clients.get(destination_id)
        if client is None:
            destination = store.get_destination(destination_id)
            client = SparkK8sClient(SparkK8sConfig.from_config(dict(destination.config)))
            clients[destination_id] = client
        return client.get_job_run(ref).state in TERMINAL_STATES

    return probe
