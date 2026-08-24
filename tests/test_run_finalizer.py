"""Tests for cryo.db.repositories.run_finalizer -- fixes a live bug found 2026-08-24 while
digging into the production health checklist's stuck_runs CRITICAL alarm: 20 runs stuck at
status="running" for up to 321h (13+ days), even though every single job under each of them had
already reached a terminal state. Root cause: TERMINAL excluded JobStatus.DEFERRED, and the
governor marks a job DEFERRED (not requeued, not re-transitioned) when it chooses not to admit
it this cycle for capacity/cooldown/guardrail reasons -- see governor.py's _defer_native_job.
A run with any deferred jobs could therefore never finalize, no matter how long it waited.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.run_finalizer import finalize_open_runs

NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)


def _seed_source_table(session, *, name: str = "t") -> tuple[Source, Table]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(), source_id=source.id, schema_name="s", table_name=name, is_active=True
    )
    session.add_all([source, table])
    session.flush()
    return source, table


def _seed_destination(session) -> Destination:
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="native", config={})
    session.add(destination)
    session.flush()
    return destination


def _seed_run(session, *, status: str = "running", created_at: datetime = NOW) -> MaintenanceRun:
    run = MaintenanceRun(id=uuid4(), kind="scheduled", status=status, trigger="cron", created_at=created_at)
    session.add(run)
    session.flush()
    return run


def _seed_job(session, *, run: MaintenanceRun, table: Table, destination: Destination, status: str) -> MaintenanceJob:
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status=status,
    )
    session.add(job)
    session.flush()
    return job


def test_run_with_only_deferred_jobs_finalizes_instead_of_staying_open_forever(session, session_factory) -> None:
    """Direct regression test for the live incident: a run whose jobs are 100% deferred (the
    governor declined to admit any of them this cycle) must not be stuck at status="running"
    indefinitely -- it has nothing left in flight."""
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session, created_at=NOW - timedelta(hours=300))
    for _ in range(3):
        _seed_job(session, run=run, table=table, destination=destination, status="deferred")
    session.commit()

    finalized = finalize_open_runs(session_factory)

    session.refresh(run)
    assert finalized == 1
    assert run.status != "running"
    assert run.finished_at is not None


def test_deferred_only_run_is_not_mislabeled_failed(session, session_factory) -> None:
    """Nothing actually failed -- the governor just chose not to admit any of it. Labeling this
    'failed' would misdirect anyone investigating it toward a nonexistent job-level failure."""
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session)
    for _ in range(2):
        _seed_job(session, run=run, table=table, destination=destination, status="deferred")
    session.commit()

    finalize_open_runs(session_factory)

    session.refresh(run)
    assert run.status == "partial"


def test_mix_of_succeeded_and_deferred_finalizes_as_partial(session, session_factory) -> None:
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session)
    _seed_job(session, run=run, table=table, destination=destination, status="succeeded")
    _seed_job(session, run=run, table=table, destination=destination, status="deferred")
    session.commit()

    finalize_open_runs(session_factory)

    session.refresh(run)
    assert run.status == "partial"
    assert run.summary["deferred"] == 1
    assert run.summary["succeeded"] == 1


def test_run_still_stays_open_while_a_job_is_genuinely_in_flight(session, session_factory) -> None:
    """Regression guard the other direction: this fix must not make finalize_open_runs
    over-eager -- a run with a queued/running/admitted job must still be left alone."""
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session)
    _seed_job(session, run=run, table=table, destination=destination, status="succeeded")
    _seed_job(session, run=run, table=table, destination=destination, status="queued")
    session.commit()

    finalize_open_runs(session_factory)

    session.refresh(run)
    assert run.status == "running"
    assert run.finished_at is None


def test_all_succeeded_still_finalizes_as_succeeded(session, session_factory) -> None:
    """Baseline behavior (no deferred jobs involved) must be unchanged by this fix."""
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session)
    for _ in range(3):
        _seed_job(session, run=run, table=table, destination=destination, status="succeeded")
    session.commit()

    finalize_open_runs(session_factory)

    session.refresh(run)
    assert run.status == "succeeded"


def test_cancelled_run_is_never_touched(session, session_factory) -> None:
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session, status="cancelled")
    _seed_job(session, run=run, table=table, destination=destination, status="deferred")
    session.commit()

    finalize_open_runs(session_factory)

    session.refresh(run)
    assert run.status == "cancelled"
