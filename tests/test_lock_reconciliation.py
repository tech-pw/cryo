"""Tests for orphaned table-lock detection/release.

Regression coverage for a real incident, 2026-07-22: Governor acquires a table lock in
one step and only dispatches or safely defers-with-release in a later step. If the
process dies in that window (e.g. a rollout restart mid-admission), the lock is
orphaned and would otherwise sit until its TTL expires (up to 6h), blocking every
future admission attempt for that table.
"""

from __future__ import annotations

from types import SimpleNamespace

from cryo.cli import GovernorRunner
from cryo.domain.enums import JobStatus, Mode
from cryo.queue.locks import InMemoryLockStore
from cryo.worker.worker import InMemoryJobStore, MaintenanceJobRecord


def test_list_table_locks_returns_holder_pairs() -> None:
    store = InMemoryLockStore()
    store.acquire_table_lock("t1", "job-a", 3600)
    store.acquire_table_lock("t2", "job-b", 3600)

    pairs = set(store.list_table_locks())

    assert pairs == {("t1", "job-a"), ("t2", "job-b")}


def test_list_table_locks_omits_expired_entries() -> None:
    ticks = iter([0.0, 0.0, 10_000.0])

    class FakeClock:
        def __call__(self) -> object:
            from datetime import UTC, datetime, timedelta

            return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=next(ticks))

    store = InMemoryLockStore(clock=FakeClock())
    store.acquire_table_lock("t1", "job-a", 60)  # expires long before the 10_000s tick

    assert store.list_table_locks() == []


def _job(job_id: str, status: JobStatus) -> MaintenanceJobRecord:
    return MaintenanceJobRecord(
        id=job_id,
        run_id="r1",
        table_id="t1",
        destination_id="d1",
        mode=Mode.STANDARD,
        status=status,
    )


def test_reconcile_releases_lock_for_non_running_job() -> None:
    lock_store = InMemoryLockStore()
    lock_store.acquire_table_lock("t1", "job-a", 3600)
    job_store = InMemoryJobStore()
    job_store.add_job(_job("job-a", JobStatus.QUEUED))
    wiring = SimpleNamespace(lock_store=lock_store, job_store=job_store)

    GovernorRunner()._reconcile_orphaned_table_locks(wiring)

    assert not lock_store.is_table_locked("t1")


def test_reconcile_preserves_lock_for_running_job() -> None:
    lock_store = InMemoryLockStore()
    lock_store.acquire_table_lock("t1", "job-a", 3600)
    job_store = InMemoryJobStore()
    job_store.add_job(_job("job-a", JobStatus.RUNNING))
    wiring = SimpleNamespace(lock_store=lock_store, job_store=job_store)

    GovernorRunner()._reconcile_orphaned_table_locks(wiring)

    assert lock_store.is_table_locked("t1")


def test_reconcile_releases_lock_for_job_that_no_longer_exists() -> None:
    lock_store = InMemoryLockStore()
    lock_store.acquire_table_lock("t1", "ghost-job", 3600)
    job_store = InMemoryJobStore()
    wiring = SimpleNamespace(lock_store=lock_store, job_store=job_store)

    GovernorRunner()._reconcile_orphaned_table_locks(wiring)

    assert not lock_store.is_table_locked("t1")


def test_reconcile_leaves_unrelated_locks_alone() -> None:
    lock_store = InMemoryLockStore()
    lock_store.acquire_table_lock("running-table", "job-running", 3600)
    lock_store.acquire_table_lock("orphaned-table", "job-orphaned", 3600)
    job_store = InMemoryJobStore()
    job_store.add_job(_job("job-running", JobStatus.RUNNING))
    job_store.add_job(_job("job-orphaned", JobStatus.FAILED))
    wiring = SimpleNamespace(lock_store=lock_store, job_store=job_store)

    GovernorRunner()._reconcile_orphaned_table_locks(wiring)

    assert lock_store.is_table_locked("running-table")
    assert not lock_store.is_table_locked("orphaned-table")


def test_reconcile_preserves_lock_for_admitted_job() -> None:
    """The governor takes the lock at admission, while the job still waits for a worker."""
    lock_store = InMemoryLockStore()
    lock_store.acquire_table_lock("t1", "job-a", 3600)
    job_store = InMemoryJobStore()
    job_store.add_job(_job("job-a", JobStatus.ADMITTED))
    wiring = SimpleNamespace(lock_store=lock_store, job_store=job_store)

    GovernorRunner()._reconcile_orphaned_table_locks(wiring)

    assert lock_store.is_table_locked("t1")
