from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any

from cryo.capabilities import EffectStrategy
from cryo.db.repositories.intents import IntentStore, ReconciliationEffect
from cryo.domain.enums import JobStatus, JobStepStatus
from cryo.domain.intents import EffectState
from cryo.engines.trino.query_status import EngineOpState
from cryo.observability.correlation import correlation_scope, set_context
from cryo.observability.metrics import (
    maintenance_reconciliation_backlog,
    maintenance_reconciliation_duration_seconds,
    maintenance_reconciliation_oldest_age_seconds,
    maintenance_reconciliation_outcomes_total,
)
from cryo.observability.tracing import trace_operation
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.worker.orphan_recovery import probe_engine_op_state
from cryo.worker.worker import JobStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IntentReconciliationResult:
    examined: int = 0
    resumed: int = 0
    waiting: int = 0
    manual_review: int = 0
    superseded: int = 0


class IntentReconciler:
    def __init__(
        self,
        *,
        intents: IntentStore,
        jobs: JobStore,
        queue: MaintenanceQueue,
        step_ledger: Any | None = None,
        probe_interval_seconds: int = 30,
    ) -> None:
        self._intents = intents
        self._jobs = jobs
        self._queue = queue
        self._step_ledger = step_ledger
        self._probe_interval_seconds = probe_interval_seconds

    def run_once(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> IntentReconciliationResult:
        now = now or datetime.now(UTC)
        backlog = self._intents.reconciliation_backlog(now=now)
        maintenance_reconciliation_backlog.set(backlog.count)
        oldest_age = (
            max(0.0, (_as_utc(now) - _as_utc(backlog.oldest_created_at)).total_seconds())
            if backlog.oldest_created_at is not None
            else 0.0
        )
        maintenance_reconciliation_oldest_age_seconds.set(oldest_age)
        started_at = perf_counter()
        with (
            correlation_scope(clear=True),
            trace_operation(
                "maintenance.effects.reconcile",
                attributes={"cryo.reconciliation.backlog": backlog.count},
            ),
        ):
            try:
                return self._reconcile_due_effects(now=now, limit=limit)
            finally:
                maintenance_reconciliation_duration_seconds.observe(perf_counter() - started_at)

    def _reconcile_due_effects(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> IntentReconciliationResult:
        effects = self._intents.list_reconciliation_effects(now=now, limit=limit)
        resumed = waiting = manual = superseded = 0
        for effect in effects:
            set_context(
                replace=True,
                correlation_id=effect.correlation_id or effect.job_id,
                run_id=effect.run_id,
                job_id=effect.job_id,
                intent_id=effect.intent_id,
                attempt_id=effect.attempt_id,
                effect_id=effect.receipt_id,
                table_id=effect.table_id,
                destination_id=effect.destination_id,
                engine_ref=effect.external_reference,
            )
            if effect.strategy is EffectStrategy.MANUAL_RECONCILIATION:
                manual += 1
                self._defer(effect, now=now, seconds=3600)
                logger.warning(
                    "Effect %s for job %s requires manual reconciliation",
                    effect.receipt_id,
                    effect.job_id,
                )
                continue
            destination = _try_get_destination(self._jobs, effect.destination_id)
            if destination is None:
                waiting += 1
                self._defer(effect, now=now)
                continue
            state = probe_engine_op_state(
                destination,
                table_ref=effect.table_ref,
                operation=effect.operation,
                engine_ref=effect.external_reference,
            )
            if state in {
                EngineOpState.RUNNING,
                EngineOpState.UNCERTAIN,
                EngineOpState.NOT_APPLICABLE,
            }:
                waiting += 1
                self._defer(effect, now=now)
                continue
            if state not in {EngineOpState.SUCCEEDED, EngineOpState.FAILED}:
                waiting += 1
                self._defer(effect, now=now)
                continue

            resolution = (
                EffectState.SUCCEEDED if state is EngineOpState.SUCCEEDED else EffectState.FAILED
            )
            auto_resume = (effect.submission or {}).get("continuation") != "manual_review"
            current = self._intents.complete_reconciliation(
                effect.receipt_id,
                state=resolution,
                detail=f"engine probe resolved {state.value}",
                make_ready=auto_resume,
                now=now,
            )
            if not current:
                superseded += 1
                continue
            if self._step_ledger is not None:
                self._step_ledger.complete_step(
                    effect.job_id,
                    effect.step_index,
                    (
                        JobStepStatus.SUCCEEDED
                        if resolution is EffectState.SUCCEEDED
                        else JobStepStatus.FAILED
                    ),
                    engine_ref=effect.external_reference,
                    detail=f"effect reconciled as {resolution.value}",
                )
            if not auto_resume:
                manual += 1
                logger.warning(
                    "Effect %s for job %s was reconciled but requires manual continuation",
                    effect.receipt_id,
                    effect.job_id,
                )
                continue
            job = self._jobs.get_job(effect.job_id)
            self._jobs.mark_status(effect.job_id, JobStatus.QUEUED, error=None)
            self._queue.enqueue(effect.job_id, job.destination_id)
            resumed += 1
        result = IntentReconciliationResult(
            examined=len(effects),
            resumed=resumed,
            waiting=waiting,
            manual_review=manual,
            superseded=superseded,
        )
        for outcome, count in {
            "resumed": result.resumed,
            "waiting": result.waiting,
            "manual_review": result.manual_review,
            "superseded": result.superseded,
        }.items():
            if count:
                maintenance_reconciliation_outcomes_total.labels(outcome=outcome).inc(count)
        return result

    def _defer(
        self,
        effect: ReconciliationEffect,
        *,
        now: datetime,
        seconds: int | None = None,
    ) -> None:
        self._intents.defer_effect_probe(
            effect.receipt_id,
            next_probe_at=now + timedelta(seconds=seconds or self._probe_interval_seconds),
        )


def _try_get_destination(jobs: JobStore, destination_id: str) -> Any | None:
    getter = getattr(jobs, "try_get_destination", None)
    if callable(getter):
        return getter(destination_id)
    try:
        return jobs.get_destination(destination_id)
    except KeyError:
        return None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
