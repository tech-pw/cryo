"""Admission-pass behaviour that the 620-line run_wave never had coverage for.

Written alongside the decomposition of that function so the pieces it was split into are
pinned by tests rather than by reading: the order the gates apply in, which counter each
refusal bumps, and the two ways a destination can run out of room. The gate order in
particular was only discoverable by tracing ten levels of nesting, and a reordering that
moved the table lock earlier would silently start leaking locks on refusal.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from cryo.domain.enums import EngineType, JobStatus, Mode
from cryo.governor.admission import AdmissionConfig
from cryo.governor.governor import Governor, _WaveTally, _WorkerDrainHeadroom
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import DestinationConfig, InMemoryJobStore, MaintenanceJobRecord

NOW = datetime(2026, 9, 22, 20, 0, tzinfo=UTC)
DEST = "dest"


def _null_session_factory():
    return nullcontext(MagicMock())


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    monkeypatch.setattr("cryo.governor.governor.finalize_open_runs", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "cryo.governor.governor.prefetch_approved_operation_keys", lambda *_a, **_k: {}
    )
    monkeypatch.setattr("cryo.governor.governor.prefetch_table_stats", lambda *_a, **_k: {})
    monkeypatch.setattr("cryo.governor.governor.prefetch_paused_table_ids", lambda *_a, **_k: set())
    monkeypatch.setattr(
        "cryo.governor.governor.prefetch_paused_destination_ids", lambda *_a, **_k: set()
    )


def _job(job_id: str, *, table_id: str | None = None) -> MaintenanceJobRecord:
    return MaintenanceJobRecord(
        id=job_id,
        run_id="run-1",
        table_id=table_id or f"table-{job_id}",
        destination_id=DEST,
        mode=Mode.STANDARD,
        status=JobStatus.QUEUED,
    )


def _governor(
    *, slot_cap: int = 5, jobs: list[MaintenanceJobRecord] | None = None, queue=None
) -> tuple[Governor, InMemoryMaintenanceQueue, InMemoryJobStore, InMemoryLockStore]:
    queue = queue or InMemoryMaintenanceQueue()
    store = InMemoryJobStore()
    locks = InMemoryLockStore()
    store.add_destination(
        DestinationConfig(
            id=DEST,
            engine=EngineType.SPARK_EMR,
            config={"replicas": ["r1", "r2", "r3"], "per_replica_cap": slot_cap},
        )
    )
    for job in jobs or []:
        store.add_job(job)
        queue.enqueue(job.id, DEST)
    governor = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(per_replica_cap=slot_cap),
        session_factory=_null_session_factory,
        enable_signal_triggers=False,
    )
    return governor, queue, store, locks


def test_a_plain_wave_admits_pending_work(monkeypatch):
    governor, queue, store, _ = _governor(jobs=[_job("job-1"), _job("job-2")])

    result = governor.run_wave(now=NOW)

    assert result.admitted == 2
    assert store.get_job("job-1").status is JobStatus.ADMITTED
    assert queue.admitted_depth() == 2


def test_worker_backpressure_blocks_a_destination_without_deferring_its_jobs(monkeypatch):
    """Exhausted worker headroom is a "come back next wave" signal, not a job-level verdict.

    Deferring would be wrong: nothing about these jobs is unsuitable, the fleet just cannot
    drain any more right now. Failing them was the 2026-09-21 incident.
    """
    governor, queue, store, _ = _governor(jobs=[_job("job-1"), _job("job-2")])
    monkeypatch.setattr(Governor, "_worker_drain_headroom", lambda self: 0)

    result = governor.run_wave(now=NOW)

    assert result.admitted == 0
    assert result.blocked == 2
    assert result.deferred == 0
    assert store.get_job("job-1").status is JobStatus.QUEUED
    assert queue.is_pending("job-1", DEST)


def test_worker_backpressure_admits_exactly_the_headroom_available(monkeypatch):
    jobs = [_job(f"job-{i}") for i in range(6)]
    governor, _, _, _ = _governor(jobs=jobs)
    monkeypatch.setattr(Governor, "_worker_drain_headroom", lambda self: 2)

    result = governor.run_wave(now=NOW)

    assert result.admitted == 2


def test_headroom_is_spent_once_across_destinations():
    """Two destinations must not each admit the full fleet headroom."""
    headroom = _WorkerDrainHeadroom(3)

    first = headroom.clamp(["r1", "r2"])
    second = headroom.clamp(["r1", "r2"])

    assert len(first) == 2
    assert len(second) == 1
    assert headroom.exhausted()


def test_unknown_headroom_leaves_admission_untouched():
    """The queue not reporting worker presence must not wedge admission fleet-wide."""
    headroom = _WorkerDrainHeadroom(None)

    assert headroom.clamp(["r1", "r2", "r3"]) == ["r1", "r2", "r3"]
    assert not headroom.exhausted()


def test_a_job_deferred_by_a_gate_does_not_hold_a_table_lock(monkeypatch):
    """The table lock is taken late for exactly this reason. A gate that refuses before it
    must not leave the table locked against the next wave."""
    governor, _, store, locks = _governor(jobs=[_job("job-1", table_id="table-1")])
    monkeypatch.setattr(
        Governor, "_gate_paused", lambda self, job, wave: self._defer(job, "paused") or "deferred"
    )

    result = governor.run_wave(now=NOW)

    assert result.deferred == 1
    assert not locks.is_table_locked("table-1")
    assert store.get_job("job-1").status is JobStatus.DEFERRED


def test_a_job_refused_after_the_lock_is_taken_releases_it(monkeypatch):
    """QPP is the one gate that runs after the lock, so it owns releasing it."""
    governor, _, _, locks = _governor(jobs=[_job("job-1", table_id="table-1")])
    monkeypatch.setattr(governor, "_qpp_enabled", True)
    monkeypatch.setattr(
        "cryo.governor.governor._evaluate_qpp_for_governor", lambda **_kw: (False, 9_000.0)
    )

    result = governor.run_wave(now=NOW)

    assert result.deferred == 1
    assert not locks.is_table_locked("table-1")


def test_an_admitted_job_holds_its_table_lock(monkeypatch):
    governor, _, _, locks = _governor(jobs=[_job("job-1", table_id="table-1")])

    assert governor.run_wave(now=NOW).admitted == 1
    assert locks.is_table_locked("table-1")


def test_two_jobs_for_the_same_table_cannot_both_be_admitted():
    governor, _, store, _ = _governor(
        jobs=[_job("job-1", table_id="shared"), _job("job-2", table_id="shared")]
    )

    result = governor.run_wave(now=NOW)

    assert result.admitted == 1
    assert result.deferred == 1
    statuses = {store.get_job(j).status for j in ("job-1", "job-2")}
    assert statuses == {JobStatus.ADMITTED, JobStatus.DEFERRED}


def test_queue_entries_for_unknown_jobs_are_purged_as_orphans():
    queue = InMemoryMaintenanceQueue()
    governor, _, _, _ = _governor(jobs=[_job("job-1")], queue=queue)
    queue.enqueue("job-ghost", DEST)

    result = governor.run_wave(now=NOW)

    assert result.orphaned >= 1
    assert not queue.is_pending("job-ghost", DEST)


def test_wave_totals_and_per_destination_breakdown_cannot_disagree():
    """They were two hand-maintained counters at ~20 branches before."""
    tally = _WaveTally()

    tally.record("dest-a", "admitted", 3)
    tally.record("dest-a", "deferred")
    tally.record("dest-b", "admitted", 2)
    tally.record("dest-b", "blocked", 0)

    assert tally.admitted == 5
    assert tally.deferred == 1
    assert tally.blocked == 0
    assert dict(tally.per_destination["dest-a"]) == {"admitted": 3, "deferred": 1}
    assert dict(tally.per_destination["dest-b"]) == {"admitted": 2}


def test_a_wave_with_nothing_queued_is_a_no_op():
    governor, _, _, _ = _governor()

    result = governor.run_wave(now=NOW)

    assert (result.admitted, result.deferred, result.blocked) == (0, 0, 0)
