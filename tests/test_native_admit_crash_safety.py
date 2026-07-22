"""Regression coverage for a real incident, 2026-07-22: `admit_native_k8s_jobs` had several
calls (pack_jobs, cluster_headroom, the create_dispatch_record/mark_dispatch_running DB
writes) that weren't individually guarded. When one of them raised, the exception propagated
straight out of the function -- past every `_defer_spec`/`release_lock` cleanup path -- while
the caller (governor.py) had already acquired each job's table lock before buffering it here.
The lock orphaned silently (logging was also broken in prod, so nothing surfaced), and the
job's next admission attempt failed to re-acquire its own stale lock, deferring with
"table_locked" forever -- with no code path releasing it until the 6h TTL expired.
"""

from __future__ import annotations

from unittest.mock import patch

from cryo.domain.enums import Mode
from cryo.governor.native_admit import admit_native_k8s_jobs
from cryo.worker.worker import (
    DestinationConfig,
    InMemoryJobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
)


def _job(job_id: str, table_id: str) -> MaintenanceJobRecord:
    return MaintenanceJobRecord(
        id=job_id,
        run_id="r1",
        table_id=table_id,
        destination_id="dest-1",
        mode=Mode.STANDARD,
        params={},
        operations=None,
    )


def _store_with_job(job: MaintenanceJobRecord) -> InMemoryJobStore:
    store = InMemoryJobStore()
    store.add_job(job)
    store.add_table_data(
        job.table_id,
        TableMaintenanceData(
            stats={"total_files": 100, "total_size_gb": 10},
            partitions=(),
            source_config={"uri": "http://rest:8181", "warehouse": "s3://wh/"},
        ),
    )
    return store


def test_unhandled_exception_mid_dispatch_releases_lock_and_defers() -> None:
    job = _job("job-1", "table-1")
    store = _store_with_job(job)
    destination = DestinationConfig(id="dest-1", engine="native", config={"k8s_dispatch": True})

    deferred_calls: list[tuple[str, str]] = []
    released_calls: list[tuple[str, str]] = []

    def defer_job(job_id: str, reason: str) -> None:
        deferred_calls.append((job_id, reason))

    def release_lock(table_id: str, job_id: str) -> None:
        released_calls.append((table_id, job_id))

    with (
        patch("cryo.governor.native_admit.k8s_dispatch_available", return_value=True),
        patch(
            "cryo.governor.native_admit.pack_jobs",
            side_effect=RuntimeError("simulated packing failure"),
        ),
    ):
        result = admit_native_k8s_jobs(
            jobs=[job],
            destination=destination,
            store=store,
            session_factory=lambda: None,
            live_capacity={},
            mark_status=store.mark_status,
            cancel_pending=lambda *_: None,
            defer_job=defer_job,
            release_lock=release_lock,
        )

    # The job must never be silently dropped: it's deferred (so it re-enters the
    # queue) AND its lock is released (so the next attempt can actually re-acquire
    # it instead of self-blocking on "table_locked" forever).
    assert result.admitted == 0
    assert result.deferred == 1
    assert deferred_calls == [("job-1", "native_admit_unexpected_error")]
    assert released_calls == [("table-1", "job-1")]


def test_one_job_crashing_does_not_orphan_a_sibling_jobs_lock() -> None:
    """Two jobs prepared fine; the crash happens after prepare (in pack_jobs), so both
    must come out of the safety net -- the bug wasn't scoped to a single job."""
    job_a = _job("job-a", "table-a")
    job_b = _job("job-b", "table-b")
    store = _store_with_job(job_a)
    store.add_table_data(
        job_b.table_id,
        TableMaintenanceData(
            stats={"total_files": 50, "total_size_gb": 5},
            partitions=(),
            source_config={"uri": "http://rest:8181", "warehouse": "s3://wh/"},
        ),
    )
    store.add_job(job_b)
    destination = DestinationConfig(id="dest-1", engine="native", config={"k8s_dispatch": True})

    released: set[tuple[str, str]] = set()
    deferred: set[tuple[str, str]] = set()

    with (
        patch("cryo.governor.native_admit.k8s_dispatch_available", return_value=True),
        patch(
            "cryo.governor.native_admit.cluster_headroom",
            side_effect=RuntimeError("simulated headroom failure"),
        ),
    ):
        result = admit_native_k8s_jobs(
            jobs=[job_a, job_b],
            destination=destination,
            store=store,
            session_factory=lambda: None,
            live_capacity={},
            mark_status=store.mark_status,
            cancel_pending=lambda *_: None,
            defer_job=lambda jid, reason: deferred.add((jid, reason)),
            release_lock=lambda tid, jid: released.add((tid, jid)),
        )

    assert result.admitted == 0
    assert result.deferred == 2
    assert released == {("table-a", "job-a"), ("table-b", "job-b")}
    assert deferred == {
        ("job-a", "native_admit_unexpected_error"),
        ("job-b", "native_admit_unexpected_error"),
    }
