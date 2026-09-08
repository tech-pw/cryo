from __future__ import annotations

import logging
import uuid
from collections import defaultdict, deque
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol, cast

from cryo.config import get_settings
from cryo.db.repositories.run_finalizer import finalize_open_runs
from cryo.db.session import SessionLocal
from cryo.domain.enums import EngineType, JobStatus
from cryo.domain.intents import IntentStatus
from cryo.domain.models import CapacitySnapshot
from cryo.governor.admission import (
    AdmissionConfig,
    AdmissionJob,
    ReplicaBudget,
    compute_admission_budget,
    decide_job_admission,
    pack_fleet_budget,
)
from cryo.governor.native_admit import (
    _run_fallback_dispatches_concurrently,
    admit_native_k8s_jobs,
    destination_uses_k8s_dispatch,
    split_fallback_dispatch_wave,
)
from cryo.governor.prefetch import (
    batch_job_requires_approval,
    batch_pii_block_reason,
    prefetch_approved_operation_keys,
    prefetch_paused_destination_ids,
    prefetch_paused_table_ids,
    prefetch_table_stats,
)
from cryo.governor.roi_gate import GateAction, ROIGate
from cryo.observability.correlation import clear_context, set_context
from cryo.observability.metrics import (
    governor_admission_decisions,
    governor_qpp_decisions_total,
    governor_queue_depth,
    governor_wave_duration_seconds,
)
from cryo.queue.locks import LockStore
from cryo.queue.reconcile import (
    purge_stale_queue_entries,
    reconcile_deferred_jobs,
    reconcile_queued_jobs,
    reconcile_stale_running_jobs,
    reconcile_stuck_admitted_jobs,
)
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.triggers.evaluator import TriggerEvaluator
from cryo.worker.worker import DestinationConfig, JobStore, MaintenanceJobRecord

logger = logging.getLogger(__name__)


class CapacityProvider(Protocol):
    def probe(self, destination: DestinationConfig) -> Mapping[str, CapacitySnapshot | int]: ...


class StaticCapacityProvider:
    def probe(self, destination: DestinationConfig) -> Mapping[str, CapacitySnapshot | int]:
        config = destination.config
        replicas = config.get("replicas") or config.get("coordinators") or ("default",)
        if isinstance(replicas, str):
            replicas = (replicas,)
        cap = int(config.get("per_replica_cap") or config.get("slot_cap") or 5)
        return {str(replica): CapacitySnapshot(capacity=cap, used=0) for replica in replicas}


@dataclass(frozen=True)
class WaveResult:
    admitted: int = 0
    deferred: int = 0
    blocked: int = 0
    orphaned: int = 0
    re_enqueued: int = 0
    reaped: int = 0


class _PendingFallbackDispatch:
    """One native-unsupported SQL fallback still waiting for a later wave."""

    __slots__ = ("dispatch", "destination_config")

    def __init__(
        self,
        dispatch: Any,
        destination_config: Mapping[str, Any],
    ) -> None:
        self.dispatch = dispatch
        self.destination_config = destination_config


def _shadow_sampled(job_id: str, sample_rate: int) -> bool:
    """True for roughly 1-in-sample_rate job_ids, deterministically -- same job_id always
    samples the same way within a run, no per-wave counter to maintain across calls. See
    Settings.governor_shadow_sample_rate's own docstring for why this exists: the ROI-gate
    and resource-predictor shadow paths were evaluating every single candidate every wave,
    purely to log data neither currently enforces."""
    if sample_rate <= 1:
        return True
    return hash(job_id) % sample_rate == 0


def _tenant_round_robin(
    jobs: list[MaintenanceJobRecord],
) -> list[MaintenanceJobRecord]:
    groups: dict[str, deque[MaintenanceJobRecord]] = {}
    order: list[str] = []
    for job in jobs:
        tenant = job.tenant_id or "default"
        if tenant not in groups:
            groups[tenant] = deque()
            order.append(tenant)
        groups[tenant].append(job)
    fair: list[MaintenanceJobRecord] = []
    while groups:
        for tenant in tuple(order):
            group = groups.get(tenant)
            if group is None:
                continue
            fair.append(group.popleft())
            if not group:
                del groups[tenant]
                order.remove(tenant)
    return fair


class Governor:
    def __init__(
        self,
        queue: MaintenanceQueue,
        lock_store: LockStore,
        store: JobStore,
        capacity_provider: CapacityProvider | None = None,
        *,
        default_config: AdmissionConfig | None = None,
        lock_ttl_seconds: int = 6 * 60 * 60,
        trigger_evaluator: TriggerEvaluator | None = None,
        enable_signal_triggers: bool = True,
        session_factory: Any | None = SessionLocal,
    ) -> None:
        self._queue = queue
        self._lock_store = lock_store
        self._store = store
        self._capacity_provider = capacity_provider or StaticCapacityProvider()
        self._default_config = default_config or AdmissionConfig()
        self._lock_ttl_seconds = lock_ttl_seconds
        self._replica_offsets: dict[str, int] = defaultdict(int)
        # Injectable so unit tests can run fully in-memory (queue/store/lock_store are already
        # fake) without also standing up a real Postgres connection just for the stale-job
        # reaper's step-ledger lookup. None means "no step ledger available", which
        # reconcile_stale_running_jobs already treats the same as "nothing found" -- a real
        # DB-lookup failure is handled separately, defensively, inside that function itself.
        self._session_factory = session_factory
        self._trigger_evaluator = trigger_evaluator or TriggerEvaluator()
        self._enable_signal_triggers = enable_signal_triggers
        settings = get_settings()
        self._qpp_enabled = settings.qpp_enabled
        self._roi_gate_enabled = settings.roi_gate_enabled
        self._roi_gate_shadow_mode = settings.roi_gate_shadow_mode
        self._roi_gate = ROIGate()
        self._shadow_sample_rate = settings.governor_shadow_sample_rate
        # Fallbacks not yet dispatched because the current wave already spent its
        # one slot_cap-sized concurrent batch (PDAE-8352). Drained at the end of
        # subsequent run_wave ticks so regular queue admission is never blocked
        # behind a multi-batch cohort.
        self._pending_fallback_dispatches: list[_PendingFallbackDispatch] = []

    def _spark_engine_still_active(self, job: MaintenanceJobRecord) -> bool:
        """Real fix for a live incident, 2026-08-11: engine_activity_checker was declared as a
        parameter on reconcile_stale_running_jobs but never actually passed from anywhere in
        the codebase -- every RUNNING job past stale_after_seconds (default 6h) got reaped
        unconditionally, with no way to tell "worker died" from "job is huge and still going".
        Confirmed live: a 24TB rewrite (cdp.unified_bq.gold_dbt_pw_bq) legitimately still
        processing after ~5h, driver pod genuinely Running, would have been force-FAILED by
        this reaper within the hour despite doing real, uninterrupted work the whole time.

        Only implemented for spark_emr -- that's the engine large, long-running jobs actually
        route to (see cryo/engines/native/routing.py's SQL-fallback-required cases), and
        SparkK8sClient.get_job_run() already has the exact live signal this needs: the driver
        pod's own K8s phase, not the dispatch wrapper Job's, which is known unreliable
        (see get_job_run's own docstring). Any lookup failure or a destination this isn't
        built for returns False (not still active) -- matches reconcile_stale_running_jobs'
        existing conservative-but-not-silent-forever default elsewhere in this same function
        (the step-ledger check): when genuinely unsure, this reaper still reaps rather than
        leaving a truly-dead job stuck RUNNING forever, but a real live signal always wins.
        """
        if not job.engine_ref:
            return False
        destination = _try_get_destination(self._store, job.destination_id)
        if destination is None or destination.engine != EngineType.SPARK_EMR:
            return False
        try:
            from cryo.engines.spark.k8s import JobRunState
            from cryo.engines.spark.k8s_engine import SparkK8sEngine

            engine = SparkK8sEngine(config=dict(destination.config))
            result = engine.k8s_client.get_job_run(job.engine_ref)
            return result.state == JobRunState.RUNNING
        except Exception:
            logger.warning(
                "Spark engine-activity check failed for job %s (engine_ref=%s); "
                "treating as not-active",
                job.id,
                job.engine_ref,
                exc_info=True,
            )
            return False

    def run_wave(self, *, now: datetime | None = None) -> WaveResult:
        wave_start = perf_counter()
        destination_outcomes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        try:
            current = now or datetime.now(UTC)
            if self._session_factory is not None:
                finalize_open_runs(self._session_factory, now=current)
            re_enqueued = reconcile_queued_jobs(self._store, self._queue)
            re_enqueued += reconcile_deferred_jobs(self._store, self._queue, now=current)
            orphaned = purge_stale_queue_entries(self._store, self._queue)
            step_ledger = None
            if self._session_factory is not None:
                try:
                    from cryo.db.repositories.job_steps import JobStepStore

                    step_ledger = JobStepStore(self._session_factory)
                except Exception:
                    logger.debug("JobStepStore unavailable for stale reaper", exc_info=True)
            reaped = reconcile_stale_running_jobs(
                self._store,
                self._queue,
                self._lock_store,
                now=current,
                stale_after_seconds=self._lock_ttl_seconds,
                step_ledger=step_ledger,
                engine_activity_checker=self._spark_engine_still_active,
            )
            reaped += reconcile_stuck_admitted_jobs(
                self._store,
                self._queue,
                self._lock_store,
                now=current,
            )
            admitted = 0
            deferred = 0
            blocked = 0

            pending_by_destination = self._queue.pending_by_destination()
            for destination_id, job_ids in pending_by_destination.items():
                governor_queue_depth.labels(destination=destination_id).set(len(job_ids))

            for destination_id, job_ids in pending_by_destination.items():
                destination = _try_get_destination(self._store, destination_id)
                if destination is None:
                    orphan_count = self._fail_orphaned_jobs(destination_id, job_ids)
                    orphaned += orphan_count
                    destination_outcomes[destination_id]["orphaned"] += orphan_count
                    continue
                config = _admission_config(destination, self._default_config)
                live_capacity = self._capacity_provider.probe(destination)
                local_in_flight = self._queue.local_in_flight_by_replica(destination_id)
                budget = compute_admission_budget(live_capacity, local_in_flight, config)
                replicas = self._round_robin_slots(destination_id, budget.replicas)
                session_factory = self._session_factory
                session_context = (
                    session_factory() if session_factory is not None else nullcontext(None)
                )

                wave_start_replica_count = len(replicas)
                # Populated at the two direct-dispatch points below (worker-queue admit, and
                # the native-k8s-fallback-to-worker path) for the fleet-pack/dispatch-order
                # counterfactual telemetry after this destination's dispatch work is done. Not
                # populated for admit_native_k8s_jobs' own aggregate path (result.admitted is a
                # count, not individual job_ids there) -- a partial signal, not a full one.
                dispatched_job_ids: set[str] = set()
                with session_context as session:
                    wave_jobs: list[MaintenanceJobRecord] = []
                    table_uuids: set[uuid.UUID] = set()
                    native_k8s_buffer: list[MaintenanceJobRecord] = []
                    use_native_k8s = (
                        destination_uses_k8s_dispatch(destination) and session_factory is not None
                    )
                    # Inspect a bounded multiple of available slots, then round-robin that window
                    # by tenant. This preserves the 11k-fleet OOM fix while giving later tenants a
                    # chance to use capacity instead of letting one queue prefix monopolize a wave.
                    candidate_limit = (
                        len(replicas) if use_native_k8s else max(len(replicas), len(replicas) * 4)
                    )
                    # 2026-09-04 (docs/admission-ordering-and-scheduling.md): candidate_limit
                    # truncates the queue to a bounded prefix before any priority ranking runs
                    # (pack_fleet_budget, below, only ever sees this prefix) -- a genuinely
                    # high-priority job past this cutoff is structurally invisible to admission
                    # this wave, not merely deprioritized. truncated_this_wave records how many
                    # jobs never got a look (still `break`s at the same point as before -- this
                    # is a count for the telemetry emitted after the dispatch loop below, not a
                    # behavior change; still bounded, same OOM-safety this cap exists for).
                    truncated_this_wave = max(0, len(job_ids) - candidate_limit)
                    for job_id in job_ids:
                        if not replicas:
                            blocked += 1
                            destination_outcomes[destination_id]["blocked"] += 1
                            break
                        if len(wave_jobs) >= candidate_limit:
                            blocked += 1
                            destination_outcomes[destination_id]["blocked"] += 1
                            break
                        try:
                            job = self._store.get_job(job_id)
                        except KeyError:
                            logger.warning("Purging queue entry for unknown job %s", job_id)
                            self._queue.cancel_pending(job_id, destination_id)
                            orphaned += 1
                            destination_outcomes[destination_id]["orphaned"] += 1
                            continue
                        wave_jobs.append(job)
                        table_uuid = _job_table_uuid(job)
                        if table_uuid is not None:
                            table_uuids.add(table_uuid)

                    wave_jobs = _tenant_round_robin(wave_jobs)
                    effect_destination_ids = {
                        effect_destination_id
                        for job in wave_jobs
                        for effect_destination_id in job.intent_effect_destination_ids
                    }
                    approved_keys: dict[
                        tuple[uuid.UUID, str],
                        list[dict[str, Any]],
                    ]
                    stats_cache: dict[uuid.UUID, Any]
                    paused_table_ids: set[uuid.UUID]
                    paused_effect_destination_ids: set[str]
                    if session is None:
                        approved_keys = {}
                        stats_cache = {}
                        paused_table_ids = set()
                        paused_effect_destination_ids = set()
                    else:
                        approved_keys = prefetch_approved_operation_keys(
                            session,
                            table_uuids,
                        )
                        stats_cache = prefetch_table_stats(session, table_uuids)
                        paused_table_ids = prefetch_paused_table_ids(
                            session,
                            table_uuids,
                            destination_id=destination_id,
                        )
                        paused_effect_destination_ids = prefetch_paused_destination_ids(
                            session,
                            effect_destination_ids,
                        )
                    # AutoComp §A2 "Fleet Decide": computed once per destination over the whole
                    # wave, not per job, so a big low-priority job doesn't block a wave's worth
                    # of higher-MOOP-score jobs from any budget headroom it leaves behind.
                    fleet_decisions = pack_fleet_budget(
                        [_admission_job(job) for job in wave_jobs], config
                    )

                    for job in wave_jobs:
                        if not replicas:
                            break

                        set_context(
                            replace=True,
                            correlation_id=job.correlation_id or job.id,
                            run_id=job.run_id,
                            job_id=job.id,
                            intent_id=job.intent_id,
                            table_id=job.table_id,
                            destination_id=job.destination_id,
                        )
                        job_id = job.id
                        table_uuid = _job_table_uuid(job)
                        if table_uuid in paused_table_ids or any(
                            effect_destination_id in paused_effect_destination_ids
                            for effect_destination_id in job.intent_effect_destination_ids
                        ):
                            reason = "Maintenance execution is paused"
                            logger.info("Job %s deferred: %s", job_id, reason)
                            self._defer(job, reason)
                            deferred += 1
                            destination_outcomes[destination_id]["deferred"] += 1
                            continue
                        abac_blocked = False
                        if session is not None and table_uuid is not None and job.plan:
                            from cryo.security.abac_enforcement import evaluate_maintenance_allowed

                            for op in job.plan.get("operations") or []:
                                op_type = op.get("type") if isinstance(op, dict) else op
                                allowed, abac_reason = evaluate_maintenance_allowed(
                                    session,
                                    table_uuid,
                                    None,
                                    str(op_type),
                                )
                                if not allowed:
                                    logger.warning(
                                        "Deferring job %s: %s",
                                        job_id,
                                        abac_reason,
                                    )
                                    deferred += 1
                                    destination_outcomes[destination_id]["deferred"] += 1
                                    from cryo.governor.bandit_feedback import (
                                        record_deferred_admission,
                                    )

                                    record_deferred_admission(config)
                                    abac_blocked = True
                                    break
                        if abac_blocked:
                            continue

                        plan_operations = job.plan.get("operations") if job.plan else None

                        if job.intent_id is not None:
                            if job.intent_status != IntentStatus.READY.value:
                                reason = (
                                    "Durable intent is not ready "
                                    f"(status={job.intent_status or 'missing'})"
                                )
                                logger.info("Job %s deferred: %s", job_id, reason)
                                self._defer(job, reason)
                                deferred += 1
                                destination_outcomes[destination_id]["deferred"] += 1
                                continue
                            if (
                                job.intent_expires_at is None
                                or _as_utc(job.intent_expires_at) <= current
                            ):
                                reason = "Durable intent expired before admission"
                                logger.info("Job %s deferred: %s", job_id, reason)
                                self._defer(job, reason)
                                deferred += 1
                                destination_outcomes[destination_id]["deferred"] += 1
                                continue

                        signal_triggered = False
                        if (
                            session is not None
                            and self._enable_signal_triggers
                            and table_uuid is not None
                        ):
                            try:
                                trigger_result = self._trigger_evaluator.evaluate(
                                    session,
                                    table_uuid,
                                    is_due_for_schedule=False,
                                )
                                if (
                                    trigger_result.should_run
                                    and trigger_result.trigger_type.value != "schedule"
                                ):
                                    signal_triggered = True
                            except Exception as exc:
                                logger.warning(
                                    "Trigger evaluation failed for job %s table %s: %s",
                                    job_id,
                                    table_uuid,
                                    exc,
                                )

                        if (
                            session is not None
                            and job.intent_id is None
                            and table_uuid is not None
                            and batch_job_requires_approval(
                                table_uuid, plan_operations, approved_keys
                            )
                        ):
                            logger.info(
                                "Job %s deferred: destructive operation requires approval",
                                job_id,
                            )
                            # Surface the block, not just log it -- see
                            # ensure_pending_approval_requests's own docstring for the
                            # incident this fixes. Best-effort: a failure creating the
                            # request must never block the defer itself.
                            try:
                                from cryo.workflows.approvals import (
                                    ensure_pending_approval_requests,
                                )

                                ensure_pending_approval_requests(
                                    session, table_uuid, plan_operations
                                )
                            except Exception as exc:
                                logger.warning(
                                    "Failed to surface approval request for job %s table %s: %s",
                                    job_id,
                                    table_uuid,
                                    exc,
                                )
                            self._defer(job, "Destructive operation requires approval")
                            deferred += 1
                            destination_outcomes[destination_id]["deferred"] += 1
                            continue

                        if session is not None and table_uuid is not None:
                            try:
                                pii_reason = batch_pii_block_reason(
                                    session,
                                    table_uuid,
                                    plan_operations,
                                    job_params=job.params,
                                    stats_cache=stats_cache,
                                )
                                if pii_reason:
                                    logger.info("Job %s deferred: %s", job_id, pii_reason)
                                    self._defer(job, pii_reason)
                                    deferred += 1
                                    destination_outcomes[destination_id]["deferred"] += 1
                                    continue
                            except Exception as exc:
                                logger.warning(
                                    "PII gate check failed for job %s table %s: %s",
                                    job_id,
                                    table_uuid,
                                    exc,
                                )
                                self._defer(job, "pii_gate_check_failed")
                                deferred += 1
                                destination_outcomes[destination_id]["deferred"] += 1
                                continue

                        if not signal_triggered and not job.force_admit:
                            fleet_decision = fleet_decisions.get(job.id)
                            if fleet_decision is not None and not fleet_decision.admissible:
                                from cryo.governor.bandit_feedback import (
                                    record_deferred_admission,
                                )

                                record_deferred_admission(config)
                                self._defer(job, fleet_decision.reason)
                                deferred += 1
                                destination_outcomes[destination_id]["deferred"] += 1
                                continue

                            decision = decide_job_admission(
                                _admission_job(job), config, now=current
                            )
                            if not decision.admissible:
                                if decision.defer:
                                    from cryo.governor.bandit_feedback import (
                                        record_deferred_admission,
                                    )

                                    record_deferred_admission(config)
                                    self._defer(job, decision.reason)
                                    deferred += 1
                                    destination_outcomes[destination_id]["deferred"] += 1
                                else:
                                    blocked += 1
                                    destination_outcomes[destination_id]["blocked"] += 1
                                continue

                        # In shadow mode (roi_gate_shadow_mode), decide() only ever logs -- the
                        # SKIP/DEFER branches below never fire (a shadow decision always takes
                        # the `roi.shadow: log` branch and falls through). Sampling here is
                        # therefore safe: it skips real enforcement work zero times, only
                        # redundant logging-only evaluations. Once real enforcement is live
                        # (roi_gate_shadow_mode=False), every candidate is evaluated regardless
                        # of sample_rate -- the gate must never silently skip a real SKIP/DEFER
                        # decision.
                        roi = None
                        if self._roi_gate_enabled and session is not None:
                            skip_for_sampling = self._roi_gate_shadow_mode and not _shadow_sampled(
                                job_id, self._shadow_sample_rate
                            )
                            if not skip_for_sampling:
                                roi = self._roi_gate.decide(session, _admission_job(job))
                        if roi is not None and roi.shadow:
                            logger.info(
                                "ROI gate shadow: job %s action=%s bcr=%.4f threshold=%.4f",
                                job_id,
                                roi.action.value,
                                roi.bcr,
                                roi.threshold,
                            )
                        elif roi is not None and roi.action == GateAction.SKIP:
                            self._queue.cancel_pending(job.id, destination_id)
                            self._store.mark_status(job.id, JobStatus.SKIPPED, error=roi.reason)
                            blocked += 1
                            destination_outcomes[destination_id]["blocked"] += 1
                            continue
                        elif roi is not None and roi.action == GateAction.DEFER:
                            self._defer(job, roi.reason)
                            deferred += 1
                            destination_outcomes[destination_id]["deferred"] += 1
                            continue

                        if not self._lock_store.acquire_table_lock(
                            job.table_id,
                            job.id,
                            self._lock_ttl_seconds,
                        ):
                            self._defer(job, "table_locked")
                            deferred += 1
                            destination_outcomes[destination_id]["deferred"] += 1
                            continue

                        if self._qpp_enabled:
                            try:
                                qpp_admit, predicted_p95_ms = _evaluate_qpp_for_governor(
                                    table_uuid=table_uuid,
                                    stats_cache=stats_cache,
                                    live_capacity=live_capacity,
                                    job=job,
                                )
                                if not qpp_admit:
                                    logger.info(
                                        "Job %s deferred: QPP refused (p95=%.0fms)",
                                        job_id,
                                        predicted_p95_ms,
                                    )
                                    governor_qpp_decisions_total.labels(
                                        destination=destination_id,
                                        outcome="refused",
                                    ).inc()
                                    self._lock_store.release_table_lock(job.table_id, job.id)
                                    self._defer(job, "qpp_refused")
                                    deferred += 1
                                    destination_outcomes[destination_id]["deferred"] += 1
                                    continue
                                governor_qpp_decisions_total.labels(
                                    destination=destination_id,
                                    outcome="confirmed",
                                ).inc()
                            except Exception as exc:
                                logger.warning(
                                    "QPP gate failed for job %s table %s: %s",
                                    job_id,
                                    table_uuid,
                                    exc,
                                )
                                governor_qpp_decisions_total.labels(
                                    destination=destination_id,
                                    outcome="error",
                                ).inc()

                        # Resource predictor runs in shadow mode first: log recommendations and
                        # compare against static sizing before any enforcement. No enforcement
                        # path exists yet at all -- this block only ever logs -- so sampling
                        # (see _shadow_sampled's own docstring) is unconditionally safe here,
                        # unlike the ROI gate above which still evaluates every candidate once
                        # real enforcement is live.
                        if _shadow_sampled(job_id, self._shadow_sample_rate):
                            try:
                                from cryo.ai.resource_predictor import predict_resources_for_table

                                if session is None:
                                    raise ValueError("resource predictor has no database session")
                                if table_uuid is None:
                                    raise ValueError("job has no valid table UUID")
                                sizing = predict_resources_for_table(session, table_uuid)
                                logger.info(
                                    "Resource predictor (shadow) for table %s: cpu=%.1f "
                                    "mem=%.1fGiB p50=%.1fs p90=%.1fs conf=%.2f samples=%d "
                                    "static_cap=%d",
                                    table_uuid,
                                    sizing.recommended_cpu_cores,
                                    sizing.recommended_memory_gb,
                                    sizing.predicted_p50_seconds,
                                    sizing.predicted_p90_seconds,
                                    sizing.confidence,
                                    sizing.samples,
                                    config.per_replica_cap,
                                )
                            except Exception as exc:
                                logger.debug(
                                    "Resource predictor shadow failed for %s: %s",
                                    table_uuid,
                                    exc,
                                )

                        if use_native_k8s:
                            # Buffer for pack-on-admit; do not push to Worker admitted queue.
                            native_k8s_buffer.append(job)
                            continue

                        replica = replicas.pop(0)
                        self._queue.admit(job.id, destination_id, replica)
                        self._store.mark_status(job.id, JobStatus.ADMITTED, replica=replica)
                        from cryo.governor.bandit_feedback import register_admission

                        register_admission(job.id, config)
                        admitted += 1
                        destination_outcomes[destination_id]["admitted"] += 1
                        dispatched_job_ids.add(job.id)

                    if native_k8s_buffer:
                        assert session_factory is not None

                        def _defer_native_job(jid: str, reason: str) -> None:
                            self._queue.defer(jid)
                            self._store.mark_status(jid, JobStatus.DEFERRED, error=reason)

                        result = admit_native_k8s_jobs(
                            jobs=native_k8s_buffer,
                            destination=destination,
                            store=self._store,
                            session_factory=session_factory,
                            live_capacity=live_capacity,
                            stats_cache=stats_cache,
                            mark_status=self._store.mark_status,
                            cancel_pending=self._queue.cancel_pending,
                            defer_job=_defer_native_job,
                            release_lock=self._lock_store.release_table_lock,
                        )
                        if result.fallback_to_worker:
                            for job in native_k8s_buffer:
                                if not replicas:
                                    self._defer(job, "no_replica_slots")
                                    deferred += 1
                                    destination_outcomes[destination_id]["deferred"] += 1
                                    continue
                                replica = replicas.pop(0)
                                self._queue.admit(job.id, destination_id, replica)
                                self._store.mark_status(job.id, JobStatus.ADMITTED, replica=replica)
                                from cryo.governor.bandit_feedback import register_admission

                                register_admission(job.id, config)
                                admitted += 1
                                destination_outcomes[destination_id]["admitted"] += 1
                                dispatched_job_ids.add(job.id)
                        else:
                            admitted += result.admitted
                            deferred += result.deferred
                            destination_outcomes[destination_id]["admitted"] += result.admitted
                            destination_outcomes[destination_id]["deferred"] += result.deferred
                            from cryo.governor.bandit_feedback import register_admission

                            for job in native_k8s_buffer:
                                register_admission(job.id, config)
                            self._enqueue_fallback_dispatches(
                                result.pending_fallback_dispatches,
                                destination.config,
                            )

                    # 2026-09-04 (docs/admission-ordering-and-scheduling.md, "Consequences"):
                    # fleet_decisions ranks candidates by moop_score but dispatch order above
                    # actually follows _tenant_round_robin's fairness interleaving, not that
                    # rank -- so a job the fleet-packer ranked highly can still lose its replica
                    # slot to a lower-ranked job that happened to come first in round-robin
                    # order. Pure read-only counterfactual, computed after dispatch so it can
                    # never affect what was actually admitted: how many of fleet_decisions' own
                    # top-N admissible jobs (N = replicas available at the start of this
                    # destination's wave) were NOT among the jobs this wave actually dispatched.
                    # Exists to measure how often this mismatch happens before deciding whether
                    # dispatch order is worth changing -- see the doc's Phase S0.
                    try:
                        if fleet_decisions and wave_start_replica_count > 0:
                            fleet_admissible = {
                                jid for jid, dec in fleet_decisions.items() if dec.admissible
                            }

                            def _rank_key(j: MaintenanceJobRecord) -> float:
                                aj = _admission_job(j)
                                if aj.moop_score is not None:
                                    return aj.moop_score
                                if aj.estimated_benefit_score is not None:
                                    return aj.estimated_benefit_score
                                return float("-inf")

                            fleet_ranked = sorted(
                                (j for j in wave_jobs if j.id in fleet_admissible),
                                key=_rank_key,
                                reverse=True,
                            )
                            top_n_ids = {j.id for j in fleet_ranked[:wave_start_replica_count]}
                            missed = top_n_ids - dispatched_job_ids
                            if missed:
                                logger.info(
                                    "Fleet-pack/dispatch-order mismatch for %s: %d of the top "
                                    "%d fleet-ranked job(s) were not dispatched this wave "
                                    "(round-robin order took priority over fleet rank)",
                                    destination_id,
                                    len(missed),
                                    wave_start_replica_count,
                                )
                    except Exception as exc:  # noqa: BLE001 - telemetry only, must never affect dispatch
                        logger.debug("Fleet-pack counterfactual telemetry failed: %s", exc)

            self._run_one_fallback_dispatch_wave()

            return WaveResult(
                admitted=admitted,
                deferred=deferred,
                blocked=blocked,
                orphaned=orphaned,
                re_enqueued=re_enqueued,
                reaped=reaped,
            )
        finally:
            for destination_id, outcomes in destination_outcomes.items():
                for outcome, count in outcomes.items():
                    if count:
                        governor_admission_decisions.labels(
                            destination=destination_id,
                            outcome=outcome,
                        ).inc(count)
            governor_wave_duration_seconds.observe(perf_counter() - wave_start)
            clear_context()

    def _enqueue_fallback_dispatches(
        self,
        dispatches: tuple[Any, ...],
        destination_config: Mapping[str, Any],
    ) -> None:
        if not dispatches:
            return
        cfg = dict(destination_config)
        for dispatch in dispatches:
            self._pending_fallback_dispatches.append(_PendingFallbackDispatch(dispatch, cfg))

    def _run_one_fallback_dispatch_wave(self) -> None:
        """Dispatch at most one slot_cap concurrent batch, then yield.

        Regular async-queue admission already ran for every destination this tick.
        Remaining fallbacks stay on ``_pending_fallback_dispatches`` for later waves.
        """
        pending = self._pending_fallback_dispatches
        if not pending:
            return
        lead_cfg = pending[0].destination_config
        lead_key = lead_cfg.get("sql_fallback_destination_id")
        matching: list[Any] = []
        rest: list[_PendingFallbackDispatch] = []
        for item in pending:
            same = item.destination_config.get("sql_fallback_destination_id") == lead_key
            if same:
                matching.append(item.dispatch)
            else:
                rest.append(item)
        this_wave, later = split_fallback_dispatch_wave(
            matching,
            destination_config=lead_cfg,
            store=self._store,
        )
        self._pending_fallback_dispatches = [
            _PendingFallbackDispatch(dispatch, lead_cfg) for dispatch in later
        ] + rest
        if this_wave:
            _run_fallback_dispatches_concurrently(
                this_wave,
                destination_config=lead_cfg,
                store=self._store,
            )

    def _fail_orphaned_jobs(self, destination_id: str, job_ids: list[str]) -> int:
        logger.warning(
            "Purging %d queued job(s) for missing destination %s",
            len(job_ids),
            destination_id,
        )
        failed = 0
        for job_id in job_ids:
            self._queue.cancel_pending(job_id, destination_id)
            if not _is_valid_job_id(job_id):
                failed += 1
                continue
            try:
                self._store.mark_status(job_id, JobStatus.FAILED, error="destination_not_found")
                failed += 1
            except KeyError:
                logger.debug("Skipping orphan queue entry with no DB job record: %s", job_id)
        return failed

    def _round_robin_slots(
        self,
        destination_id: str,
        budgets: tuple[ReplicaBudget, ...],
    ) -> list[str]:
        expanded: list[str] = []
        replicas_with_slots: list[tuple[str, int]] = [
            (budget.replica, budget.available) for budget in budgets
        ]
        replicas_with_slots = [
            (replica, available) for replica, available in replicas_with_slots if available > 0
        ]
        if not replicas_with_slots:
            return []

        offset = self._replica_offsets[destination_id] % len(replicas_with_slots)
        ordered = replicas_with_slots[offset:] + replicas_with_slots[:offset]
        remaining = dict(ordered)
        index = 0
        while any(count > 0 for count in remaining.values()):
            replica = ordered[index % len(ordered)][0]
            if remaining[replica] > 0:
                expanded.append(replica)
                remaining[replica] -= 1
            index += 1

        self._replica_offsets[destination_id] = (offset + 1) % len(replicas_with_slots)
        return expanded

    def _defer(self, job: MaintenanceJobRecord, reason: str) -> None:
        self._queue.defer(job.id)
        self._store.mark_status(job.id, JobStatus.DEFERRED, error=reason)


def _admission_job(job: MaintenanceJobRecord) -> AdmissionJob:
    params = dict(job.params) if job.params else {}
    estimated_benefit = params.get("min_expected_benefit_score")
    if estimated_benefit is None:
        estimated_benefit = params.get("benefit_score")
    moop_score = params.get("moop_score")
    size_gb = params.get("size_gb") or params.get("total_size_gb")
    return AdmissionJob(
        job_id=job.id,
        table_id=job.table_id,
        destination_id=job.destination_id,
        mode=job.mode,
        cooldown_hours=job.cooldown_hours,
        estimated_benefit_score=float(estimated_benefit) if estimated_benefit is not None else None,
        moop_score=float(moop_score) if moop_score is not None else None,
        size_gb=float(size_gb) if size_gb is not None else None,
        last_maintained_at=job.last_maintained_at,
        deadline_at=job.deadline_at,
        force_admit=job.force_admit,
    )


def _try_get_destination(store: JobStore, destination_id: str) -> DestinationConfig | None:
    getter = getattr(store, "try_get_destination", None)
    if callable(getter):
        return cast(DestinationConfig | None, getter(destination_id))
    try:
        return store.get_destination(destination_id)
    except KeyError:
        return None


def _is_valid_job_id(job_id: str) -> bool:
    return bool(job_id) and job_id.lower() != "none"


def _job_table_uuid(job: MaintenanceJobRecord) -> uuid.UUID | None:
    try:
        raw = job.table_id
        return uuid.UUID(raw) if isinstance(raw, str) else raw
    except (ValueError, AttributeError, TypeError):
        return None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _admission_config(destination: DestinationConfig, default: AdmissionConfig) -> AdmissionConfig:

    from cryo.db.session import SessionLocal
    from cryo.governor.bandit_store import load_bandit

    config = _admission_config_from_destination(destination, default)
    try:
        with SessionLocal() as session:
            bandit = load_bandit(session)
    except Exception as exc:
        logger.error(
            "Bandit state unavailable from Postgres; using static admission config: %s", exc
        )
        return config
    tuned, confident = bandit.suggest_config(config)
    return tuned if confident else config


def _bandit_path() -> Path:
    import os

    return Path(os.environ.get("CRYO_STATE_DIR", "/tmp/cryo")) / "admission_bandit.json"


def _admission_config_from_destination(
    destination: DestinationConfig, default: AdmissionConfig
) -> AdmissionConfig:
    config = destination.config
    per_replica_cap = config.get("per_replica_cap", config.get("slot_cap", default.per_replica_cap))
    return AdmissionConfig(
        offpeak_start_ist=int(config.get("offpeak_start_ist", default.offpeak_start_ist)),
        offpeak_end_ist=int(config.get("offpeak_end_ist", default.offpeak_end_ist)),
        per_replica_cap=int(per_replica_cap),
        wave_wait_sec=int(config.get("wave_wait_sec", default.wave_wait_sec)),
        adaptive_budget=bool(config.get("adaptive_budget", default.adaptive_budget)),
        high_load_ratio=float(config.get("high_load_ratio", default.high_load_ratio)),
        high_load_fraction=float(config.get("high_load_fraction", default.high_load_fraction)),
        default_cooldown_hours=int(
            config.get("default_cooldown_hours", default.default_cooldown_hours)
        ),
        min_benefit_score=float(config.get("min_benefit_score", default.min_benefit_score)),
        min_moop_score=(
            float(config["min_moop_score"])
            if config.get("min_moop_score") is not None
            else default.min_moop_score
        ),
        fleet_budget_gb=(
            float(config["fleet_budget_gb"])
            if config.get("fleet_budget_gb") is not None
            else default.fleet_budget_gb
        ),
    )


def _cotenant_load_pct(live_capacity: Mapping[str, object]) -> float:
    """Average load fraction across replicas, for the QPP gate."""
    if not live_capacity:
        return 0.0
    total = 0.0
    count = 0
    for _replica, snapshot in live_capacity.items():
        if isinstance(snapshot, CapacitySnapshot):
            used = max(snapshot.used, snapshot.queued)
            if snapshot.capacity > 0:
                total += used / snapshot.capacity
                count += 1
        elif isinstance(snapshot, (int, float)) and int(snapshot) > 0:
            total += int(snapshot) / 100
            count += 1
    return total / count if count else 0.0


def _evaluate_qpp_for_governor(
    *,
    table_uuid: uuid.UUID | None,
    stats_cache: Mapping[uuid.UUID, object],
    live_capacity: Mapping[str, object],
    job: MaintenanceJobRecord,
) -> tuple[bool, float]:
    """Run the variance-aware QPP gate for one governor admission decision.

    Returns ``(admit, predicted_p95_ms)``. ``admit=False`` means the job should be deferred.
    """
    from cryo.config import get_settings
    from cryo.governor.capacity import evaluate_qpp_for_job

    settings = get_settings()
    if not settings.qpp_enabled:
        return True, 0.0

    files_scanned = 0
    partition_count = 0
    if table_uuid is not None:
        stats = stats_cache.get(table_uuid)
        if stats is not None:
            files_scanned = getattr(stats, "total_files", 0) or 0
            partition_count = getattr(stats, "total_partitions", 0) or 0

    cotenant_load = _cotenant_load_pct(live_capacity)

    return evaluate_qpp_for_job(
        table_id=table_uuid,
        files_scanned=files_scanned,
        partition_count=partition_count,
        cotenant_load_pct=cotenant_load,
        target_p95_ms=settings.qpp_target_p95_ms,
        is_cached=False,
        query_text=None,
        model=None,
    )


def main() -> None:
    raise SystemExit("Governor core is library-only in this phase.")


if __name__ == "__main__":
    main()
