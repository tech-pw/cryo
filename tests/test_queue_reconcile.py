from __future__ import annotations

from datetime import UTC, datetime

from cryo.domain.enums import JobStatus, Mode
from cryo.governor.admission import AdmissionConfig
from cryo.governor.governor import Governor
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.reconcile import (
    purge_stale_queue_entries,
    reconcile_queued_jobs,
    reconcile_stale_running_jobs,
)
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import DestinationConfig, InMemoryJobStore, MaintenanceJobRecord


def test_reconcile_re_enqueues_db_queued_jobs_missing_from_redis() -> None:
    queue = InMemoryMaintenanceQueue()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig("dest", config={"replicas": ["r1"], "per_replica_cap": 1})
    )
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            status=JobStatus.QUEUED,
        )
    )

    assert queue.pending("dest") == []
    assert reconcile_queued_jobs(store, queue) == 1
    assert queue.pending("dest") == ["job-1"]


def test_purge_removes_pending_entries_for_jobs_gone_terminal_in_db() -> None:
    # Mirrors a job whose DB status was updated directly (e.g. a bulk-cancel script) without
    # going through queue.cancel_pending() -- the Redis pending entry outlives the DB row's
    # terminal status and must be purged, not just entries for jobs missing outright.
    queue = InMemoryMaintenanceQueue()
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig("dest", config={"replicas": ["r1"]}))
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            status=JobStatus.SKIPPED,
        )
    )
    queue.enqueue("job-1", "dest")

    assert queue.pending("dest") == ["job-1"]
    assert purge_stale_queue_entries(store, queue) == 1
    assert queue.pending("dest") == []


def test_purge_keeps_pending_entries_for_jobs_still_active_in_db() -> None:
    queue = InMemoryMaintenanceQueue()
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig("dest", config={"replicas": ["r1"]}))
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            status=JobStatus.QUEUED,
        )
    )
    queue.enqueue("job-1", "dest")

    assert purge_stale_queue_entries(store, queue) == 0
    assert queue.pending("dest") == ["job-1"]


def test_reconcile_reaps_running_job_whose_worker_never_came_back() -> None:
    # Mirrors a worker dying mid-job (OOM, node eviction, rollout): the job's DB row is
    # stuck RUNNING forever since nothing else ever transitions it out, silently pinning a
    # destination concurrency slot and a table lock. Seen in production: 5 jobs stuck
    # RUNNING for hours across two worker restarts.
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            status=JobStatus.RUNNING,
            started_at=datetime(2026, 7, 13, 17, 15, tzinfo=UTC),
        )
    )
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", replica="r1")
    queue.mark_running("job-1")
    assert locks.acquire_table_lock("table-1", "job-1", ttl_seconds=6 * 60 * 60)
    assert queue.local_in_flight_by_replica("dest") == {"r1": 1}

    reaped = reconcile_stale_running_jobs(
        store,
        queue,
        locks,
        now=datetime(2026, 7, 14, 13, 0, tzinfo=UTC),
        stale_after_seconds=6 * 60 * 60,
    )

    assert reaped == 1
    assert store.get_job("job-1").status is JobStatus.FAILED
    assert store.get_job("job-1").error == "stale_running_reaped"
    assert queue.local_in_flight_by_replica("dest") == {}
    # The table lock must be released too, or the "zombie" just moves from blocking
    # admission slots to blocking every other job that wants this table.
    assert not locks.is_table_locked("table-1")


def test_reconcile_keeps_running_job_still_within_staleness_window() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            status=JobStatus.RUNNING,
            started_at=datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        )
    )

    reaped = reconcile_stale_running_jobs(
        store,
        queue,
        locks,
        now=datetime(2026, 7, 14, 13, 0, tzinfo=UTC),
        stale_after_seconds=6 * 60 * 60,
    )

    assert reaped == 0
    assert store.get_job("job-1").status is JobStatus.RUNNING


def test_reconcile_skips_stale_job_with_adopt_lease() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            status=JobStatus.RUNNING,
            started_at=datetime(2026, 7, 13, 17, 15, tzinfo=UTC),
        )
    )
    assert locks.acquire_job_owner("job-1", "worker-a", ttl_seconds=600)

    reaped = reconcile_stale_running_jobs(
        store,
        queue,
        locks,
        now=datetime(2026, 7, 14, 13, 0, tzinfo=UTC),
        stale_after_seconds=6 * 60 * 60,
    )

    assert reaped == 0
    assert store.get_job("job-1").status is JobStatus.RUNNING


def test_reconcile_skips_stale_job_with_started_step() -> None:
    from cryo.db.repositories.job_steps import InMemoryJobStepStore
    from cryo.domain.enums import OperationType

    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    steps = InMemoryJobStepStore()
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            status=JobStatus.RUNNING,
            started_at=datetime(2026, 7, 13, 17, 15, tzinfo=UTC),
        )
    )
    steps.begin_step("job-1", 0, OperationType.EXPIRE_SNAPSHOTS, detail="expire")

    reaped = reconcile_stale_running_jobs(
        store,
        queue,
        locks,
        now=datetime(2026, 7, 14, 13, 0, tzinfo=UTC),
        stale_after_seconds=6 * 60 * 60,
        step_ledger=steps,
    )

    assert reaped == 0
    assert store.get_job("job-1").status is JobStatus.RUNNING


def test_governor_re_enqueues_and_admits_db_queued_jobs() -> None:
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
                "offpeak_end_ist": 0,
                "adaptive_budget": False,
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
            status=JobStatus.QUEUED,
        )
    )

    governor = Governor(queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False))
    result = governor.run_wave(now=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))

    assert result.re_enqueued == 1
    assert result.admitted == 1
    assert store.get_job("job-1").status is JobStatus.ADMITTED


def test_governor_wave_reaps_stale_running_jobs() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig("dest", config={"replicas": ["r1"]}))
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            status=JobStatus.RUNNING,
            started_at=datetime(2026, 7, 13, 17, 15, tzinfo=UTC),
        )
    )

    governor = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(adaptive_budget=False),
        lock_ttl_seconds=6 * 60 * 60,
        # This test is fully in-memory (queue/store/lock_store are fakes, "job-1" isn't even a
        # real UUID) -- Governor's step-ledger lookup for the stale reaper needs a real DB
        # session factory by default for production use, so opt out of it here rather than
        # let it try to hit a real Postgres connection.
        session_factory=None,
    )
    result = governor.run_wave(now=datetime(2026, 7, 14, 13, 0, tzinfo=UTC))

    assert result.reaped == 1
    assert store.get_job("job-1").status is JobStatus.FAILED
