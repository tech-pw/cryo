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
from cryo.engines.native.batch import TABLE_RESULT_PREFIX
from cryo.engines.native.job_dispatch import (
    _get_job_pods,
    _read_pod_logs,
    _safe_load_k8s,
)
from cryo.engines.native.job_state import current_k8s_namespace
from cryo.engines.native.runner import split_stdout_lines
from cryo.queue.locks import LockStore
from cryo.worker.worker import AuditRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchResult:
    finalized: int = 0
    still_running: int = 0
    errors: int = 0


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
    ) -> None:
        self._session_factory = session_factory
        self._lock_store = lock_store
        self._mark_status = mark_status
        self._record_audit = record_audit
        self._namespace = namespace or current_k8s_namespace()
        self._lock_ttl_seconds = lock_ttl_seconds

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
            active = list_active_dispatches(session)
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
                            _delete_payload_secret(
                                core_api, dispatch.k8s_job_name, namespace
                            )
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
                    logger.warning(
                        "Failed to read Job %s: %s", dispatch.k8s_job_name, exc
                    )
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
                    self._finalize_failure(session, dispatch.id, reason)
                    _delete_payload_secret(core_api, dispatch.k8s_job_name, namespace)
                    finalized += 1
                    continue

                if dispatch.status == "pending":
                    dispatch.status = "running"
                self._mark_active_jobs(session, dispatch.id, dispatch.k8s_job_name)
                still_running += 1

            session.commit()

        return WatchResult(
            finalized=finalized, still_running=still_running, errors=errors
        )

    def _finalize_success(
        self,
        session: Session,
        dispatch_id: uuid.UUID,
        stdout: str | None,
    ) -> None:
        per_table = _parse_batch_results(stdout)
        jobs = jobs_for_dispatch(session, dispatch_id)
        succeeded = 0
        failed = 0
        errors: list[str] = []
        for job in jobs:
            entry = per_table.get(str(job.id))
            if entry is None and len(jobs) == 1 and per_table:
                # Single-table non-batch legacy result
                entry = next(iter(per_table.values()))
            ok = bool(entry.get("ok", False)) if entry else False
            error = None if ok else (entry or {}).get("error") or "native_job_no_result"
            status = JobStatus.SUCCEEDED if ok else JobStatus.FAILED
            self._mark_status(str(job.id), status, error=error)
            self._record_result_audits(job, entry, fallback_error=error)
            self._lock_store.release_table_lock(str(job.table_id), str(job.id))
            if ok:
                succeeded += 1
            else:
                failed += 1
                errors.append(f"{job.id}: {error}")
        if failed == 0 and succeeded:
            dispatch_status = "succeeded"
        elif succeeded:
            dispatch_status = "partial"
        else:
            dispatch_status = "failed"
        mark_dispatch_finished(
            session,
            dispatch_id,
            status=dispatch_status,
            error="; ".join(errors)[:2000] or None,
        )

    def _finalize_failure(
        self,
        session: Session,
        dispatch_id: uuid.UUID,
        reason: str,
    ) -> None:
        jobs = jobs_for_dispatch(session, dispatch_id)
        for job in jobs:
            self._mark_status(str(job.id), JobStatus.FAILED, error=reason)
            self._record_plan_failure_audits(job, reason)
            self._lock_store.release_table_lock(str(job.table_id), str(job.id))
        mark_dispatch_finished(session, dispatch_id, status="failed", error=reason)

    def _finalize_cancelled(self, session: Session, dispatch_id: uuid.UUID) -> None:
        jobs = jobs_for_dispatch(session, dispatch_id)
        for job in jobs:
            self._mark_status(str(job.id), JobStatus.SKIPPED, error="cancelled")
            self._lock_store.release_table_lock(str(job.table_id), str(job.id))
        mark_dispatch_finished(session, dispatch_id, status="cancelled", error="cancelled")

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

    aggregate_lines = [
        line for line in lines if not line.startswith(TABLE_RESULT_PREFIX)
    ]
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
    }
    if isinstance(value, OperationType):
        return value
    return aliases.get(str(value))


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
