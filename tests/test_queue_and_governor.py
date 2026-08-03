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


def test_in_memory_queue_cancel_pending_removes_stale_entry_without_job_hash() -> None:
    queue = InMemoryMaintenanceQueue()
    queue._pending["missing-dest"].append((0, 1, "None"))

    queue.cancel_pending("None")

    assert queue.pending("missing-dest") == []


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


def test_governor_fleet_budget_admits_highest_moop_score_first() -> None:
    """AutoComp §A2 'Fleet Decide' wired into the real wave loop: enough replica slots for
    both jobs, but fleet_budget_gb only has room for one -- the higher moop_score table wins,
    the other is deferred with budget_exhausted, not silently admitted anyway."""
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "dest",
            config={
                "replicas": ["r1", "r2"],
                "per_replica_cap": 5,
                "adaptive_budget": False,
                "fleet_budget_gb": 10.0,
            },
        )
    )
    store.add_job(
        MaintenanceJobRecord(
            id="cold-job",
            run_id="run-1",
            table_id="cold-table",
            destination_id="dest",
            mode=Mode.LIGHT,
            params={"moop_score": 0.1, "size_gb": 10.0},
        )
    )
    store.add_job(
        MaintenanceJobRecord(
            id="hot-job",
            run_id="run-1",
            table_id="hot-table",
            destination_id="dest",
            mode=Mode.LIGHT,
            params={"moop_score": 0.9, "size_gb": 10.0},
        )
    )
    queue.enqueue("cold-job", "dest")
    queue.enqueue("hot-job", "dest")

    governor = Governor(queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False))
    result = governor.run_wave(now=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))

    assert result.admitted == 1
    assert result.deferred == 1
    assert store.get_job("hot-job").status is JobStatus.ADMITTED
    assert store.get_job("cold-job").status is JobStatus.DEFERRED
    assert store.get_job("cold-job").error == "budget_exhausted"


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


def test_governor_bounds_native_k8s_scan_to_replica_budget_not_full_queue() -> None:
    """Regression, 2026-08-03: a cold-table fleet run queued 11,252 jobs against a single
    native k8s_dispatch destination. Neither of run_wave's two per-destination passes over
    job_ids/wave_jobs is naturally bounded for native jobs -- their own `if not replicas`
    throttle only trips when something pops `replicas`, which never happens on the native
    path (native jobs are packed on admit, not assigned a worker replica). So both passes
    walked the *entire* queue every 30s tick regardless of capacity: one store.get_job() call
    each in the first pass, plus a resource-predictor DB query each in the second. That
    OOMKilled the Governor at 11k+ depth and, even after a memory bump, still throttled
    admission to a fraction of what slot_cap should allow at 500 (deferred=18 of 20 in this
    exact scenario pre-fix -- every job got touched and marked deferred instead of just
    staying queued). The fix caps the first pass at the replica budget for native
    destinations, which naturally caps the second pass too since wave_jobs is built from it."""
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "native-dest",
            engine="native",
            config={
                "k8s_dispatch": True,
                "replicas": ["r1", "r2"],
                "per_replica_cap": 1,
                "adaptive_budget": False,
            },
        )
    )
    total_jobs = 2000
    for i in range(total_jobs):
        job_id = f"job-{i}"
        store.add_job(
            MaintenanceJobRecord(
                id=job_id,
                run_id="run-1",
                table_id=f"table-{i}",
                destination_id="native-dest",
                mode=Mode.STANDARD,
            )
        )
        queue.enqueue(job_id, "native-dest")

    governor = Governor(queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False))
    result = governor.run_wave(now=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))

    # Budget is 2 replica slots (per_replica_cap=1 x 2 replicas): exactly that many admitted,
    # the scan blocked (not deferred) once it hit budget, and the other ~1998 jobs were never
    # touched at all this tick -- not scanned, not deferred, just left queued for next time.
    assert result.admitted == 2
    assert result.blocked >= 1
    assert result.deferred == 0
    assert len(queue.pending("native-dest")) == total_jobs - result.admitted


def test_governor_purges_jobs_for_missing_destination() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="missing-dest",
            mode=Mode.STANDARD,
        )
    )
    queue.enqueue("job-1", "missing-dest")

    result = Governor(queue, locks, store).run_wave(now=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))

    assert result.orphaned == 0
    assert store.get_job("job-1").status is JobStatus.FAILED
    assert store.get_job("job-1").error == "destination_not_found"
    assert queue.pending("missing-dest") == []
