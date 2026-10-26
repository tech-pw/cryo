"""Resolve async Spark fallback dispatches to their real terminal outcome.

_dispatch_fallback_operations (cryo/governor/native_admit.py) submits a Spark fallback
job (e.g. optimize_manifests, unsupported by the native engine) and returns immediately
-- run_wave() no longer blocks on wait_for_completion(), which used to cap the whole
Governor's per-wave throughput at the fallback destination's own slot_cap regardless of
the native engine's much higher one (confirmed live, 2026-08-11: 7,855 deep-mode jobs
queued, draining at ~12/min against a native slot_cap of 80).

That submission is recorded as an audit_log row with status="SUBMITTED" and the K8s Job
name in engine_ref. Nothing else in the system reaches a conclusion about whether that
job actually succeeded unless something asks -- this sweep is that something. It's the
async counterpart to how native K8s jobs are "fire-and-forget and watched separately"
(see native_admit.py's own docstring) -- fallback dispatches now follow the identical
shape, and this module is the "watched separately" half that was previously missing.

2026-09-01: also finalizes the underlying cryo.jobs row, not just the audit_log row --
the original version resolved AuditLog.status but never touched MaintenanceJob.status,
so a job dispatched through this path stayed 'running' forever regardless of real
outcome. Live-verified: one job (f204ecbc) had 4 separate AuditLog rows all resolved OK
across a single day (real successful re-dispatches, each ~30-120min), while its own
MaintenanceJob row stayed status='running' with started_at frozen at the very first
attempt -- 19h+ stale by the time it was noticed, and would have stayed stuck open
indefinitely since nothing else in the system was watching this job's row at all. Native
jobs don't have this gap (NativeJobWatcher finalizes the job row directly); this sweep
now does the equivalent for the fallback path, using the same status mapping
finalize_open_runs already uses for job-status vocabulary (succeeded/failed) so a job
resolved here reads identically to one finalized any other way.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, Destination, MaintenanceJob, TableStats
from cryo.domain.enums import JobStatus, OperationType
from cryo.engines.base import capacity_refusal_error, is_capacity_refusal
from cryo.engines.spark.k8s import JobRunState
from cryo.engines.spark.procedure_metrics import rewrite_file_counts

if TYPE_CHECKING:
    from cryo.engines.spark.k8s_engine import SparkK8sEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FallbackSweepResult:
    resolved_ok: int
    resolved_failed: int
    resolved_unknown: int
    still_pending: int
    lookup_errors: int


def sweep_pending_fallback_dispatches(
    session: Session,
    *,
    max_rows: int = 200,
) -> FallbackSweepResult:
    """Resolve up to ``max_rows`` SUBMITTED audit rows to their real Spark outcome.

    Bounded per call (matches run_silent_data_loss_sweep's own rationale in
    cryo/scheduler/health.py) so one sweep can't itself become a scheduler-tick
    bottleneck -- a backlog of pending rows is resolved across several ticks, not one.
    One row's lookup failure (network hiccup, destination since deleted) must not abort
    the rest of the batch.
    """
    rows = session.scalars(
        select(AuditLog)
        .where(AuditLog.status == "SUBMITTED")
        .order_by(AuditLog.ts.asc())
        .limit(max_rows)
    ).all()

    resolved_ok = 0
    resolved_failed = 0
    resolved_unknown = 0
    still_pending = 0
    lookup_errors = 0

    # engine_ref lookups all go through the same fallback destination's Spark client --
    # resolve it once per sweep call, not once per row, and skip the whole batch (leaving
    # rows SUBMITTED for the next tick) if it can't be resolved at all rather than
    # guessing which destination each row's engine_ref belongs to.
    engine = _spark_engine_for_fallback_lookups(session)
    if engine is None:
        logger.warning(
            "Fallback sweep: no resolvable Spark fallback destination; %d row(s) left pending",
            len(rows),
        )
        return FallbackSweepResult(0, 0, 0, len(rows), 0)

    for row in rows:
        if not row.engine_ref:
            # A SUBMITTED row with no engine_ref shouldn't exist (submit() only records
            # SUBMITTED on a successful dispatch, which always has one) -- treat it the
            # same as a lookup failure rather than looping on it forever.
            lookup_errors += 1
            continue
        try:
            result = engine.k8s_client.get_job_run(row.engine_ref)
        except Exception:
            logger.warning(
                "Fallback sweep: lookup failed for engine_ref=%s (audit row %s)",
                row.engine_ref,
                row.id,
                exc_info=True,
            )
            lookup_errors += 1
            continue

        if result.state == JobRunState.RUNNING:
            still_pending += 1
            continue
        # submit() returns before the driver exists, so the one OOM step cannot live
        # in execute(). The 2026-10-19 expire died on this path. Resubmit once, on a
        # larger driver, and leave the row SUBMITTED so the next tick resolves the
        # new job. A second kill, a SQL error, or a missing plan falls through and
        # finalizes FAILED as before.
        if result.state == JobRunState.FAILED and _resubmit_after_driver_oom(session, row, result):
            # The new engine_ref has to be visible to the next tick. The terminal
            # path commits below; this one returns early, so it commits itself.
            session.commit()
            still_pending += 1
            continue
        if result.state == JobRunState.SUCCESS:
            row.status = "OK"
            if result.bytes_removed is not None:
                row.actual_bytes_freed = result.bytes_removed
            input_files, output_files = rewrite_file_counts(str(row.action), result.metrics)
            if input_files is not None:
                row.input_files_count = input_files
            if output_files is not None:
                row.output_files_count = output_files
            resolved_ok += 1
            job_status = JobStatus.SUCCEEDED
        elif result.state == JobRunState.UNKNOWN:
            # Job + driver both gone (TTL-expired) before anything ever resolved this row
            # -- the real outcome is unrecoverable, not "known to have failed". Distinct
            # terminal status so this doesn't read as a false FAILED (implies an observed
            # failure) or keep looking like an active bug (lookup_errors, which retries
            # forever -- this will never resolve differently on a later tick).
            row.status = "UNKNOWN"
            row.detail = result.state_details or row.detail
            resolved_unknown += 1
            # No positive evidence of success -- FAILED (not SUCCEEDED) is the safe default
            # for the job row, matching how a genuinely-failed dispatch is treated below.
            job_status = JobStatus.FAILED
        elif is_capacity_refusal(result.state_details):
            # A ResourceQuota refused the driver pod or Service: nothing ran, and the table
            # is fine. Defer the job so the reconciler requeues it (issue #159).
            row.status = "DEFERRED"
            row.detail = capacity_refusal_error(result.state_details or "")
            resolved_failed += 1
            job_status = JobStatus.DEFERRED
        else:
            row.status = "FAILED"
            row.detail = result.state_details or row.detail
            resolved_failed += 1
            job_status = JobStatus.FAILED
        row.resolved_at = datetime.now(UTC)
        _finalize_job_row(session, job_id=row.job_id, status=job_status, error=row.detail)
        # submit() (the async fire-and-forget path this sweep resolves) deliberately
        # skips cleanup at dispatch time -- the driver pod/credentials Secret are still
        # in use while the job runs (see SparkK8sEngine.submit's own docstring). Now that
        # this row has reached a terminal state, this is the only place left that will
        # ever call it -- execute()'s own `finally: cleanup_after_run(...)` never runs
        # for a submit()-dispatched job, since execute() is never called for it at all.
        # Still worth attempting even for UNKNOWN: the wrapper Job is gone but the
        # credentials Secret may not be (it isn't Job-TTL-owned in every code path --
        # see _adopt_credentials_secret). Best-effort: a cleanup failure here must not
        # stop this row from being marked resolved -- the outcome is already known (or
        # known-unknowable) and correct either way.
        try:
            engine.k8s_client.cleanup_after_run(row.engine_ref)
        except Exception:
            logger.warning(
                "Fallback sweep: cleanup_after_run failed for engine_ref=%s (audit row %s)",
                row.engine_ref,
                row.id,
                exc_info=True,
            )

    if resolved_ok or resolved_failed or resolved_unknown:
        session.commit()

    return FallbackSweepResult(
        resolved_ok, resolved_failed, resolved_unknown, still_pending, lookup_errors
    )


def _finalize_job_row(
    session: Session, *, job_id: uuid.UUID | None, status: JobStatus, error: str | None
) -> None:
    """Mirrors mark_status's own started_at/finished_at semantics (job_store.py) so a job
    finalized here reads identically to one finalized through the normal admission path.

    Only touches a job still in RUNNING -- a job that already reached a terminal state some
    other way (a later dispatch superseded it, an operator intervened) is left alone rather
    than overwritten by a possibly-stale resolution of an older audit row. job_id can, in
    principle, reference a since-deleted job (FK is nullable) -- silently no-ops rather than
    erroring, since a missing job row means there's nothing left to finalize."""
    if job_id is None:
        return
    job = session.get(MaintenanceJob, job_id)
    if job is None or job.status != JobStatus.RUNNING.value:
        return
    job.status = status.value
    job.finished_at = datetime.now(UTC)
    if status is not JobStatus.SUCCEEDED:
        job.error = error


def _resubmit_after_driver_oom(session: Session, row: AuditLog, result: object) -> bool:
    """Submit one larger driver for an OOMKilled expire/orphan. False means finalize.

    The async path never calls execute(), so the step that lives there never ran for
    the expire that died. This is that step. It does not retry a SQL error, a second
    kill, or an op whose driver is not the pressure point. A resubmit that cannot
    rebuild the statement returns False and the caller finalizes FAILED.
    """
    from cryo.engines.spark.k8s import JobRunResult
    from cryo.engines.spark.k8s_engine import (
        _oom_step_would_grow,
        is_spark_driver_oom,
    )
    from cryo.engines.spark.sql import render
    from cryo.planner.serialization import plan_from_dict

    if not isinstance(result, JobRunResult) or not is_spark_driver_oom(result.state_details):
        return False
    if row.action not in {"expire_snapshots", "remove_orphan_files"}:
        return False
    # The job name is the marker. engine_ref is a random cryo-spark-<hex> and
    # cannot carry it. A second kill of a job we already stepped must finalize.
    if "oom-step" in (row.detail or ""):
        return False
    if row.job_id is None:
        return False
    job = session.get(MaintenanceJob, row.job_id)
    if job is None or not job.plan:
        return False
    stats_row = session.get(TableStats, row.table_id) if row.table_id is not None else None
    total_files = _pressure_file_count(stats_row)
    # The pod that died ran at step 0. Passing 1 here would score the heap of the
    # pod we have not launched and skip the only retry a 3072MiB tier gets.
    if not _oom_step_would_grow(row.action, total_files, 0):
        return False
    try:
        plan = plan_from_dict(job.plan)
        statements = [
            statement for statement in render(plan) if statement.action == OperationType(row.action)
        ]
        if len(statements) != 1:
            return False
        engine = _spark_engine_for_fallback_lookups(session)
        if engine is None:
            return False
        catalog = statements[0].metadata.get("catalog") or engine._catalog
        if not catalog:
            return False
        if stats_row is not None:
            engine = engine.with_table_stats(
                {
                    "total_size_gb": stats_row.total_size_gb,
                    "total_files": stats_row.total_files,
                    "total_delete_files": stats_row.total_delete_files,
                }
            )
        new_ref = engine.k8s_client.submit_spark_sql(
            statements[0].sql,
            job_name=f"cryo-{row.action}-oom-step",
            catalog=str(catalog),
            destination_id=engine._destination_id,
            operation=row.action,
            total_size_gb=float(stats_row.total_size_gb) if stats_row is not None else None,
            total_files=total_files,
            oom_steps=1,
        )
    except Exception:
        logger.warning(
            "Fallback sweep: OOM step-up failed for audit row %s; finalizing FAILED",
            row.id,
            exc_info=True,
        )
        return False
    try:
        engine.k8s_client.cleanup_after_run(row.engine_ref or "")
    except Exception:
        logger.warning(
            "Fallback sweep: cleanup of OOMKilled job %s failed",
            row.engine_ref,
            exc_info=True,
        )
    row.engine_ref = new_ref
    # Keep the marker in front of the old log. The next tick's success path
    # replaces detail, so this only has to survive until that tick. A second
    # kill reads it and finalizes instead of starting a third pod.
    row.detail = f"oom-step {new_ref}\n{result.state_details or ''}"
    logger.info(
        "Fallback sweep: resubmitted %s after driver OOM (was %s, now %s, files=%s)",
        row.action,
        result.job_run_id,
        new_ref,
        total_files,
    )
    return True


def _pressure_file_count(stats_row: TableStats | None) -> int | None:
    """Data files plus delete files. Either missing stays unknown, not zero."""
    if stats_row is None:
        return None
    data = stats_row.total_files
    deletes = stats_row.total_delete_files
    if data is None and deletes is None:
        return None
    return int(data or 0) + int(deletes or 0)


def _spark_engine_for_fallback_lookups(session: Session) -> SparkK8sEngine | None:
    """Any enabled spark_emr destination works -- get_job_run() only needs the K8s
    namespace/client config, not the specific destination a given row's job was
    submitted through (all Spark fallback dispatches land in the same namespace)."""
    from cryo.engines.spark.k8s_engine import SparkK8sEngine

    destination = session.scalar(
        select(Destination).where(Destination.engine == "spark_emr", Destination.enabled.is_(True))
    )
    if destination is None:
        return None
    try:
        return SparkK8sEngine(config=dict(destination.config))
    except Exception:
        logger.warning(
            "Fallback sweep: could not construct SparkK8sEngine for destination %s",
            destination.id,
            exc_info=True,
        )
        return None
