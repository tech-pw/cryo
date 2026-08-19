"""Tests for NativeJobWatcher's PDAE-8546 fix: salvaging real per-table outcomes from
run_events when a batched native dispatch dies with no recoverable pod logs.

Root cause this addresses: K8s deletes a Job's pod immediately on activeDeadlineSeconds,
unlike a normal exit-code failure (where the pod lingers for log collection) -- confirmed
live, 2026-08-19, against real PDAE-8274 (Wave 1) job history: 98% of 5,553 native_job_failed
failures clustered in the same 120-130 minute band as the old 2h deadline, and even
*successful* batches routinely finished at 114-132 minutes. Every table in a deadline-killed
batch previously lost its own real outcome -- including ones that had already genuinely
succeeded -- because _read_pod_logs found nothing once the pod was gone.
_publish_incremental_progress already streams each member's real CRYO_TABLE_RESULT to
run_events every ~30s while the pod is alive; this is the only surviving record once it's not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.engines.native.watcher import NativeJobWatcher
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.run_events import InMemoryRunEventBus, build_log_line
from tests.conftest import (
    _assign_missing_autoincrement_primary_keys,
    _assign_missing_uuid_primary_keys,
)


def _bound_session_factory(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    event.listen(factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    return factory


def _watcher(session, *, run_events=None) -> NativeJobWatcher:
    marked: list[tuple[str, str, str | None]] = []

    def mark_status(job_id, status, *, error=None):
        marked.append((job_id, str(status), error))

    watcher = NativeJobWatcher(
        session_factory=_bound_session_factory(session),
        lock_store=InMemoryLockStore(),
        mark_status=mark_status,
        run_events=run_events,
    )
    watcher._marked = marked  # type: ignore[attr-defined]
    return watcher


def _seed_dispatch_jobs(session, *, n: int) -> tuple[uuid.UUID, list[MaintenanceJob]]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(
        id=uuid4(), name=f"dest-{uuid4()}", engine="native", config={}
    )
    session.add_all([source, destination])
    session.flush()

    run = MaintenanceRun(
        id=uuid4(), kind="scheduled", trigger="cron", status="running", summary={}
    )
    session.add(run)
    session.flush()

    dispatch_id = uuid4()
    jobs = []
    for i in range(n):
        table = Table(
            id=uuid4(), source_id=source.id, schema_name="s", table_name=f"t{i}", is_active=True
        )
        session.add(table)
        session.flush()
        job = MaintenanceJob(
            id=uuid4(),
            run_id=run.id,
            table_id=table.id,
            destination_id=destination.id,
            dispatch_id=dispatch_id,
            mode="standard",
            status="running",
        )
        session.add(job)
        jobs.append(job)
    session.commit()
    return run.id, jobs


# ---------------------------------------------------------------------------
# _salvage_from_run_events
# ---------------------------------------------------------------------------


def test_salvage_returns_empty_with_no_run_events(session) -> None:
    watcher = _watcher(session, run_events=InMemoryRunEventBus())
    assert watcher._salvage_from_run_events(uuid4()) == {}


def test_salvage_returns_empty_when_run_events_is_none(session) -> None:
    watcher = _watcher(session, run_events=None)
    assert watcher._salvage_from_run_events(uuid4()) == {}


def test_salvage_credits_a_job_with_a_clean_info_event(session) -> None:
    bus = InMemoryRunEventBus()
    run_id = uuid4()
    job_id = str(uuid4())
    bus.publish(
        run_id,
        build_log_line(run_id=run_id, job_id=job_id, message="optimize: done", level="info"),
    )
    watcher = _watcher(session, run_events=bus)

    salvage = watcher._salvage_from_run_events(run_id)

    assert salvage == {job_id: True}


def test_salvage_excludes_a_job_with_an_error_event(session) -> None:
    bus = InMemoryRunEventBus()
    run_id = uuid4()
    job_id = str(uuid4())
    bus.publish(
        run_id,
        build_log_line(
            run_id=run_id, job_id=job_id, message="optimize: failed — boom", level="error"
        ),
    )
    watcher = _watcher(session, run_events=bus)

    salvage = watcher._salvage_from_run_events(run_id)

    assert salvage == {}


def test_salvage_excludes_a_job_with_mixed_success_and_error_events(session) -> None:
    """A table whose plan has multiple operations (e.g. expire_snapshots + optimize) is not
    a clean success just because one operation's line was info-level -- the whole job's
    streamed history must be clean before it's credited."""
    bus = InMemoryRunEventBus()
    run_id = uuid4()
    job_id = str(uuid4())
    bus.publish(
        run_id,
        build_log_line(
            run_id=run_id, job_id=job_id, message="expire_snapshots: done", level="info"
        ),
    )
    bus.publish(
        run_id,
        build_log_line(
            run_id=run_id, job_id=job_id, message="optimize: failed — boom", level="error"
        ),
    )
    watcher = _watcher(session, run_events=bus)

    salvage = watcher._salvage_from_run_events(run_id)

    assert salvage == {}


def test_salvage_only_credits_jobs_with_a_streamed_event(session) -> None:
    """A job that never streamed anything (pod died before it was even reached) must not
    appear in the salvage map at all -- absence means genuinely unknown, not success."""
    bus = InMemoryRunEventBus()
    run_id = uuid4()
    seen_job = str(uuid4())
    never_reached_job = str(uuid4())
    bus.publish(
        run_id,
        build_log_line(run_id=run_id, job_id=seen_job, message="optimize: done", level="info"),
    )
    watcher = _watcher(session, run_events=bus)

    salvage = watcher._salvage_from_run_events(run_id)

    assert salvage == {seen_job: True}
    assert never_reached_job not in salvage


def test_salvage_ignores_non_log_line_events(session) -> None:
    bus = InMemoryRunEventBus()
    run_id = uuid4()
    bus.publish(run_id, {"type": "progress", "job_id": str(uuid4())})
    watcher = _watcher(session, run_events=bus)

    assert watcher._salvage_from_run_events(run_id) == {}


def test_salvage_swallows_a_broken_event_bus(session) -> None:
    class _BrokenBus:
        def replay(self, run_id, *, count=200):
            raise RuntimeError("redis is down")

    watcher = _watcher(session, run_events=_BrokenBus())

    assert watcher._salvage_from_run_events(uuid4()) == {}


# ---------------------------------------------------------------------------
# _finalize_failure with salvage
# ---------------------------------------------------------------------------


def test_finalize_failure_with_no_salvage_fails_every_job(session) -> None:
    _run_id, jobs = _seed_dispatch_jobs(session, n=3)
    dispatch_id = jobs[0].dispatch_id
    watcher = _watcher(session)

    watcher._finalize_failure(session, dispatch_id, "native_job_failed")

    statuses = {str(job_id): status for job_id, status, _ in watcher._marked}
    assert all(status == "failed" for status in statuses.values())
    assert len(statuses) == 3


def test_finalize_failure_salvages_jobs_present_in_the_salvage_map(session) -> None:
    _run_id, jobs = _seed_dispatch_jobs(session, n=3)
    dispatch_id = jobs[0].dispatch_id
    watcher = _watcher(session)

    salvage = {str(jobs[0].id): True, str(jobs[1].id): True}
    watcher._finalize_failure(session, dispatch_id, "native_job_failed", salvage=salvage)

    outcomes = {str(job_id): status for job_id, status, _ in watcher._marked}
    assert outcomes[str(jobs[0].id)] == "succeeded"
    assert outcomes[str(jobs[1].id)] == "succeeded"
    assert outcomes[str(jobs[2].id)] == "failed"


def test_finalize_failure_salvaged_jobs_get_no_error(session) -> None:
    _run_id, jobs = _seed_dispatch_jobs(session, n=1)
    dispatch_id = jobs[0].dispatch_id
    watcher = _watcher(session)

    salvage = {str(jobs[0].id): True}
    watcher._finalize_failure(session, dispatch_id, "native_job_failed", salvage=salvage)

    _job_id, status, error = watcher._marked[0]
    assert status == "succeeded"
    assert error is None


def test_finalize_failure_a_job_not_in_salvage_map_still_fails_with_the_real_reason(
    session,
) -> None:
    _run_id, jobs = _seed_dispatch_jobs(session, n=1)
    dispatch_id = jobs[0].dispatch_id
    watcher = _watcher(session)

    watcher._finalize_failure(session, dispatch_id, "native_job_failed: deadline exceeded")

    _job_id, status, error = watcher._marked[0]
    assert status == "failed"
    assert error == "native_job_failed: deadline exceeded"
