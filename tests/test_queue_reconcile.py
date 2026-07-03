from __future__ import annotations

from datetime import UTC, datetime

from cryo.domain.enums import JobStatus, Mode
from cryo.governor.admission import AdmissionConfig
from cryo.governor.governor import Governor
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.reconcile import reconcile_queued_jobs
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
