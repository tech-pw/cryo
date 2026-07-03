from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from cryo.domain.enums import JobStatus
from cryo.domain.models import CapacitySnapshot
from cryo.governor.admission import (
    AdmissionConfig,
    AdmissionJob,
    ReplicaBudget,
    compute_admission_budget,
    decide_job_admission,
)
from cryo.queue.locks import LockStore
from cryo.queue.redis_queue import MaintenanceQueue
from cryo.worker.worker import DestinationConfig, JobStore, MaintenanceJobRecord


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
    ) -> None:
        self._queue = queue
        self._lock_store = lock_store
        self._store = store
        self._capacity_provider = capacity_provider or StaticCapacityProvider()
        self._default_config = default_config or AdmissionConfig()
        self._lock_ttl_seconds = lock_ttl_seconds
        self._replica_offsets: dict[str, int] = defaultdict(int)

    def run_wave(self, *, now: datetime | None = None) -> WaveResult:
        current = now or datetime.now(UTC)
        admitted = 0
        deferred = 0
        blocked = 0

        for destination_id, job_ids in self._queue.pending_by_destination().items():
            destination = self._store.get_destination(destination_id)
            config = _admission_config(destination, self._default_config)
            live_capacity = self._capacity_provider.probe(destination)
            local_in_flight = self._queue.local_in_flight_by_replica(destination_id)
            budget = compute_admission_budget(live_capacity, local_in_flight, config)
            replicas = self._round_robin_slots(destination_id, budget.replicas)

            for job_id in job_ids:
                if not replicas:
                    blocked += 1
                    break

                job = self._store.get_job(job_id)
                decision = decide_job_admission(_admission_job(job), config, now=current)
                if not decision.admissible:
                    if decision.defer:
                        self._defer(job, decision.reason)
                        deferred += 1
                    else:
                        blocked += 1
                    continue

                if not self._lock_store.acquire_table_lock(
                    job.table_id,
                    job.id,
                    self._lock_ttl_seconds,
                ):
                    self._defer(job, "table_locked")
                    deferred += 1
                    continue

                replica = replicas.pop(0)
                self._queue.admit(job.id, destination_id, replica)
                self._store.mark_status(job.id, JobStatus.ADMITTED, replica=replica)
                admitted += 1

        return WaveResult(admitted=admitted, deferred=deferred, blocked=blocked)

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


def _admission_config(destination: DestinationConfig, default: AdmissionConfig) -> AdmissionConfig:
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
