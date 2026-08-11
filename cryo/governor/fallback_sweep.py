"""Resolve async Spark fallback dispatches (PDAE-8352) to their real terminal outcome.

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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, Destination
from cryo.engines.spark.k8s import JobRunState

if TYPE_CHECKING:
    from cryo.engines.spark.k8s_engine import SparkK8sEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FallbackSweepResult:
    resolved_ok: int
    resolved_failed: int
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
        return FallbackSweepResult(0, 0, len(rows), 0)

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
        if result.state == JobRunState.SUCCESS:
            row.status = "OK"
            resolved_ok += 1
        else:
            row.status = "FAILED"
            row.detail = result.state_details or row.detail
            resolved_failed += 1
        row.resolved_at = datetime.now(UTC)

    if resolved_ok or resolved_failed:
        session.commit()

    return FallbackSweepResult(resolved_ok, resolved_failed, still_pending, lookup_errors)


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
