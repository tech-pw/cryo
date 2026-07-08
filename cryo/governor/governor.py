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
)
from cryo.governor.prefetch import (
    batch_job_requires_approval,
    batch_pii_block_reason,
    prefetch_approved_operation_keys,
    prefetch_table_stats,
)
from cryo.observability.correlation import set_job_id
from cryo.observability.metrics import (
    governor_admission_decisions,
    governor_queue_depth,
    governor_wave_duration_seconds,
)
from cryo.queue.locks import LockStore
from cryo.queue.reconcile import (
    purge_stale_queue_entries,
    reconcile_deferred_jobs,
    reconcile_queued_jobs,
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

    def run_wave(self, *, now: datetime | None = None) -> WaveResult:
        wave_start = perf_counter()
        destination_outcomes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        try:
            finalize_open_runs(self._session_factory)
            current = now or datetime.now(UTC)
            re_enqueued = reconcile_queued_jobs(self._store, self._queue)
            re_enqueued += reconcile_deferred_jobs(self._store, self._queue, now=current)
            orphaned = purge_stale_queue_entries(self._store, self._queue)
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

                        if not signal_triggered:
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

                        if not self._lock_store.acquire_table_lock(
                            job.table_id,
                            job.id,
                            self._lock_ttl_seconds,
                        ):
                            self._defer(job, "table_locked")
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

            return WaveResult(
                admitted=admitted,
                deferred=deferred,
                blocked=blocked,
                orphaned=orphaned,
                re_enqueued=re_enqueued,
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
    return AdmissionJob(
        job_id=job.id,
        table_id=job.table_id,
        destination_id=job.destination_id,
        mode=job.mode,
        cooldown_hours=job.cooldown_hours,
        last_maintained_at=job.last_maintained_at,
        deadline_at=job.deadline_at,
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
    )


def main() -> None:
    raise SystemExit("Governor core is library-only in this phase.")


if __name__ == "__main__":
    main()
