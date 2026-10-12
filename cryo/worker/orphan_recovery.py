"""Adopt orphaned RUNNING jobs after worker pod restarts.

Governor remains the sole admitter. This module only claims ownership of jobs
already in RUNNING (or stuck ADMITTED) and resolves write-ahead ``job_steps``.
"""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from cryo.db.repositories.job_steps import JobStepRecord
from cryo.domain.enums import EngineType, JobStatus, JobStepStatus, OperationType
from cryo.engines.trino.client import TrinoCoordinator, coordinators_from_config
from cryo.engines.trino.query_status import (
    EngineOpState,
    find_maintenance_query_state,
    get_query_state,
)
from cryo.queue.locks import LockStore
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.worker.safety_gates import DANGEROUS_OPS, TableActivity
from cryo.worker.worker import DestinationConfig, JobStore, MaintenanceJobRecord

logger = logging.getLogger(__name__)

DEFAULT_OWNER_TTL_SEC = 60

_ACTIVE_JOB_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.ADMITTED,
    JobStatus.RUNNING,
    JobStatus.DEFERRED,
}

# Confirmed live, 2026-09-10: recover_orphaned_jobs's fallback loop called
# store.list_queued_jobs() with no limit -- JobStore.list_queued_jobs's own docstring
# already documents this exact unbounded-scan pattern (28,445 queued jobs, 2026-09-02,
# fixed in queue/reconcile.py's reconcile_queued_jobs via this same _MAX_QUEUED_RECONCILE_
# PER_WAVE-shaped cap) but this call site was missed. WorkerRunner._tick() runs
# recover_orphaned_jobs on the base 5s worker poll interval, on every worker pod
# independently -- so an unbounded fetch here means every pod re-materializes the entire
# fleet-wide queued backlog (full MaintenanceJobRecord + eager-loaded run/preset) every 5
# seconds. Under today's cron burst (queue_growth vitals check: 3,134+ deep) this compounded
# across ~15 pods far faster than the 2026-09-02 incident's single-governor-pod case,
# OOMKilling 9 of 9 worker pods within roughly 90 minutes. QUEUED jobs were never actually
# adoptable orphans anyway (this module's own docstring: "already in RUNNING (or stuck
# ADMITTED)") -- capped instead of removed, since the loop below still needs to see *some*
# queued jobs to re-enqueue ones missing from Redis (see the ADMITTED/QUEUED loop below).
_MAX_QUEUED_SCAN_PER_TICK = 2000

# Confirmed live, 2026-09-10 (same outage as above): list_running_jobs() -- called
# unconditionally, every tick, with no limit -- was the actual dominant cost, not
# list_queued_jobs alone. RUNNING includes every engine's jobs, not just the Spark/Trino
# ones this recovery loop can act on (native-engine jobs go straight to RUNNING via their
# own dispatch_id and are filtered out later via _is_native_watcher_owned, but only *after*
# the full unbounded fetch already materialized them). Kernel-verified via /proc/1/smaps_
# rollup on a live pod: Anonymous (heap) memory climbed linearly ~1GB per 5s tick, ~140MB
# to 8GB in ~42s, immediately OOMKilled -- matches this function's own 5s poll interval
# exactly. list_admitted_jobs() capped for the same reason, smaller blast radius (ADMITTED
# is normally a much smaller, transient set) but same unbounded shape.
_MAX_RUNNING_SCAN_PER_TICK = 500
_MAX_ADMITTED_SCAN_PER_TICK = 500


class StepLedger(Protocol):
    def get_started_step(self, job_id: str) -> JobStepRecord | None: ...

    def has_started_step(self, job_id: str) -> bool: ...

    def complete_step(
        self,
        job_id: str,
        step_index: int,
        status: JobStepStatus | str,
        *,
        engine_ref: str | None = None,
        detail: str | None = None,
    ) -> JobStepRecord | None: ...

    def list_steps(self, job_id: str) -> list[JobStepRecord]: ...


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    examined: int = 0
    adopted: int = 0
    resolved_steps: int = 0
    skipped: int = 0
    waiting: int = 0


def default_worker_holder_id() -> str:
    return os.environ.get("HOSTNAME") or socket.gethostname() or "cryo-worker"


def probe_table_activity_for_destination(
    destination: DestinationConfig,
    table_ref: str | None,
    *,
    operation: OperationType | None = None,
    detail: str | None = None,
    engine_ref: str | None = None,
) -> TableActivity:
    """Map destination engine probes to TableActivity for safety gates."""
    state = probe_engine_op_state(
        destination,
        table_ref=table_ref,
        operation=operation,
        detail=detail,
        engine_ref=engine_ref,
    )
    if state is EngineOpState.RUNNING:
        return TableActivity.ACTIVE
    if state is EngineOpState.UNCERTAIN:
        return TableActivity.UNCERTAIN
    if state is EngineOpState.NOT_APPLICABLE:
        # Native watcher still owns the live job — treat as active for quiet gates.
        return TableActivity.ACTIVE
    return TableActivity.QUIET


def probe_engine_op_state(
    destination: DestinationConfig,
    table_ref: str | None,
    *,
    operation: OperationType | None = None,
    detail: str | None = None,
    engine_ref: str | None = None,
    job: MaintenanceJobRecord | None = None,
) -> EngineOpState:
    engine = (destination.engine or "").lower()
    if engine == EngineType.NATIVE.value or engine == "native":
        if job is not None and getattr(job, "dispatch_id", None):
            return EngineOpState.NOT_APPLICABLE
        if engine_ref:
            return EngineOpState.NOT_APPLICABLE
        # No dispatch_id and no engine_ref means nothing has ever been dispatched for this
        # job -- there is no prior/orphaned attempt to check for staleness, so this is
        # unambiguously quiet, not uncertain. Regression, 2026-07-28: this returned UNCERTAIN
        # unconditionally, which wait_for_table_quiet() never resolves out of (UNCERTAIN
        # always resets its consecutive-clean counter for DANGEROUS_OPS) -- every fresh job's
        # first dangerous op (always expire_snapshots, always first in the plan) held forever
        # at "Table activity uncertain before expire_snapshots; holding for quiet window",
        # confirmed live blocking every fresh Spark-K8s AND native job dispatched after this
        # safety gate was deployed. SUCCEEDED maps to TableActivity.QUIET below (the only
        # EngineOpState values that do); it's a slight misnomer here (nothing ran) but is the
        # existing state that carries the right "nothing to wait for" meaning.
        return EngineOpState.SUCCEEDED

    if engine in {EngineType.TRINO.value, "trino"}:
        coordinators = _trino_coordinators(destination.config)
        if engine_ref and coordinators:
            states = [get_query_state(c, engine_ref) for c in coordinators]
            if any(s is EngineOpState.RUNNING for s in states):
                return EngineOpState.RUNNING
            if any(s is EngineOpState.SUCCEEDED for s in states):
                return EngineOpState.SUCCEEDED
            if any(s is EngineOpState.FAILED for s in states):
                return EngineOpState.FAILED
        return find_maintenance_query_state(
            coordinators,
            table_ref=table_ref,
            operation=operation,
            detail=detail,
        )

    if engine in {EngineType.SPARK_EMR.value, "spark_emr", "spark"}:
        if not engine_ref:
            # Same reasoning as the native branch above: no engine_ref means nothing has
            # been dispatched for this job yet, so there is no prior Spark-on-K8s/EMR job to
            # check for staleness. Confirmed live, 2026-07-28: this returning UNCERTAIN
            # unconditionally blocked every fresh Spark job's first dangerous op forever.
            return EngineOpState.SUCCEEDED
        return _spark_job_state(destination, engine_ref)

    return EngineOpState.UNCERTAIN


def recover_orphaned_jobs(
    store: JobStore,
    queue: MaintenanceQueue,
    lock_store: LockStore,
    step_ledger: StepLedger,
    *,
    holder_id: str | None = None,
    lock_ttl_seconds: int = 21600,
    owner_ttl_seconds: int = DEFAULT_OWNER_TTL_SEC,
    table_ref_resolver: Callable[[MaintenanceJobRecord], str | None] | None = None,
) -> RecoveryResult:
    """Scan non-terminal jobs and adopt RUNNING orphans with a started step.

    Does not admit new work. Returns counts for logging/metrics.
    """
    holder = holder_id or default_worker_holder_id()
    list_running = getattr(store, "list_running_jobs", None)
    examined = adopted = resolved = skipped = waiting = 0

    jobs: list[MaintenanceJobRecord] = []
    if callable(list_running):
        try:
            jobs.extend(list_running(limit=_MAX_RUNNING_SCAN_PER_TICK))
        except TypeError:
            # Older/test JobStore stand-ins may not accept limit= -- fall back to the
            # unbounded call rather than crash the whole tick over a kwarg mismatch.
            jobs.extend(list_running())
    list_all_active = getattr(store, "list_active_jobs", None)
    if callable(list_all_active):
        jobs = list_all_active()
    else:
        for status_name, lister_name, lister_kwargs in (
            (JobStatus.ADMITTED, "list_admitted_jobs", {"limit": _MAX_ADMITTED_SCAN_PER_TICK}),
            (JobStatus.QUEUED, "list_queued_jobs", {"limit": _MAX_QUEUED_SCAN_PER_TICK}),
        ):
            lister = getattr(store, lister_name, None)
            if not callable(lister):
                continue
            try:
                results = lister(**lister_kwargs)
            except TypeError:
                # Older/test JobStore stand-ins may not accept limit= -- fall back to the
                # unbounded call rather than crash the whole tick over a kwarg mismatch.
                results = lister()
            for job in results:
                if job.status is status_name:
                    jobs.append(job)

    seen: set[str] = set()
    for job in jobs:
        if job.id in seen:
            continue
        seen.add(job.id)
        if job.status not in _ACTIVE_JOB_STATUSES:
            continue
        examined += 1

        if job.status is JobStatus.ADMITTED:
            if queue.status(job.id) is None:
                queue.enqueue(job.id, job.destination_id)
                admit = getattr(queue, "requeue_admitted", None)
                if callable(admit):
                    admit(job.id, job.destination_id, job.replica)
                skipped += 1
            continue

        if job.status is not JobStatus.RUNNING:
            continue

        started = step_ledger.get_started_step(job.id)

        destination = _try_destination(store, job.destination_id)
        if destination is None:
            skipped += 1
            continue

        table_ref: str | None = None
        if table_ref_resolver is not None:
            table_ref = table_ref_resolver(job)
        elif job.plan and isinstance(job.plan, dict):
            table_ref = job.plan.get("table_ref")

        if _is_native_watcher_owned(job, destination):
            skipped += 1
            continue

        if not lock_store.acquire_job_owner(job.id, holder, owner_ttl_seconds):
            skipped += 1
            continue
        adopted += 1

        if not lock_store.renew_table_lock(job.table_id, job.id, lock_ttl_seconds):
            lock_store.acquire_table_lock(job.table_id, job.id, lock_ttl_seconds)

        if started is None:
            lock_store.renew_job_owner(job.id, holder, owner_ttl_seconds)
            continue

        state = probe_engine_op_state(
            destination,
            table_ref=str(table_ref) if table_ref else None,
            operation=started.operation_type,
            detail=started.detail,
            engine_ref=started.engine_ref or job.engine_ref,
            job=job,
        )

        if state is EngineOpState.RUNNING or state is EngineOpState.NOT_APPLICABLE:
            waiting += 1
            lock_store.renew_job_owner(job.id, holder, owner_ttl_seconds)
            lock_store.renew_table_lock(job.table_id, job.id, lock_ttl_seconds)
            logger.info(
                "Adopted job %s step %s still %s; waiting",
                job.id,
                started.step_index,
                state.value,
            )
            continue

        if state is EngineOpState.UNCERTAIN:
            waiting += 1
            lock_store.renew_job_owner(job.id, holder, owner_ttl_seconds)
            lock_store.renew_table_lock(job.table_id, job.id, lock_ttl_seconds)
            if started.operation_type in DANGEROUS_OPS:
                logger.info(
                    "Adopted job %s dangerous step %s uncertain; not re-running",
                    job.id,
                    started.operation_type.value,
                )
            continue

        if state is EngineOpState.SUCCEEDED:
            step_ledger.complete_step(
                job.id,
                started.step_index,
                JobStepStatus.SUCCEEDED,
                engine_ref=started.engine_ref,
                detail=started.detail,
            )
            resolved += 1
            # Regression, 2026-07-29: resolving the interrupted step here isn't enough on its
            # own -- nothing else ever dispatches the job's remaining operations, so it used to
            # sit at this step forever (confirmed live: 35+ min of "still running; waiting"
            # after the underlying Spark job had already succeeded). Hand it back to the normal
            # claim_admitted() dispatch path; process_next()'s statement loop rebuilds the plan
            # fresh and skips anything already SUCCEEDED/SKIPPED in the ledger, so this either
            # continues to the next operation or -- if that was the last one -- finalizes the
            # job correctly.
            requeue = getattr(queue, "requeue_for_continuation", None)
            if callable(requeue):
                requeue(job.id)
            continue

        if state is not EngineOpState.FAILED:
            continue

        step_ledger.complete_step(
            job.id,
            started.step_index,
            JobStepStatus.FAILED,
            engine_ref=started.engine_ref,
            detail=started.detail,
        )
        store.mark_status(job.id, JobStatus.FAILED, error="engine_step_failed_on_recover")
        queue.complete(job.id, JobStatus.FAILED)
        lock_store.release_table_lock(job.table_id, job.id)
        lock_store.release_job_owner(job.id, holder)
        resolved += 1

    return RecoveryResult(
        examined=examined,
        adopted=adopted,
        resolved_steps=resolved,
        skipped=skipped,
        waiting=waiting,
    )


def job_has_recovery_lease_or_started_step(
    job_id: str,
    lock_store: LockStore,
    step_ledger: StepLedger | None,
) -> bool:
    if lock_store.get_job_owner(job_id):
        return True
    if step_ledger is not None and step_ledger.has_started_step(job_id):
        return True
    return False


def _trino_coordinators(config: Mapping[str, Any]) -> Sequence[TrinoCoordinator]:
    try:
        return coordinators_from_config(dict(config))
    except Exception:
        logger.debug("Unable to parse Trino coordinators", exc_info=True)
        return ()


def _spark_job_state(destination: DestinationConfig, engine_ref: str) -> EngineOpState:
    submit_mode = str(destination.config.get("submit_mode") or "").lower()
    try:
        if submit_mode == "k8s":
            # Regression, 2026-07-29: this used to import JobRunState from
            # cryo.engines.spark.emr while comparing it against the result of
            # SparkK8sClient.get_job_run(), which returns cryo.engines.spark.k8s.JobRunState --
            # a *different* enum class. `result.state is JobRunState.SUCCESS` compares object
            # identity across two unrelated StrEnum classes, which is never True even when both
            # members are literally named/valued "SUCCESS" -- so a genuinely completed Spark-K8s
            # job could never be detected as SUCCEEDED here, only ever falling through to
            # RUNNING. Confirmed live: an orphaned job whose Spark K8s Job had already reached
            # completionTime/succeeded=1 was still logged "still running; waiting" 40+ minutes
            # after the underlying rewrite committed, permanently stalling the job.
            from cryo.engines.spark.k8s import (
                TERMINAL_STATES,
                JobRunState,
                SparkK8sClient,
                SparkK8sConfig,
            )

            k8s_client = SparkK8sClient(SparkK8sConfig.from_config(destination.config))
            result = k8s_client.get_job_run(engine_ref)
            if result.state is JobRunState.SUCCESS:
                return EngineOpState.SUCCEEDED
            # Regression, confirmed live 2026-09-09: only SUCCESS/FAILED were checked here,
            # so JobRunState.UNKNOWN (get_job_run's own signal that the driver pod AND the
            # wrapper Job are both gone -- TTL-expired before this job's outcome was ever
            # resolved, see _get_wrapper_job_run's docstring) silently fell through to
            # RUNNING. An adopted job whose real Spark Job had been garbage-collected days
            # earlier stayed in the "still running; waiting" loop forever: every worker
            # restart re-adopted it, renewed the lease, and waited on an outcome that can
            # never arrive -- one repeatedly OOM-killed worker held the lease for 4+ days
            # across 16+ crash-restart cycles without ever releasing it or letting the
            # reaper (reconcile_stale_running_jobs) take over, because an active adopt lease
            # makes that reaper defer to orphan recovery. UNKNOWN is genuinely unrecoverable,
            # not merely uncertain -- match the EMR branch below's TERMINAL_STATES pattern:
            # anything terminal that isn't SUCCESS resolves as FAILED instead of waiting.
            if result.state in TERMINAL_STATES:
                return EngineOpState.FAILED
            return EngineOpState.RUNNING

        from cryo.engines.spark.emr import (
            SUCCESS_STATES,
            EMRServerlessClient,
            EMRServerlessConfig,
        )
        from cryo.engines.spark.emr import (
            TERMINAL_STATES as EMR_TERMINAL_STATES,
        )

        emr_client = EMRServerlessClient(EMRServerlessConfig.from_config(destination.config))
        emr_result = emr_client.get_job_run(engine_ref)
        if emr_result.state in SUCCESS_STATES:
            return EngineOpState.SUCCEEDED
        if emr_result.state in EMR_TERMINAL_STATES:
            return EngineOpState.FAILED
        return EngineOpState.RUNNING
    except Exception:
        logger.debug("Spark job probe failed for %s", engine_ref, exc_info=True)
        return EngineOpState.UNCERTAIN


def _try_destination(store: JobStore, destination_id: str) -> DestinationConfig | None:
    getter = getattr(store, "try_get_destination", None)
    if callable(getter):
        return cast(DestinationConfig | None, getter(destination_id))
    try:
        return store.get_destination(destination_id)
    except KeyError:
        return None


def _is_native_watcher_owned(job: MaintenanceJobRecord, destination: DestinationConfig) -> bool:
    engine = (destination.engine or "").lower()
    if engine not in {EngineType.NATIVE.value, "native"}:
        return False
    return bool(job.engine_ref) or bool(getattr(job, "dispatch_id", None))
