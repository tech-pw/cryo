from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol, cast

from cryo.config import get_settings
from cryo.db.repositories.run_finalizer import finalize_open_runs
from cryo.db.session import SessionLocal
from cryo.domain.enums import JobStatus
from cryo.domain.models import CapacitySnapshot
from cryo.governor.admission import (
    AdmissionConfig,
    AdmissionJob,
    ReplicaBudget,
    compute_admission_budget,
    decide_job_admission,
    pack_fleet_budget,
)
from cryo.governor.native_admit import admit_native_k8s_jobs, destination_uses_k8s_dispatch
from cryo.governor.prefetch import (
    batch_job_requires_approval,
    batch_pii_block_reason,
    prefetch_approved_operation_keys,
    prefetch_table_stats,
)
from cryo.governor.roi_gate import GateAction, ROIGate
from cryo.observability.correlation import set_job_id
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
    ) -> None:
        self._queue = queue
        self._lock_store = lock_store
        self._store = store
        self._capacity_provider = capacity_provider or StaticCapacityProvider()
        self._default_config = default_config or AdmissionConfig()
        self._lock_ttl_seconds = lock_ttl_seconds
        self._replica_offsets: dict[str, int] = defaultdict(int)
        self._session_factory = SessionLocal
        self._trigger_evaluator = trigger_evaluator or TriggerEvaluator()
        self._enable_signal_triggers = enable_signal_triggers
        settings = get_settings()
        self._qpp_enabled = settings.qpp_enabled
        self._roi_gate_enabled = settings.roi_gate_enabled
        self._roi_gate = ROIGate()

    def run_wave(self, *, now: datetime | None = None) -> WaveResult:
        wave_start = perf_counter()
        destination_outcomes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        try:
            finalize_open_runs(self._session_factory)
            current = now or datetime.now(UTC)
            re_enqueued = reconcile_queued_jobs(self._store, self._queue)
            re_enqueued += reconcile_deferred_jobs(self._store, self._queue, now=current)
            orphaned = purge_stale_queue_entries(self._store, self._queue)
            step_ledger = None
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

                with self._session_factory() as session:
                    wave_jobs: list[MaintenanceJobRecord] = []
                    table_uuids: set[uuid.UUID] = set()
                    native_k8s_buffer: list[MaintenanceJobRecord] = []
                    use_native_k8s = destination_uses_k8s_dispatch(destination)
                    for job_id in job_ids:
                        if not replicas:
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

                    approved_keys = prefetch_approved_operation_keys(session, table_uuids)
                    stats_cache = prefetch_table_stats(session, table_uuids)
                    # AutoComp §A2 "Fleet Decide": computed once per destination over the whole
                    # wave, not per job, so a big low-priority job doesn't block a wave's worth
                    # of higher-MOOP-score jobs from any budget headroom it leaves behind.
                    fleet_decisions = pack_fleet_budget(
                        [_admission_job(job) for job in wave_jobs], config
                    )

                    for job in wave_jobs:
                        if not replicas:
                            break

                        set_job_id(job.id)
                        job_id = job.id
                        table_uuid = _job_table_uuid(job)
                        abac_blocked = False
                        if table_uuid is not None and job.plan:
                            from cryo.security.abac_enforcement import evaluate_maintenance_allowed

                            for op in job.plan.get("operations") or []:
                                op_type = op.get("type") if isinstance(op, dict) else op
                                allowed, reason = evaluate_maintenance_allowed(
                                    session,
                                    table_uuid,
                                    None,
                                    str(op_type),
                                )
                                if not allowed:
                                    logger.warning("Deferring job %s: %s", job_id, reason)
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

                        signal_triggered = False
                        if self._enable_signal_triggers and table_uuid is not None:
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
                            not signal_triggered
                            and table_uuid is not None
                            and batch_job_requires_approval(
                                table_uuid, plan_operations, approved_keys
                            )
                        ):
                            logger.info(
                                "Job %s deferred: destructive operation requires approval",
                                job_id,
                            )
                            self._defer(job, "Destructive operation requires approval")
                            deferred += 1
                            destination_outcomes[destination_id]["deferred"] += 1
                            continue

                        if table_uuid is not None:
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

                        if self._roi_gate_enabled:
                            roi = self._roi_gate.decide(session, _admission_job(job))
                            if roi.shadow:
                                logger.info(
                                    "ROI gate shadow: job %s action=%s bcr=%.4f threshold=%.4f",
                                    job_id,
                                    roi.action.value,
                                    roi.bcr,
                                    roi.threshold,
                                )
                            elif roi.action == GateAction.SKIP:
                                self._queue.cancel_pending(job.id, destination_id)
                                self._store.mark_status(job.id, JobStatus.SKIPPED, error=roi.reason)
                                blocked += 1
                                destination_outcomes[destination_id]["blocked"] += 1
                                continue
                            elif roi.action == GateAction.DEFER:
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
                        # compare against static sizing before any enforcement.
                        try:
                            from cryo.ai.resource_predictor import predict_resources_for_table

                            if table_uuid is None:
                                raise ValueError("job has no valid table UUID")
                            sizing = predict_resources_for_table(session, table_uuid)
                            logger.info(
                                "Resource predictor (shadow) for table %s: cpu=%.1f mem=%.1fGiB "
                                "p50=%.1fs p90=%.1fs conf=%.2f samples=%d static_cap=%d",
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

                    if native_k8s_buffer:
                        def _defer_native_job(jid: str, reason: str) -> None:
                            self._queue.defer(jid)
                            self._store.mark_status(jid, JobStatus.DEFERRED, error=reason)

                        result = admit_native_k8s_jobs(
                            jobs=native_k8s_buffer,
                            destination=destination,
                            store=self._store,
                            session_factory=self._session_factory,
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
                                self._store.mark_status(
                                    job.id, JobStatus.ADMITTED, replica=replica
                                )
                                from cryo.governor.bandit_feedback import register_admission

                                register_admission(job.id, config)
                                admitted += 1
                                destination_outcomes[destination_id]["admitted"] += 1
                        else:
                            admitted += result.admitted
                            deferred += result.deferred
                            destination_outcomes[destination_id]["admitted"] += result.admitted
                            destination_outcomes[destination_id]["deferred"] += result.deferred
                            from cryo.governor.bandit_feedback import register_admission

                            for job in native_k8s_buffer:
                                register_admission(job.id, config)

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
