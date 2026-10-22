"""Governor review fixes in the reconcile path: FIFO-preserving re-queue, the deferred re-queue
agreeing with real admission, the still-blocked gate, and bounded/batched scans."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.domain.enums import JobStatus, Mode
from cryo.governor.governor import _still_blocked_predicate
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.reconcile import (
    purge_stale_queue_entries,
    reconcile_deferred_jobs,
    reconcile_queued_jobs,
)
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import DestinationConfig, InMemoryJobStore, MaintenanceJobRecord

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _store(**destination_config: object) -> InMemoryJobStore:
    store = InMemoryJobStore()
    config = {"replicas": ["r1"], "adaptive_budget": False, **destination_config}
    store.add_destination(DestinationConfig("dest", config=config))
    return store


def _job(job_id: str, status: JobStatus, **kwargs: object) -> MaintenanceJobRecord:
    return MaintenanceJobRecord(
        id=job_id,
        run_id=f"run-{job_id}",
        table_id=f"table-{job_id}",
        destination_id="dest",
        mode=Mode.STANDARD,
        status=status,
        **kwargs,  # type: ignore[arg-type]
    )


def test_requeued_jobs_keep_their_run_age_instead_of_jumping_the_queue() -> None:
    store = _store()
    queue = InMemoryMaintenanceQueue()
    store.add_job(_job("fresh", JobStatus.QUEUED, run_created_at=NOW))
    store.add_job(_job("old", JobStatus.QUEUED, run_created_at=NOW - timedelta(days=2)))

    assert reconcile_queued_jobs(store, queue) == 2

    assert queue.pending("dest") == ["old", "fresh"]


def test_deferred_requeue_uses_run_age_priority() -> None:
    store = _store()
    queue = InMemoryMaintenanceQueue()
    queue.enqueue("already-fresh", "dest", priority=-int(NOW.timestamp()))
    store.add_job(_job("old", JobStatus.DEFERRED, run_created_at=NOW - timedelta(days=2)))

    assert reconcile_deferred_jobs(store, queue, now=NOW) == 1

    assert queue.pending("dest") == ["old", "already-fresh"]


def test_deferred_job_of_unknown_cost_stays_deferred_under_a_fleet_budget() -> None:
    store = _store(fleet_budget_gb=100)
    queue = InMemoryMaintenanceQueue()
    store.add_job(_job("unknown", JobStatus.DEFERRED))
    store.add_job(_job("sized", JobStatus.DEFERRED, params={"size_gb": 5}))
    store.add_job(_job("forced", JobStatus.DEFERRED, force_admit=True))

    reconcile_deferred_jobs(store, queue, now=NOW)

    assert store.get_job("unknown").status is JobStatus.DEFERRED
    assert store.get_job("sized").status is JobStatus.QUEUED
    assert store.get_job("forced").status is JobStatus.QUEUED


def test_still_blocked_keeps_a_job_whose_table_is_locked() -> None:
    locks = InMemoryLockStore()
    store = _store()
    queue = InMemoryMaintenanceQueue()
    store.add_job(_job("locked", JobStatus.DEFERRED))
    store.add_job(_job("free", JobStatus.DEFERRED))
    assert locks.acquire_table_lock("table-locked", "someone-else", ttl_seconds=600)
    predicate = _still_blocked_predicate(None, locks, now=NOW)

    reconcile_deferred_jobs(store, queue, now=NOW, still_blocked=predicate)

    assert store.get_job("locked").status is JobStatus.DEFERRED
    assert store.get_job("free").status is JobStatus.QUEUED


def test_still_blocked_keeps_a_job_whose_intent_is_not_ready_or_expired() -> None:
    locks = InMemoryLockStore()
    predicate = _still_blocked_predicate(None, locks, now=NOW)

    not_ready = _job("a", JobStatus.DEFERRED, intent_id="i", intent_status="pending")
    expired = _job(
        "b",
        JobStatus.DEFERRED,
        intent_id="i",
        intent_status="ready",
        intent_expires_at=NOW - timedelta(minutes=1),
    )
    valid = _job(
        "c",
        JobStatus.DEFERRED,
        intent_id="i",
        intent_status="ready",
        intent_expires_at=NOW + timedelta(hours=1),
    )
    no_intent = _job("d", JobStatus.DEFERRED)

    assert predicate(not_ready) is True
    assert predicate(expired) is True
    assert predicate(valid) is False
    assert predicate(no_intent) is False


def test_purge_scan_is_bounded_per_destination(monkeypatch) -> None:
    monkeypatch.setattr("cryo.queue.reconcile._MAX_PURGE_SCAN_PER_DESTINATION", 3)
    store = _store()
    queue = InMemoryMaintenanceQueue()
    for index in range(6):
        store.add_job(_job(f"job-{index}", JobStatus.SKIPPED))
        queue.enqueue(f"job-{index}", "dest", priority=index)

    assert purge_stale_queue_entries(store, queue) == 3
    assert queue.pending_depth("dest") == 3


def test_purge_looks_statuses_up_in_one_batch_per_destination() -> None:
    store = _store()
    queue = InMemoryMaintenanceQueue()
    calls: list[int] = []
    original = store.job_statuses

    def counting(job_ids):  # type: ignore[no-untyped-def]
        calls.append(len(job_ids))
        return original(job_ids)

    store.job_statuses = counting  # type: ignore[method-assign]
    store.get_job = lambda job_id: (_ for _ in ()).throw(AssertionError("per-job lookup"))  # type: ignore[method-assign]
    for index in range(5):
        store.add_job(_job(f"11111111-1111-1111-1111-11111111111{index}", JobStatus.QUEUED))
        queue.enqueue(f"11111111-1111-1111-1111-11111111111{index}", "dest")

    assert purge_stale_queue_entries(store, queue) == 0
    assert calls == [5]
