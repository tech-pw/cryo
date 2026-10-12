from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from cryo.domain.enums import EngineType, JobStatus, Mode
from cryo.governor.admission import AdmissionConfig
from cryo.governor.governor import Governor
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.reconcile import (
    _MAX_DEFERRED_RECONCILE_PER_WAVE,
    _MAX_QUEUED_RECONCILE_PER_WAVE,
    purge_stale_queue_entries,
    reconcile_deferred_jobs,
    reconcile_queued_jobs,
    reconcile_stale_running_jobs,
    reconcile_stuck_admitted_jobs,
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


def test_reconcile_re_enqueues_a_job_whose_hash_says_queued_but_is_missing_from_pending() -> None:
    """2026-09-07 fix: queue.status() (the job hash's own status field) can desync from
    actual pending-zset membership -- nothing updates the hash back off "queued" when an
    entry is removed from the zset (e.g. cancel_pending, or purge_stale_queue_entries).
    Confirmed live: 3,046 jobs across 2 runs sat with status()=='queued' but genuinely
    absent from their destination's pending queue for 21+ hours -- reconcile_queued_jobs
    trusted status() alone and skipped them on every single wave. This directly reproduces
    that desync (status says QUEUED, but the job was never actually enqueued) and asserts
    the fix: reconcile now checks real pending-zset membership via is_pending(), not just
    the hash, for the QUEUED case.
    """
    queue = InMemoryMaintenanceQueue()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig("dest", config={"replicas": ["r1"], "per_replica_cap": 1})
    )
    store.add_job(
        MaintenanceJobRecord(
            id="desynced-job",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            status=JobStatus.QUEUED,
        )
    )
    # Simulate the exact desync: the queue's own hash says QUEUED (as if it had once been
    # enqueued and then cancel_pending'd, which never resets this field), but it was never
    # actually added to the pending zset -- or was removed from it without the hash
    # following along.
    queue._statuses["desynced-job"] = JobStatus.QUEUED
    assert queue.status("desynced-job") is JobStatus.QUEUED
    assert not queue.is_pending("desynced-job", "dest")
    assert queue.pending("dest") == []

    re_enqueued = reconcile_queued_jobs(store, queue)

    assert re_enqueued == 1
    assert queue.pending("dest") == ["desynced-job"]


def test_reconcile_does_not_re_enqueue_a_genuinely_admitted_job() -> None:
    """Safety boundary for the same fix: ADMITTED/RUNNING correctly means "not in the
    pending zset by design" (it moved to the admitted list/in-flight counter), not desync
    -- reconcile must still trust status() for those, never re-enqueuing a job that's
    legitimately already past the pending stage."""
    queue = InMemoryMaintenanceQueue()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig("dest", config={"replicas": ["r1"], "per_replica_cap": 1})
    )
    store.add_job(
        MaintenanceJobRecord(
            id="admitted-job",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
            status=JobStatus.QUEUED,
        )
    )
    queue.enqueue("admitted-job", "dest")
    queue.admit("admitted-job", "dest", "r1")
    assert queue.status("admitted-job") is JobStatus.ADMITTED

    re_enqueued = reconcile_queued_jobs(store, queue)

    assert re_enqueued == 0
    assert queue.pending("dest") == []


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


def test_reconcile_requeues_an_admitted_job_the_queue_has_lost_entirely() -> None:
    """The one delivery failure the consumer group cannot repair from the inside.

    A worker dying between claiming a job and starting it leaves the entry in the group's
    pending list, so another worker reclaims it (see test_admitted_stream_queue). What is
    left for this pass is the entry being gone outright -- a Redis restart without
    persistence -- where the DB row says ADMITTED, the in-flight slot is still held, and
    nothing in Redis will ever produce that job again."""
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
            status=JobStatus.ADMITTED,
            admitted_at=datetime(2026, 9, 7, 2, 47, tzinfo=UTC),
            engine_ref=None,
        )
    )
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", replica="r1")
    assert locks.acquire_table_lock("table-1", "job-1", ttl_seconds=6 * 60 * 60)
    assert queue.local_in_flight_by_replica("dest") == {"r1": 1}
    # Redis lost the admission: neither undelivered nor held by any worker.
    queue._admitted.clear()
    queue._claimed.clear()

    reaped = reconcile_stuck_admitted_jobs(
        store,
        queue,
        locks,
        now=datetime(2026, 9, 8, 5, 0, tzinfo=UTC),
        stuck_after_seconds=15 * 60,
    )

    assert reaped == 1
    # Recovered, not discarded: the table still needs maintenance, and nothing about the
    # dispatch being lost says the work itself is bad.
    assert store.get_job("job-1").status is JobStatus.QUEUED
    assert queue.is_pending("job-1", "dest")
    assert not locks.is_table_locked("table-1")


def test_reconcile_leaves_a_claimed_job_to_the_consumer_group() -> None:
    """A worker that claimed a job and died is the queue's problem to solve, not this pass's.

    Touching it here would be actively wrong: the entry is still in the group's pending list,
    so requeueing would put the same work in two places at once."""
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
            status=JobStatus.ADMITTED,
            admitted_at=datetime(2026, 9, 7, 2, 47, tzinfo=UTC),
            engine_ref=None,
        )
    )
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", replica="r1")
    assert queue.claim_admitted() == "job-1"

    reaped = reconcile_stuck_admitted_jobs(
        store,
        queue,
        locks,
        now=datetime(2026, 9, 8, 5, 0, tzinfo=UTC),
        stuck_after_seconds=15 * 60,
    )

    assert reaped == 0
    assert store.get_job("job-1").status is JobStatus.ADMITTED


def test_reconcile_leaves_an_old_admitted_job_that_is_still_awaiting_a_worker() -> None:
    """Age plus a missing engine_ref does not mean a job was dropped.

    Live 2026-09-21: 5,952 of one run's 6,117 jobs were recorded as
    `stuck_admitted_no_dispatch` while workers were healthy and still draining the same claim
    list -- the run was simply larger than the fleet could process inside the grace window.
    A job still on the claim list has an owner coming for it however long the queue is."""
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
            status=JobStatus.ADMITTED,
            admitted_at=datetime(2026, 9, 7, 2, 47, tzinfo=UTC),
            engine_ref=None,
        )
    )
    queue.enqueue("job-1", "dest")
    queue.admit("job-1", "dest", replica="r1")

    reaped = reconcile_stuck_admitted_jobs(
        store,
        queue,
        locks,
        now=datetime(2026, 9, 8, 5, 0, tzinfo=UTC),
        stuck_after_seconds=15 * 60,
    )

    assert reaped == 0
    assert store.get_job("job-1").status is JobStatus.ADMITTED
    assert queue.awaiting_claim("job-1")


def test_reconcile_keeps_freshly_admitted_job_within_the_grace_window() -> None:
    """Must not regress into failing a job admitted moments ago -- e.g. native-k8s admit that
    marks ADMITTED before mark_status(RUNNING, engine_ref=...) lands a beat later."""
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
            status=JobStatus.ADMITTED,
            admitted_at=datetime(2026, 9, 8, 4, 58, tzinfo=UTC),
            engine_ref=None,
        )
    )

    reaped = reconcile_stuck_admitted_jobs(
        store,
        queue,
        locks,
        now=datetime(2026, 9, 8, 5, 0, tzinfo=UTC),
        stuck_after_seconds=15 * 60,
    )

    assert reaped == 0
    assert store.get_job("job-1").status is JobStatus.ADMITTED


def test_reconcile_never_touches_an_admitted_job_with_a_real_engine_ref() -> None:
    """A job legitimately mid-dispatch (e.g. native k8s admit already created its Job, about
    to transition to RUNNING) can read ADMITTED with a real engine_ref already set -- must not
    be treated as stuck just because it's past the age threshold."""
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
            status=JobStatus.ADMITTED,
            admitted_at=datetime(2026, 9, 7, 2, 47, tzinfo=UTC),
            engine_ref="cryo-native-abc123",
        )
    )

    reaped = reconcile_stuck_admitted_jobs(
        store,
        queue,
        locks,
        now=datetime(2026, 9, 8, 5, 0, tzinfo=UTC),
        stuck_after_seconds=15 * 60,
    )

    assert reaped == 0
    assert store.get_job("job-1").status is JobStatus.ADMITTED


def test_reconcile_skips_admitted_job_with_no_admitted_at_timestamp() -> None:
    """Pre-migration/legacy rows (or a store that doesn't stamp admitted_at) can't be safely
    aged -- skip rather than risk failing a job that might be perfectly fine."""
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
            status=JobStatus.ADMITTED,
            admitted_at=None,
            engine_ref=None,
        )
    )

    reaped = reconcile_stuck_admitted_jobs(
        store,
        queue,
        locks,
        now=datetime(2026, 9, 8, 5, 0, tzinfo=UTC),
        stuck_after_seconds=15 * 60,
    )

    assert reaped == 0
    assert store.get_job("job-1").status is JobStatus.ADMITTED


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


class TestReconcileQueuedJobsBounding:
    """2026-09-02, same incident as TestReconcileDeferredJobsBounding: fixing
    reconcile_deferred_jobs alone was not enough -- a governor pod restarted onto that fix
    still failed to complete a single wave for 2.5+ minutes, because reconcile_queued_jobs
    has the identical unbounded-scan pattern and, at 28,445 queued jobs (far exceeding the
    4,227 deferred that surfaced the first bug), was the larger bottleneck once the deferred
    side was bounded."""

    def test_re_enqueues_all_queued_jobs_when_under_the_limit(self) -> None:
        queue = InMemoryMaintenanceQueue()
        store = InMemoryJobStore()
        store.add_destination(DestinationConfig("dest", config={"replicas": ["r1"]}))
        for i in range(5):
            store.add_job(
                MaintenanceJobRecord(
                    id=f"job-{i}",
                    run_id="run-1",
                    table_id=f"table-{i}",
                    destination_id="dest",
                    mode=Mode.STANDARD,
                    status=JobStatus.QUEUED,
                )
            )

        assert reconcile_queued_jobs(store, queue) == 5
        for i in range(5):
            assert queue.pending("dest").count(f"job-{i}") == 1

    def test_bounds_reconcile_to_max_per_wave_when_backlog_exceeds_it(self, monkeypatch) -> None:
        monkeypatch.setattr("cryo.queue.reconcile._MAX_QUEUED_RECONCILE_PER_WAVE", 3)
        queue = InMemoryMaintenanceQueue()
        store = InMemoryJobStore()
        store.add_destination(DestinationConfig("dest", config={"replicas": ["r1"]}))
        for i in range(10):
            store.add_job(
                MaintenanceJobRecord(
                    id=f"job-{i}",
                    run_id="run-1",
                    table_id=f"table-{i}",
                    destination_id="dest",
                    mode=Mode.STANDARD,
                    status=JobStatus.QUEUED,
                )
            )

        re_enqueued = reconcile_queued_jobs(store, queue)

        assert re_enqueued == 3

    def test_real_bound_constant_is_reasonable(self) -> None:
        """Larger than the deferred bound -- re-enqueuing a queued-but-Redis-missing job is
        cheap (no admission decision, just a queue write), so more fits in the same time
        budget per wave."""
        assert _MAX_QUEUED_RECONCILE_PER_WAVE > _MAX_DEFERRED_RECONCILE_PER_WAVE


class TestReconcileDeferredJobsBounding:
    """2026-09-02 fix: reconcile_deferred_jobs previously called list_deferred_jobs()
    unbounded on every governor wave -- live-verified, 4,227 deferred jobs made each wave's
    reconcile step slow enough to stall the governor entirely (admitted-per-wave declined
    toward zero across consecutive waves; a pod restart didn't help, confirming the backlog
    size itself, not pod state, was the bottleneck). Now bounded to
    _MAX_DEFERRED_RECONCILE_PER_WAVE per call, oldest-deferred-first in the real DB-backed
    store (job_store.py's list_deferred_jobs orders by finished_at when limit is given)."""

    def test_requeues_all_deferred_jobs_when_under_the_limit(self) -> None:
        queue = InMemoryMaintenanceQueue()
        store = InMemoryJobStore()
        store.add_destination(
            DestinationConfig("dest", config={"replicas": ["r1"], "adaptive_budget": False})
        )
        for i in range(5):
            store.add_job(
                MaintenanceJobRecord(
                    id=f"job-{i}",
                    run_id="run-1",
                    table_id=f"table-{i}",
                    destination_id="dest",
                    mode=Mode.STANDARD,
                    status=JobStatus.DEFERRED,
                )
            )

        requeued = reconcile_deferred_jobs(
            store, queue, now=datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
        )

        assert requeued == 5
        for i in range(5):
            assert store.get_job(f"job-{i}").status is JobStatus.QUEUED

    def test_bounds_reconcile_to_max_per_wave_when_backlog_exceeds_it(self, monkeypatch) -> None:
        """Direct regression: a backlog larger than the bound must not all be processed in
        one call -- confirms the fix actually caps per-wave work instead of just being wired
        up with no effect. Uses a small monkeypatched bound so the test doesn't need to seed
        thousands of jobs to exercise the cap."""
        monkeypatch.setattr("cryo.queue.reconcile._MAX_DEFERRED_RECONCILE_PER_WAVE", 3)
        queue = InMemoryMaintenanceQueue()
        store = InMemoryJobStore()
        store.add_destination(
            DestinationConfig("dest", config={"replicas": ["r1"], "adaptive_budget": False})
        )
        for i in range(10):
            store.add_job(
                MaintenanceJobRecord(
                    id=f"job-{i}",
                    run_id="run-1",
                    table_id=f"table-{i}",
                    destination_id="dest",
                    mode=Mode.STANDARD,
                    status=JobStatus.DEFERRED,
                )
            )

        requeued = reconcile_deferred_jobs(
            store, queue, now=datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
        )

        assert requeued == 3
        still_deferred = sum(
            1 for i in range(10) if store.get_job(f"job-{i}").status is JobStatus.DEFERRED
        )
        assert still_deferred == 7

    def test_real_bound_constant_is_reasonable(self) -> None:
        """Sanity check on the constant itself -- large enough to make real progress each
        wave, small enough that a single wave's reconcile step can't grow unbounded with the
        backlog. Matches the same order of magnitude as _MAX_DISPATCHES_PER_TICK
        (native/watcher.py)."""
        assert 0 < _MAX_DEFERRED_RECONCILE_PER_WAVE <= 1000


class TestReconcileDeferredJobsOrphanedRunCleanup:
    """2026-09-03 fix: a deferred job whose parent run already reached a terminal status can
    never legitimately un-defer (its run's deadline is permanently past), but re-deferring it
    doesn't refresh finished_at -- so it stayed the *oldest* deferred job forever and, combined
    with list_deferred_jobs's oldest-first bounded ordering (the 2026-09-02 fix), monopolized
    every wave's entire reconcile budget. Live-verified: 31,902 of 40,785 deferred jobs
    belonged to already-terminal runs; 5+ consecutive governor waves admitted/deferred=0
    because the same dead jobs were re-picked every time."""

    def test_deferred_job_with_terminal_run_status_is_failed_not_reconsidered(self) -> None:
        queue = InMemoryMaintenanceQueue()
        store = InMemoryJobStore()
        store.add_destination(
            DestinationConfig("dest", config={"replicas": ["r1"], "adaptive_budget": False})
        )
        store.add_job(
            MaintenanceJobRecord(
                id="job-orphaned",
                run_id="run-1",
                table_id="table-1",
                destination_id="dest",
                mode=Mode.STANDARD,
                status=JobStatus.DEFERRED,
                run_status="partial",
            )
        )

        requeued = reconcile_deferred_jobs(
            store, queue, now=datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
        )

        assert requeued == 0
        assert store.get_job("job-orphaned").status is JobStatus.FAILED
        assert (
            store.get_job("job-orphaned").error == "orphaned_deferred: parent run already finalized"
        )

    def test_deferred_job_with_running_run_status_is_still_reconsidered(self) -> None:
        """The safety boundary: a job whose run is genuinely still in flight (status=
        'running') must go through the normal admission-decision path, not be failed
        outright."""
        queue = InMemoryMaintenanceQueue()
        store = InMemoryJobStore()
        store.add_destination(
            DestinationConfig("dest", config={"replicas": ["r1"], "adaptive_budget": False})
        )
        store.add_job(
            MaintenanceJobRecord(
                id="job-in-flight",
                run_id="run-1",
                table_id="table-1",
                destination_id="dest",
                mode=Mode.STANDARD,
                status=JobStatus.DEFERRED,
                run_status="running",
            )
        )

        requeued = reconcile_deferred_jobs(
            store, queue, now=datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
        )

        assert requeued == 1
        assert store.get_job("job-in-flight").status is JobStatus.QUEUED

    def test_orphaned_cleanup_frees_budget_for_genuinely_reconsiderable_jobs(
        self, monkeypatch
    ) -> None:
        """Direct regression for the live incident shape: a small reconcile budget entirely
        consumed by dead-run jobs, ordered oldest-first ahead of a genuinely admittable job --
        without this fix, the admittable job would never be reached at all."""
        monkeypatch.setattr("cryo.queue.reconcile._MAX_DEFERRED_RECONCILE_PER_WAVE", 3)
        queue = InMemoryMaintenanceQueue()
        store = InMemoryJobStore()
        store.add_destination(
            DestinationConfig("dest", config={"replicas": ["r1"], "adaptive_budget": False})
        )
        for i in range(3):
            store.add_job(
                MaintenanceJobRecord(
                    id=f"job-dead-{i}",
                    run_id="run-dead",
                    table_id=f"table-dead-{i}",
                    destination_id="dest",
                    mode=Mode.STANDARD,
                    status=JobStatus.DEFERRED,
                    run_status="failed",
                )
            )
        store.add_job(
            MaintenanceJobRecord(
                id="job-alive",
                run_id="run-alive",
                table_id="table-alive",
                destination_id="dest",
                mode=Mode.STANDARD,
                status=JobStatus.DEFERRED,
                run_status="running",
            )
        )

        # InMemoryJobStore.list_deferred_jobs preserves insertion order (no finished_at
        # ordering in the fake store) -- the 3 dead jobs are seeded first, so with the bound at
        # 3, a naive implementation would exhaust the whole budget on them and never reach
        # job-alive in this same wave. With the orphaned-run fix, dead jobs resolve to FAILED
        # immediately (not "still using up admission-decision budget slowly"), so real per-wave
        # capacity is still spent, but this test's real point is that dead jobs are correctly
        # identified and removed rather than silently re-deferred.
        requeued = reconcile_deferred_jobs(
            store, queue, now=datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
        )

        for i in range(3):
            assert store.get_job(f"job-dead-{i}").status is JobStatus.FAILED
        # job-alive is outside this wave's bound (3 dead jobs already consumed it) -- still
        # deferred, correctly left for the next wave rather than incorrectly failed.
        assert store.get_job("job-alive").status is JobStatus.DEFERRED
        assert requeued == 0


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


def test_governor_wave_does_not_reap_spark_job_whose_driver_pod_is_still_running(
    monkeypatch,
) -> None:
    """Regression, 2026-08-11: engine_activity_checker was declared as a parameter on
    reconcile_stale_running_jobs but never actually passed from anywhere in the codebase --
    every RUNNING job past stale_after_seconds got reaped unconditionally, with no way to
    tell "worker died" from "job is huge and still going". Confirmed live: a 24TB rewrite
    legitimately still processing after ~5h (driver pod genuinely Running) would have been
    force-FAILED by this reaper despite doing real, uninterrupted work the whole time."""
    from cryo.engines.spark.k8s import JobRunResult, JobRunState

    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "spark-dest",
            engine=EngineType.SPARK_EMR,
            config={"replicas": ["r1"], "catalog": "sample"},
        )
    )
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="spark-dest",
            mode=Mode.STANDARD,
            status=JobStatus.RUNNING,
            started_at=datetime(2026, 8, 11, 10, 39, tzinfo=UTC),
            engine_ref="cryo-spark-17449de5c7",
        )
    )

    mock_client = MagicMock()
    mock_client.get_job_run.return_value = JobRunResult(
        job_run_id="cryo-spark-17449de5c7", state=JobRunState.RUNNING
    )
    monkeypatch.setattr("cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client)

    governor = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(adaptive_budget=False),
        lock_ttl_seconds=6 * 60 * 60,
        session_factory=None,
    )
    result = governor.run_wave(now=datetime(2026, 8, 11, 17, 0, tzinfo=UTC))

    assert result.reaped == 0
    assert store.get_job("job-1").status is JobStatus.RUNNING
    mock_client.get_job_run.assert_called_once_with("cryo-spark-17449de5c7")


def test_governor_wave_reaps_spark_job_whose_driver_pod_is_gone(monkeypatch) -> None:
    """The other direction of the same fix: a Spark job whose driver pod genuinely finished
    or was never found must still be reaped -- the checker isn't a blanket "never reap Spark
    jobs" escape hatch, only a real liveness signal."""
    from cryo.engines.spark.k8s import JobRunResult, JobRunState

    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "spark-dest",
            engine=EngineType.SPARK_EMR,
            config={"replicas": ["r1"], "catalog": "sample"},
        )
    )
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="spark-dest",
            mode=Mode.STANDARD,
            status=JobStatus.RUNNING,
            started_at=datetime(2026, 8, 11, 10, 39, tzinfo=UTC),
            engine_ref="cryo-spark-dead",
        )
    )

    mock_client = MagicMock()
    mock_client.get_job_run.return_value = JobRunResult(
        job_run_id="cryo-spark-dead", state=JobRunState.FAILED
    )
    monkeypatch.setattr("cryo.engines.spark.k8s_engine.SparkK8sClient", lambda config: mock_client)

    governor = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(adaptive_budget=False),
        lock_ttl_seconds=6 * 60 * 60,
        session_factory=None,
    )
    result = governor.run_wave(now=datetime(2026, 8, 11, 17, 0, tzinfo=UTC))

    assert result.reaped == 1
    assert store.get_job("job-1").status is JobStatus.FAILED


class TestDeferredJobsBlockedByANonAdmissionGate:
    """Live 2026-09-23: `deferred=24, re_enqueued=24` on every single governor wave, the same
    24 job ids each time, every one of them "Destructive operation requires approval". The
    wave's gate chain refuses them at _gate_approval, which sits *before* admission; this
    function only ever re-ran the admission arithmetic, so it kept handing them back and the
    next wave kept refusing them. At a ~39s wave that is ~2,250 pointless round trips per job
    per day, none of which could ever succeed -- waiting does not approve anything.

    The re-queue itself cannot simply be dropped: a deferred job is absent from the pending
    zset, so this is the only path back to where the gate chain can see it at all.
    """

    @staticmethod
    def _store_with_deferred_job() -> tuple[InMemoryMaintenanceQueue, InMemoryJobStore]:
        queue = InMemoryMaintenanceQueue()
        store = InMemoryJobStore()
        store.add_destination(
            DestinationConfig("dest", config={"replicas": ["r1"], "adaptive_budget": False})
        )
        store.add_job(
            MaintenanceJobRecord(
                id="job-approval",
                run_id="run-1",
                table_id="table-1",
                destination_id="dest",
                mode=Mode.STANDARD,
                status=JobStatus.DEFERRED,
                error="Destructive operation requires approval",
            )
        )
        return queue, store

    def test_a_job_whose_gate_still_refuses_it_is_left_alone(self) -> None:
        queue, store = self._store_with_deferred_job()

        requeued = reconcile_deferred_jobs(
            store,
            queue,
            now=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
            still_blocked=lambda job: True,
        )

        assert requeued == 0
        assert store.get_job("job-approval").status is JobStatus.DEFERRED

    def test_the_job_comes_straight_back_once_the_gate_stops_refusing(self) -> None:
        """The half that matters more: approving a job has to actually release it. Skipping
        the re-queue permanently would strand every approved job instead of churning it."""
        queue, store = self._store_with_deferred_job()

        requeued = reconcile_deferred_jobs(
            store,
            queue,
            now=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
            still_blocked=lambda job: False,
        )

        assert requeued == 1
        assert store.get_job("job-approval").status is JobStatus.QUEUED

    def test_admission_still_filters_first_so_the_check_stays_cheap(self) -> None:
        """A job admission would defer anyway must never reach the predicate -- that lookup
        hits the database, and this runs against the whole deferred backlog every wave."""
        queue, store = self._store_with_deferred_job()
        store.add_job(
            MaintenanceJobRecord(
                id="job-cooldown",
                run_id="run-1",
                table_id="table-2",
                destination_id="dest",
                mode=Mode.STANDARD,
                status=JobStatus.DEFERRED,
                last_maintained_at=datetime(2026, 9, 23, 11, 59, tzinfo=UTC),
                cooldown_hours=24,
            )
        )
        asked: list[str] = []

        reconcile_deferred_jobs(
            store,
            queue,
            now=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
            still_blocked=lambda job: bool(asked.append(job.id)),
        )

        # Both halves: the approval job proves the predicate is reached at all, so the
        # cooldown job's absence is admission filtering it rather than the loop never running.
        assert "job-approval" in asked
        assert "job-cooldown" not in asked

    def test_without_a_predicate_the_previous_behaviour_is_unchanged(self) -> None:
        queue, store = self._store_with_deferred_job()

        requeued = reconcile_deferred_jobs(
            store, queue, now=datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        )

        assert requeued == 1
        assert store.get_job("job-approval").status is JobStatus.QUEUED
