from __future__ import annotations

from cryo.domain.enums import JobStatus, Mode, OperationType
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.queue.run_events import InMemoryRunEventBus
from cryo.worker.worker import (
    DestinationConfig,
    FakeMaintenanceEngine,
    InMemoryJobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
    Worker,
)


def test_worker_emits_run_events() -> None:
    queue, locks, store = _worker_fixture()
    engine = FakeMaintenanceEngine()
    events = InMemoryRunEventBus()

    Worker(queue, store, lambda destination: engine, locks, run_events=events).process_next()

    published = events.replay("run-1")
    assert published
    assert any(event["type"] == "job_status" for event in published)
    assert any(
        event["type"] == "log_line" and "Started maintenance job" in event["message"]
        for event in published
    )
    assert any(
        event["type"] == "log_line" and event.get("operation") == OperationType.OPTIMIZE.value
        for event in published
    )
    assert any(
        event["type"] == "job_status" and event["status"] == JobStatus.SUCCEEDED.value
        for event in published
    )


def _worker_fixture() -> tuple[InMemoryMaintenanceQueue, InMemoryLockStore, InMemoryJobStore]:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig("dest"))
    store.add_table_data(
        "table-1",
        TableMaintenanceData(
            stats={
                "schema_name": "demo",
                "table_name": "events",
                "small_files_count": 500,
                "total_size_gb": 10,
            },
            partitions=[],
        ),
    )
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            params={"min_small_files": 10, "chunk_size_gb": 100},
            status=JobStatus.ADMITTED,
        )
    )
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", "r1")
    return queue, locks, store
