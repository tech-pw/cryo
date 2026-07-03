from __future__ import annotations

from datetime import UTC, datetime

from cryo.domain.enums import JobStatus, Mode
from cryo.governor.admission import AdmissionConfig
from cryo.governor.governor import Governor
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import DestinationConfig, InMemoryJobStore, MaintenanceJobRecord


def test_in_memory_queue_tracks_priority_admission_claim_and_inflight() -> None:
    queue = InMemoryMaintenanceQueue()

    queue.enqueue("low", "dest", priority=1)
    queue.enqueue("high", "dest", priority=10)

    assert queue.pending("dest") == ["high", "low"]
    queue.admit("high", "dest", "r1")
    assert queue.local_in_flight_by_replica("dest") == {"r1": 1}
    assert queue.claim_admitted() == "high"

    queue.mark_running("high")
    assert queue.status("high") is JobStatus.RUNNING
    queue.complete("high", JobStatus.SUCCEEDED)
    assert queue.local_in_flight("dest") == 0
    assert queue.status("high") is JobStatus.SUCCEEDED


def test_governor_admits_wave_round_robin_across_replicas() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "dest",
            config={
                "replicas": ["r1", "r2"],
                "per_replica_cap": 1,
                "offpeak_start_ist": 0,
                "offpeak_end_ist": 7,
                "adaptive_budget": False,
            },
        )
    )
    for job_id, table_id in [("job-1", "table-1"), ("job-2", "table-2")]:
        store.add_job(
            MaintenanceJobRecord(
                id=job_id,
                run_id="run-1",
                table_id=table_id,
                destination_id="dest",
                mode=Mode.STANDARD,
            )
        )
        queue.enqueue(job_id, "dest")

    governor = Governor(queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False))
    result = governor.run_wave(now=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))

    assert result.admitted == 2
    assert store.get_job("job-1").status is JobStatus.ADMITTED
    assert store.get_job("job-2").status is JobStatus.ADMITTED
    assert [queue.entry("job-1").replica, queue.entry("job-2").replica] == ["r1", "r2"]
    assert queue.local_in_flight_by_replica("dest") == {"r1": 1, "r2": 1}
    assert locks.is_table_locked("table-1")
    assert locks.is_table_locked("table-2")


def test_governor_defers_heavy_jobs_outside_offpeak() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "dest",
            config={
                "replicas": ["r1"],
                "per_replica_cap": 1,
                "offpeak_start_ist": 0,
                "offpeak_end_ist": 7,
            },
        )
    )
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
        )
    )
    queue.enqueue("job-1", "dest")

    result = Governor(queue, locks, store).run_wave(now=datetime(2026, 6, 30, 8, 0, tzinfo=UTC))

    assert result.deferred == 1
    assert store.get_job("job-1").status is JobStatus.DEFERRED
    assert store.get_job("job-1").error == "outside_offpeak"
