"""Watch native K8s Jobs created on Governor admit and finalize member jobs."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.repositories.native_dispatch import (
    jobs_for_dispatch,
    list_active_dispatches,
    mark_dispatch_finished,
)
from cryo.domain.enums import EngineType, JobStatus, OperationType
from cryo.engines.native.batch import (
    TABLE_RESULT_PREFIX,
    TRANSIENT_METASTORE_ERROR_SUBSTRING,
    is_transient_metastore_error,
)
from cryo.engines.native.job_dispatch import (
    _get_job_pods,
    _read_pod_logs,
    _safe_load_k8s,
)
from cryo.engines.native.job_state import current_k8s_namespace
from cryo.engines.native.optimize_properties import maybe_persist_optimize_properties
from cryo.engines.native.runner import PROGRESS_PREFIX, split_stdout_lines
from cryo.queue.locks import LockStore
from cryo.queue.run_events import MAX_STREAM_LEN, build_log_line, safe_publish
from cryo.worker.worker import AuditRecord

logger = logging.getLogger(__name__)

# Lines already relayed to run_events per dispatch, so a fresh NativeJobWatcher instance
# each tick (see cli.py._tick_native_watcher) doesn't re-publish the same log lines every
# poll. Module-level rather than instance state precisely because the instance is
# short-lived; keyed by dispatch id (str), cleared on finalize. Reset on process restart --
# acceptable, since the worst case is a handful of already-seen lines replayed once, not an
# unbounded loop.
_INCREMENTAL_LOG_SEEN: dict[str, set[str]] = {}
# How far back to read on each tick's incremental log fetch. Must comfortably exceed the
# Governor tick interval so a slow tick doesn't miss lines that landed between polls.
_INCREMENTAL_LOG_WINDOW_SECONDS = 120


@dataclass(frozen=True)
class WatchResult:
    finalized: int = 0
    still_running: int = 0
    errors: int = 0


#  Confirmed live, 2026-08-03: with a single destination's native dispatch backlog in the
# hundreds, list_active_dispatches(session) returning everything meant tick() made one
# read_namespaced_job K8s API call per dispatch, every 30s -- real K8s Jobs were completing
# in 6-12s each, but their DB row stayed status='running' for 20+ minutes because the
# watcher couldn't finalize them fast enough to keep pace with how many were in flight.
# Same root-cause shape as the admission-scan bug fixed earlier the same day (unbounded
# per-tick work scaling with queue/backlog depth), just in the completion path instead of
# admission. Bounded here rather than left unbounded like admission's `replicas` budget,
# since there's no natural per-tick capacity signal for "how many dispatches can finish
# checking within one tick" -- this is a conservative fixed ceiling, tunable if real
# tick duration under load says otherwise.
_MAX_DISPATCHES_PER_TICK = 200

# How many times a single table may be deferred (not terminally failed) for a transient
# metastore-connect timeout within the lookback window below, before the next occurrence is
# treated as a real, terminal failure instead. Bounds the retry loop -- a table whose every
# attempt keeps timing out (a genuinely unreachable/misconfigured source, not a transient
# blip) must still surface as `failed` eventually rather than cycle through
# queued->deferred->queued forever via reconcile_deferred_jobs' automatic requeue
# (cryo/queue/reconcile.py, called every governor wave).
_METASTORE_RETRY_CAP = 3
_METASTORE_RETRY_LOOKBACK_HOURS = 24


def _is_transient_metastore_error(error: str) -> bool:
    return is_transient_metastore_error(error)


def _under_retry_cap(session: Session, *, table_id: uuid.UUID) -> bool:
    """Found live, 2026-08-31: 490 distinct tables hit this exact bounded-timeout error in a
    single 24h window, each terminally failed on the very first occurrence -- a real metastore
    blip (the same underlying flakiness the 2026-08-21/28 timeout fixes were built around, still
    ongoing) cost each of those tables a full dispatch slot for zero benefit, with no retry until
    whatever cron/manual trigger happened to pick it up again, sometimes 24h later. Counts this
    table's own recent transient-metastore failures (both DEFERRED -- already-retried -- and
    FAILED, so a table that exhausted its cap once doesn't reset the count on its next natural
    failure) to decide whether one more deferral is still worth it."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from cryo.db.models import MaintenanceJob

    since = datetime.now(UTC) - timedelta(hours=_METASTORE_RETRY_LOOKBACK_HOURS)
    count = session.scalar(
        select(func.count())
        .select_from(MaintenanceJob)
        .where(
            MaintenanceJob.table_id == table_id,
            MaintenanceJob.status.in_((JobStatus.DEFERRED.value, JobStatus.FAILED.value)),
            MaintenanceJob.finished_at >= since,
            MaintenanceJob.error.like(f"%{TRANSIENT_METASTORE_ERROR_SUBSTRING}%"),
        )
    )
    return (count or 0) < _METASTORE_RETRY_CAP


class NativeJobWatcher:
    """Poll active native_dispatches and finalize MaintenanceJobs on Job completion."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        lock_store: LockStore,
        *,
        mark_status: Callable[..., None],
        record_audit: Callable[[AuditRecord], None] | None = None,
        namespace: str | None = None,
        lock_ttl_seconds: int = 6 * 60 * 60,
        run_events: Any | None = None,
        max_dispatches_per_tick: int = _MAX_DISPATCHES_PER_TICK,
    ) -> None:
        self._session_factory = session_factory
        self._lock_store = lock_store
        self._mark_status = mark_status
        self._record_audit = record_audit
        self._namespace = namespace or current_k8s_namespace()
        self._lock_ttl_seconds = lock_ttl_seconds
        self._run_events = run_events
        self._max_dispatches_per_tick = max_dispatches_per_tick

    def tick(self) -> WatchResult:
        k8s_client, _ = _safe_load_k8s()
        if k8s_client is None:
            return WatchResult()

        try:
            from kubernetes import client
            from kubernetes import config as k8s_config

            try:
                k8s_config.load_incluster_config()
            except Exception:
                return WatchResult()
            batch_api = client.BatchV1Api()
            core_api = client.CoreV1Api()
        except Exception as exc:
            logger.debug("NativeJobWatcher: k8s unavailable: %s", exc)
            return WatchResult()

        finalized = 0
        still_running = 0
        errors = 0

        with self._session_factory() as session:
            active = list_active_dispatches(session, limit=self._max_dispatches_per_tick)
            for dispatch in active:
                namespace = dispatch.k8s_namespace or self._namespace
                if dispatch.status == "cancel_requested":
                    if not dispatch.k8s_job_name:
                        self._finalize_cancelled(session, dispatch.id)
                        finalized += 1
                        continue
                    try:
                        batch_api.delete_namespaced_job(
                            name=dispatch.k8s_job_name,
                            namespace=namespace,
                            propagation_policy="Foreground",
                        )
                        dispatch.status = "cancelling"
                        still_running += 1
                    except Exception as exc:
                        if _is_not_found(exc):
                            self._finalize_cancelled(session, dispatch.id)
                            _delete_payload_secret(core_api, dispatch.k8s_job_name, namespace)
                            finalized += 1
                        else:
                            logger.warning(
                                "Failed to cancel Job %s: %s",
                                dispatch.k8s_job_name,
                                exc,
                            )
                            errors += 1
                    continue

                if not dispatch.k8s_job_name:
                    continue
                try:
                    job = batch_api.read_namespaced_job(
                        name=dispatch.k8s_job_name,
                        namespace=namespace,
                    )
                except Exception as exc:
                    if _is_not_found(exc):
                        if dispatch.status == "cancelling":
                            self._finalize_cancelled(session, dispatch.id)
                        else:
                            self._finalize_failure(
                                session,
                                dispatch.id,
                                "native_job_missing_before_result_collection",
                            )
                        _delete_payload_secret(core_api, dispatch.k8s_job_name, namespace)
                        finalized += 1
                        continue
                    logger.warning("Failed to read Job %s: %s", dispatch.k8s_job_name, exc)
                    errors += 1
                    continue

                if dispatch.status == "cancelling":
                    self._renew_dispatch_locks(session, dispatch.id)
                    still_running += 1
                    continue

                status = job.status
                if status.succeeded is not None and status.succeeded >= 1:
                    pods = _get_job_pods(batch_api, dispatch.k8s_job_name, namespace)
                    stdout = _read_pod_logs(pods, "cryo-engine", k8s_client)
                    self._finalize_success(session, dispatch.id, stdout)
                    _delete_payload_secret(core_api, dispatch.k8s_job_name, namespace)
                    finalized += 1
                    continue

                if status.failed is not None and status.failed >= 1:
                    pods = _get_job_pods(batch_api, dispatch.k8s_job_name, namespace)
                    stderr = _read_pod_logs(pods, "cryo-engine", k8s_client) or ""
                    if _parse_batch_results(stderr):
                        # Older batch runners exited non-zero when only one member
                        # failed. Preserve successful members from their JSON result.
                        self._finalize_success(session, dispatch.id, stderr)
                        _delete_payload_secret(core_api, dispatch.k8s_job_name, namespace)
                        finalized += 1
                        continue
                    oom = "OOM" in stderr.upper() or "oomkilled" in stderr.lower()
                    reason = "native_job_oom" if oom else "native_job_failed"
                    if stderr:
                        reason = f"{reason}: {stderr[:500]}"
                    # PDAE-8546: no pods and no stderr is the DeadlineExceeded signature --
                    # K8s deletes the pod immediately on activeDeadlineSeconds, unlike a normal
                    # exit-code failure where it lingers for log collection, so _read_pod_logs
                    # above has nothing to read even when some batch members genuinely
                    # finished first. _publish_incremental_progress already streamed each
                    # member's real per-table outcome to run_events every ~30s while the pod
                    # was alive -- consult that before blanket-failing the whole batch.
                    #
                    # BUGFIX 2026-08-19: run_events is keyed per-*run*, not per-dispatch, and a
                    # single governor wave routinely fans a run out into a dozen-plus concurrent
                    # native dispatches sharing that one stream. The old
                    # replay(count=DEFAULT_REPLAY_COUNT=200) pulled the run's 200 most-recent
                    # events regardless of source dispatch -- with several dispatches logging
                    # concurrently, this dispatch's own history routinely got crowded out by
                    # sibling dispatches' events by the time it failed. Verified live: a 32-table
                    # dispatch's salvage lookup returned only 1/32 of its own job_ids present in
                    # the replay window, out of 86 total salvageable job_ids belonging almost
                    # entirely to OTHER dispatches on the same run. Every one of the 1,769
                    # native_job_failed dispatches since the deadline fix landed salvaged zero
                    # jobs as a result. Fix: scope the lookup to this dispatch's own job_ids and
                    # read the full stream (bounded by MAX_STREAM_LEN, not an arbitrary replay
                    # count) so cross-dispatch interleaving can no longer starve it out.
                    own_job_ids = {str(job.id) for job in jobs_for_dispatch(session, dispatch.id)}
                    salvage = self._salvage_from_run_events(dispatch.run_id, own_job_ids)
                    self._finalize_failure(session, dispatch.id, reason, salvage=salvage)
                    _delete_payload_secret(core_api, dispatch.k8s_job_name, namespace)
                    finalized += 1
                    continue

                if dispatch.status == "pending":
                    dispatch.status = "running"
                self._mark_active_jobs(session, dispatch.id, dispatch.k8s_job_name)
                self._publish_incremental_progress(core_api, dispatch, namespace)
                still_running += 1

            session.commit()

        return WatchResult(finalized=finalized, still_running=still_running, errors=errors)

    def _finalize_success(
        self,
        session: Session,
        dispatch_id: uuid.UUID,
        stdout: str | None,
    ) -> None:
        per_table = _parse_batch_results(stdout)
        _log_batch_memory_telemetry(dispatch_id, stdout)
        jobs = jobs_for_dispatch(session, dispatch_id)
        succeeded = 0
        failed = 0
        deferred = 0
        errors: list[str] = []
        for job in jobs:
            entry = per_table.get(str(job.id))
            if entry is None and len(jobs) == 1 and per_table:
                # Single-table non-batch legacy result
                entry = next(iter(per_table.values()))
            ok = bool(entry.get("ok", False)) if entry else False
            error = None if ok else (entry or {}).get("error") or "native_job_no_result"
            if ok:
                noop_operations = _reported_zero_work_operations(entry)
                if noop_operations:
                    ok = False
                    error = (
                        "native_job_reported_success_with_zero_work: "
                        + ", ".join(noop_operations)
                    )
            if ok:
                status = JobStatus.SUCCEEDED
            elif error and _is_transient_metastore_error(error) and _under_retry_cap(
                session, table_id=job.table_id
            ):
                status = JobStatus.DEFERRED
            else:
                status = JobStatus.FAILED
            self._mark_status(str(job.id), status, error=error)
            self._record_result_audits(job, entry, fallback_error=error)
            self._lock_store.release_table_lock(str(job.table_id), str(job.id))
            if ok:
                maybe_persist_optimize_properties(session, job)
                succeeded += 1
            elif status is JobStatus.DEFERRED:
                deferred += 1
            else:
                failed += 1
                errors.append(f"{job.id}: {error}")
        if failed == 0 and deferred == 0 and succeeded:
            dispatch_status = "succeeded"
        elif succeeded or deferred:
            # A deferred member isn't a hard failure -- it's already been requeued for another
            # attempt (reconcile_deferred_jobs, called every governor wave) -- so it belongs in
            # the same "not a clean success, but not a dead loss either" bucket "partial" already
            # covers for a mix of successes and real failures.
            dispatch_status = "partial"
        else:
            dispatch_status = "failed"
        mark_dispatch_finished(
            session,
            dispatch_id,
            status=dispatch_status,
            error="; ".join(errors)[:2000] or None,
        )
        _INCREMENTAL_LOG_SEEN.pop(str(dispatch_id), None)

    def _finalize_failure(
        self,
        session: Session,
        dispatch_id: uuid.UUID,
        reason: str,
        *,
        salvage: dict[str, bool] | None = None,
    ) -> None:
        """Mark every job in a failed dispatch, salvaging real per-table outcomes when known.

        ``salvage`` (from ``_salvage_from_run_events``) maps job_id -> True for members whose
        incrementally-streamed run_events already showed a real success before the batch died
        with no recoverable pod logs (PDAE-8546). A member not in ``salvage`` genuinely has no
        known outcome and stays FAILED with ``reason`` -- this only ever recovers jobs with
        positive evidence of success, never invents one for silence.
        """
        salvage = salvage or {}
        jobs = jobs_for_dispatch(session, dispatch_id)
        salvaged = 0
        for job in jobs:
            if salvage.get(str(job.id)):
                self._mark_status(str(job.id), JobStatus.SUCCEEDED, error=None)
                maybe_persist_optimize_properties(session, job)
                salvaged += 1
            else:
                self._mark_status(str(job.id), JobStatus.FAILED, error=reason)
                self._record_plan_failure_audits(job, reason)
            self._lock_store.release_table_lock(str(job.table_id), str(job.id))
        if salvaged:
            logger.info(
                "Dispatch %s failed (%s) but salvaged %d/%d member(s) from run_events "
                "history that had already streamed a real success before the pod was gone",
                dispatch_id,
                reason,
                salvaged,
                len(jobs),
            )
        mark_dispatch_finished(session, dispatch_id, status="failed", error=reason)
        _INCREMENTAL_LOG_SEEN.pop(str(dispatch_id), None)

    def _salvage_from_run_events(
        self, run_id: Any, own_job_ids: set[str]
    ) -> dict[str, bool]:
        """Reconstruct per-job success from already-streamed run_events log_line events.

        _publish_incremental_progress relays each CRYO_TABLE_RESULT line to run_events while
        a dispatch's pod is still alive (~every 30s). When the pod is later deleted before
        the failure path can read its logs directly (activeDeadlineSeconds' immediate-delete
        behavior), this replayed history is the only surviving record of which batch members
        actually finished. Best-effort: any error here must never block finalization, so an
        empty result (treat everything as genuinely unknown/failed) is always a safe fallback.

        ``run_events`` is keyed per-*run*, not per-dispatch -- a run routinely fans out into
        many concurrent native dispatches sharing one stream. ``own_job_ids`` scopes salvage
        to only this dispatch's members; reading up to ``MAX_STREAM_LEN`` (the stream's own
        cap) rather than a small fixed replay count means sibling dispatches interleaving
        their own events into the same stream can no longer crowd this dispatch's history out
        of the window before it gets read.
        """
        if self._run_events is None or not own_job_ids:
            return {}
        try:
            events = self._run_events.replay(run_id, count=MAX_STREAM_LEN)
        except Exception:
            logger.debug("run_events salvage lookup failed for run_id=%s", run_id, exc_info=True)
            return {}

        succeeded: dict[str, bool] = {}
        failed_job_ids: set[str] = set()
        for event in events:
            if event.get("type") != "log_line":
                continue
            job_id = event.get("job_id")
            if not job_id:
                continue
            job_id = str(job_id)
            if job_id not in own_job_ids:
                continue
            if event.get("level") == "error":
                failed_job_ids.add(job_id)
            else:
                succeeded[job_id] = True
        # A job with any error-level event alongside an info one is not a clean success --
        # e.g. one operation of a multi-operation table result failed. Only credit jobs whose
        # entire streamed history for this run was clean.
        for job_id in failed_job_ids:
            succeeded.pop(job_id, None)
        return succeeded

    def _finalize_cancelled(self, session: Session, dispatch_id: uuid.UUID) -> None:
        jobs = jobs_for_dispatch(session, dispatch_id)
        for job in jobs:
            self._mark_status(str(job.id), JobStatus.SKIPPED, error="cancelled")
            self._lock_store.release_table_lock(str(job.table_id), str(job.id))
        mark_dispatch_finished(session, dispatch_id, status="cancelled", error="cancelled")
        _INCREMENTAL_LOG_SEEN.pop(str(dispatch_id), None)

    def _publish_incremental_progress(
        self,
        core_api: Any,
        dispatch: Any,
        namespace: str,
    ) -> None:
        """Relay new pod-log lines for a still-running dispatch to run_events.

        Gives the Run detail UI live per-operation visibility (Airflow-style task logs)
        instead of the prior behavior where nothing appeared until the whole K8s Job --
        which can bundle several tables and run for minutes -- reached a terminal state.
        Best-effort: any failure here must never affect job finalization, so all errors
        are swallowed.
        """
        if self._run_events is None:
            return
        try:
            pods = _get_job_pods_by_selector(core_api, dispatch.k8s_job_name, namespace)
            if not pods:
                return
            stdout = _read_recent_pod_logs(
                core_api, pods, "cryo-engine", _INCREMENTAL_LOG_WINDOW_SECONDS
            )
            if not stdout:
                return
            seen = _INCREMENTAL_LOG_SEEN.setdefault(str(dispatch.id), set())
            for line in stdout.splitlines():
                line = line.strip()
                if not line or line in seen:
                    continue
                seen.add(line)
                for event in _line_to_log_events(line, run_id=dispatch.run_id):
                    safe_publish(self._run_events, dispatch.run_id, event)
        except Exception:
            logger.debug(
                "Incremental log publish failed for dispatch %s", dispatch.id, exc_info=True
            )

    def _mark_active_jobs(
        self,
        session: Session,
        dispatch_id: uuid.UUID,
        k8s_job_name: str,
    ) -> None:
        for job in jobs_for_dispatch(session, dispatch_id):
            renewed = self._lock_store.renew_table_lock(
                str(job.table_id),
                str(job.id),
                self._lock_ttl_seconds,
            )
            if not renewed and not self._lock_store.acquire_table_lock(
                str(job.table_id),
                str(job.id),
                self._lock_ttl_seconds,
            ):
                logger.error(
                    "Native Job %s is active but table %s lock is owned by another job",
                    k8s_job_name,
                    job.table_id,
                )
            if job.status != JobStatus.RUNNING.value:
                self._mark_status(
                    str(job.id),
                    JobStatus.RUNNING,
                    engine_ref=k8s_job_name,
                    replica=k8s_job_name,
                )

    def _renew_dispatch_locks(self, session: Session, dispatch_id: uuid.UUID) -> None:
        for job in jobs_for_dispatch(session, dispatch_id):
            self._lock_store.renew_table_lock(
                str(job.table_id),
                str(job.id),
                self._lock_ttl_seconds,
            )

    def _record_result_audits(
        self,
        job: Any,
        entry: dict[str, Any] | None,
        *,
        fallback_error: str | None,
    ) -> None:
        if self._record_audit is None:
            return
        results = entry.get("results") if entry else None
        if not isinstance(results, list) or not results:
            self._record_plan_failure_audits(job, fallback_error or "native_job_no_result")
            return
        for result in results:
            if not isinstance(result, dict):
                continue
            action = _native_operation_type(result.get("operation"))
            if action is None:
                continue
            success = bool(result.get("success", False))
            try:
                self._record_audit(
                    AuditRecord(
                        job_id=str(job.id),
                        table_id=str(job.table_id),
                        action=action,
                        status="OK" if success else "FAILED",
                        engine=EngineType.NATIVE,
                        detail=result.get("error") if not success else None,
                        duration_ms=_optional_int(result.get("duration_ms")),
                        input_files_count=_optional_int(result.get("input_files")),
                        output_files_count=_optional_int(result.get("output_files")),
                        actual_bytes_freed=_optional_int(result.get("bytes_removed")),
                    )
                )
            except Exception:
                logger.exception("Failed to record native audit for job %s", job.id)

    def _record_plan_failure_audits(self, job: Any, reason: str) -> None:
        if self._record_audit is None:
            return
        operations = (job.plan or {}).get("operations") if job.plan else None
        if not isinstance(operations, list):
            return
        for operation in operations:
            raw_type = operation.get("type") if isinstance(operation, dict) else operation
            action = _native_operation_type(raw_type)
            if action is None:
                continue
            try:
                self._record_audit(
                    AuditRecord(
                        job_id=str(job.id),
                        table_id=str(job.table_id),
                        action=action,
                        status="FAILED",
                        engine=EngineType.NATIVE,
                        detail=reason,
                    )
                )
            except Exception:
                logger.exception("Failed to record native failure audit for job %s", job.id)


def _parse_batch_results(stdout: str | None) -> dict[str, dict[str, Any]]:
    """Map job_id -> per-table result from batch (or single) JSON stdout."""
    if not stdout:
        return {}
    _, lines = split_stdout_lines(stdout)
    if not lines:
        return {}

    by_job: dict[str, dict[str, Any]] = {}
    for line in lines:
        if not line.startswith(TABLE_RESULT_PREFIX):
            continue
        try:
            item = json.loads(line[len(TABLE_RESULT_PREFIX) :])
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("job_id"):
            by_job[str(item["job_id"])] = item

    aggregate_lines = [line for line in lines if not line.startswith(TABLE_RESULT_PREFIX)]
    if not aggregate_lines:
        return by_job
    try:
        parsed = json.loads(aggregate_lines[-1])
    except json.JSONDecodeError:
        return by_job

    if parsed.get("batch") and isinstance(parsed.get("results"), list):
        for item in parsed["results"]:
            if not isinstance(item, dict):
                continue
            job_id = item.get("job_id")
            if job_id:
                by_job[str(job_id)] = item
        return by_job

    # Single-table shape
    job_id = parsed.get("job_id")
    if job_id:
        by_job[str(job_id)] = {
            "ok": bool(parsed.get("ok")),
            "error": parsed.get("error"),
            "results": parsed.get("results"),
        }
    elif "ok" in parsed:
        by_job["_single"] = {
            "ok": bool(parsed.get("ok")),
            "error": parsed.get("error"),
            "results": parsed.get("results"),
        }
    return by_job


def _log_batch_memory_telemetry(dispatch_id: uuid.UUID, stdout: str | None) -> None:
    """Cost remediation plan §3, 2026-09-16 (docs/CRYO_UNUSED_COST_FIX_2026-09-16.md): the
    per-member serial-batch memory allowance in packing.py's _serial_pod_request is an
    admitted heuristic, explicitly flagged there as "pending real cgroup-memory telemetry from
    a live batch pod -- revisit the exact number once that exists." batch.py's execute_batch
    now reports member_count and peak_cgroup_memory_mib (this pod's own cgroup v2/v1 peak
    memory high-water-mark) in its aggregate result JSON -- logging it here at info level on
    every successful dispatch means a real distribution of (member_count, peak_memory) pairs
    accumulates in the logs/log-search backend over time, which is exactly the measurement
    basis packing.py's own comment says is missing before that heuristic can be safely
    replaced. Deliberately just a log line, not a new metrics pipeline or DB table -- the doc's
    own required regression is "measure real batch memory," not "build a dashboard for it,"
    and a structured log line is queryable via the existing S3/Trino query-log ingestion path
    this codebase already uses for cost telemetry."""
    if not stdout:
        return
    _, lines = split_stdout_lines(stdout)
    aggregate_lines = [line for line in lines if not line.startswith(TABLE_RESULT_PREFIX)]
    if not aggregate_lines:
        return
    try:
        parsed = json.loads(aggregate_lines[-1])
    except json.JSONDecodeError:
        return
    if not isinstance(parsed, dict) or not parsed.get("batch"):
        return
    peak_mib = parsed.get("peak_cgroup_memory_mib")
    member_count = parsed.get("member_count")
    if peak_mib is None:
        return
    logger.info(
        "Native batch dispatch %s: member_count=%s peak_cgroup_memory_mib=%s",
        dispatch_id,
        member_count,
        peak_mib,
    )


def _get_job_pods_by_selector(core_api: Any, job_name: str, namespace: str) -> list[Any]:
    try:
        pods = core_api.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={job_name}",
        )
        return list(pods.items)
    except Exception:
        return []


def _read_recent_pod_logs(
    core_api: Any,
    pods: list[Any],
    container_name: str,
    since_seconds: int,
) -> str | None:
    """Read only the tail window of pod logs, for incremental (still-running) tailing.

    Deliberately uses since_seconds rather than _read_pod_logs' limit_bytes: limit_bytes
    truncates from the START of the log, so once a verbose multi-table batch exceeds that
    cap, later reads would keep returning the same stale prefix and never see new lines.
    since_seconds always returns whatever landed in the trailing window, which is what
    incremental tailing needs.
    """
    for pod in pods:
        try:
            logs = core_api.read_namespaced_pod_log(
                name=pod.metadata.name,
                namespace=pod.metadata.namespace,
                container=container_name,
                since_seconds=since_seconds,
            )
            return logs if isinstance(logs, str) else None
        except Exception:
            continue
    return None


def _line_to_log_events(line: str, *, run_id: Any) -> list[dict[str, Any]]:
    """Parse one stdout line from the native engine into run_events log_line payloads.

    A single CRYO_TABLE_RESULT line can carry more than one operation's result (e.g.
    expire_snapshots and remove_orphan_files bundled for the same table) -- emit one
    event per operation rather than only the first, or later operations in the same
    line would silently never appear in the live log.
    """
    if line.startswith(TABLE_RESULT_PREFIX):
        try:
            item = json.loads(line[len(TABLE_RESULT_PREFIX) :])
        except json.JSONDecodeError:
            return []
        if not isinstance(item, dict):
            return []
        job_id = item.get("job_id")
        table_id = item.get("table_id")
        table_ref = item.get("table_ref")
        results = [r for r in (item.get("results") or []) if isinstance(r, dict)]
        if not results:
            if item.get("ok", True):
                return []
            return [
                build_log_line(
                    run_id=run_id,
                    job_id=job_id,
                    table_id=table_id,
                    table_ref=table_ref,
                    engine="native",
                    level="error",
                    message=f"table failed: {item.get('error') or 'unknown error'}",
                )
            ]
        events = []
        for result in results:
            operation = str(result.get("operation") or "native")
            ok = bool(result.get("success"))
            duration_ms = result.get("duration_ms")
            if ok:
                message = f"{operation}: done" + (
                    f" ({duration_ms}ms)" if duration_ms is not None else ""
                )
                level = "info"
            else:
                message = f"{operation}: failed — {result.get('error') or 'unknown error'}"
                level = "error"
            events.append(
                build_log_line(
                    run_id=run_id,
                    job_id=job_id,
                    table_id=table_id,
                    table_ref=table_ref,
                    operation=operation,
                    engine="native",
                    level=level,
                    message=message,
                )
            )
        return events

    if line.startswith(PROGRESS_PREFIX):
        try:
            event = json.loads(line[len(PROGRESS_PREFIX) :])
        except json.JSONDecodeError:
            return []
        if not isinstance(event, dict):
            return []
        operation = str(event.get("operation") or "native")
        event_name = str(event.get("event") or "progress")
        raw_message = event.get("message")
        if raw_message:
            progress_message = str(raw_message)
        else:
            pct = event.get("progress_pct")
            progress_message = f"{operation}: {event_name}" + (
                f" {pct}%" if pct is not None else ""
            )
        return [
            build_log_line(
                run_id=run_id,
                job_id=event.get("job_id"),
                table_id=event.get("table_id"),
                table_ref=event.get("table_ref"),
                operation=operation,
                engine="native",
                level="error" if event_name == "failed" else "info",
                message=progress_message,
            )
        ]

    return []


def _is_not_found(exc: Exception) -> bool:
    return getattr(exc, "status", None) == 404


def _delete_payload_secret(core_api: Any, job_name: str, namespace: str) -> None:
    try:
        core_api.delete_namespaced_secret(
            name=f"{job_name}-payload",
            namespace=namespace,
        )
    except Exception as exc:
        if not _is_not_found(exc):
            logger.warning("Failed to delete payload Secret for Job %s: %s", job_name, exc)


def _native_operation_type(value: Any) -> OperationType | None:
    aliases = {
        "expire_snapshots": OperationType.EXPIRE_SNAPSHOTS,
        "remove_orphan_files": OperationType.REMOVE_ORPHAN_FILES,
        "rewrite_data_files": OperationType.OPTIMIZE,
        "optimize": OperationType.OPTIMIZE,
        "rewrite_manifests": OperationType.OPTIMIZE_MANIFESTS,
        "optimize_manifests": OperationType.OPTIMIZE_MANIFESTS,
        # Real incident, 2026-09-15/16: rewrite_position_deletes and compute_statistics were
        # missing from this map entirely, so their per-operation audit records were silently
        # dropped in _record_result_audits (action resolves to None -> skipped) -- the exact
        # operation type in the gold_dbt_poll_dashboard_view silent-no-op incident had no audit
        # trail at all, which is part of why the no-op went unnoticed for 5 days.
        "rewrite_position_deletes": OperationType.REWRITE_POSITION_DELETES,
        "compute_statistics": OperationType.COMPUTE_STATISTICS,
        "convert_deletes": OperationType.CONVERT_DELETES,
    }
    if isinstance(value, OperationType):
        return value
    return aliases.get(str(value))


# Real incident, 2026-09-15/16: gold_dbt_poll_dashboard_view's native-engine job reported
# status=succeeded with a real, correctly-planned operation list (rewrite_position_deletes,
# optimize, compute_statistics, expire_snapshots, remove_orphan_files), ran for 25 minutes, but
# the table's live Iceberg $snapshots history showed zero new commits -- the native binary
# self-reported success=true on every operation while doing nothing. _finalize_success trusted
# that self-report verbatim: nothing here cross-checked the file/byte/snapshot counters the
# binary itself returned against what a real mutation of that operation type must produce. For
# these operation types, success=true with an all-zero (or absent) work-counter is not
# distinguishable from "there was nothing to do" using the counters alone in every case, but for
# a job whose plan explicitly selected this table for this operation (planner already found
# something to fix), that combination is best treated as a suspicious no-op rather than a clean
# success -- the cost of a false-positive retry is far lower than silently losing 5 days of
# maintenance on a delete-heavy table.
_WORK_COUNTER_FIELDS_BY_OPERATION: dict[OperationType, tuple[str, ...]] = {
    OperationType.REWRITE_POSITION_DELETES: ("files_rewritten", "input_files", "bytes_removed"),
    OperationType.OPTIMIZE: ("files_rewritten", "input_files", "bytes_removed"),
    OperationType.CONVERT_DELETES: ("files_rewritten", "input_files", "bytes_removed"),
    OperationType.EXPIRE_SNAPSHOTS: ("snapshots_expired",),
    OperationType.REMOVE_ORPHAN_FILES: ("bytes_removed", "input_files"),
    OperationType.OPTIMIZE_MANIFESTS: ("manifests_rewritten",),
}


def _reported_zero_work(result: dict[str, Any], op_type: OperationType) -> bool:
    fields = _WORK_COUNTER_FIELDS_BY_OPERATION.get(op_type)
    if not fields:
        # compute_statistics and any operation type without a defined work counter never
        # commits a data/snapshot change by design -- nothing to sanity-check here.
        return False
    for field in fields:
        value = _optional_int(result.get(field))
        if value:
            return False
    return True


def _reported_zero_work_operations(entry: dict[str, Any] | None) -> list[str]:
    """Operation names inside a per-table result entry that claimed success=true but reported
    no work-counter evidence of an actual mutation -- see _WORK_COUNTER_FIELDS_BY_OPERATION's
    docstring for the incident this guards against. Returns an empty list for a clean result
    (including a job with no results at all, which _record_result_audits already flags via
    native_job_no_result -- this check only adds signal on top of a claimed per-operation
    success, never invents a failure where the binary already reported one)."""
    if not entry:
        return []
    results = entry.get("results")
    if not isinstance(results, list) or not results:
        return []
    flagged: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        if not bool(result.get("success", False)):
            continue
        op_type = _native_operation_type(result.get("operation"))
        if op_type is None:
            continue
        if _reported_zero_work(result, op_type):
            flagged.append(str(result.get("operation")))
    return flagged


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
