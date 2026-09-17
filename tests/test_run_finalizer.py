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


def _seed_job(
    session,
    *,
    run: MaintenanceRun,
    table: Table,
    destination: Destination,
    status: str,
    finished_at: datetime | None = None,
    error: str | None = None,
) -> MaintenanceJob:
    if finished_at is None and status in {"succeeded", "failed", "skipped", "deferred", "partial"}:
        # Real-world default: mark_status() stamps finished_at the instant a job reaches one of
        # these statuses (job_store.py). Age it well past the finalize grace window by default so
        # existing tests (written before the grace window existed) keep exercising "long since
        # deferred, safe to finalize" rather than accidentally landing inside the grace window.
        finished_at = NOW - timedelta(hours=1)
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status=status,
        finished_at=finished_at,
        error=error,
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

    finalized = finalize_open_runs(session_factory, now=NOW)

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

    finalize_open_runs(session_factory, now=NOW)

    session.refresh(run)
    assert run.status == "partial"


def test_mix_of_succeeded_and_deferred_finalizes_as_partial(session, session_factory) -> None:
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session)
    _seed_job(session, run=run, table=table, destination=destination, status="succeeded")
    _seed_job(session, run=run, table=table, destination=destination, status="deferred")
    session.commit()

    finalize_open_runs(session_factory, now=NOW)

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

    finalize_open_runs(session_factory, now=NOW)

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

    finalize_open_runs(session_factory, now=NOW)

    session.refresh(run)
    assert run.status == "succeeded"


def test_cancelled_run_is_never_touched(session, session_factory) -> None:
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session, status="cancelled")
    _seed_job(session, run=run, table=table, destination=destination, status="deferred")
    session.commit()

    finalize_open_runs(session_factory, now=NOW)

    session.refresh(run)
    assert run.status == "cancelled"


def test_freshly_deferred_job_does_not_finalize_the_run_yet(session, session_factory) -> None:
    """Direct regression test for the 2026-09-08 incident: 309 jobs/48h failed with
    'orphaned_deferred: parent run already finalized' because a run finalized the wave right
    after its last job went DEFERRED for a transient reason (approval pending, fleet-budget,
    table lock, QPP) -- before reconcile_deferred_jobs (which runs every wave, ~30s cadence)
    ever got a real chance to un-defer it. A job deferred moments ago must not count toward
    'run is done' yet."""
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session)
    _seed_job(session, run=run, table=table, destination=destination, status="succeeded")
    _seed_job(
        session,
        run=run,
        table=table,
        destination=destination,
        status="deferred",
        finished_at=NOW - timedelta(minutes=1),
    )
    session.commit()

    finalize_open_runs(session_factory, now=NOW)

    session.refresh(run)
    assert run.status == "running"
    assert run.finished_at is None


def test_deferred_job_finalizes_the_run_once_past_the_grace_window(session, session_factory) -> None:
    """The other half of the same fix: this must not regress back into the original 2026-08-24
    bug (runs stuck open forever) -- a DEFERRED job that has genuinely had multiple wave cycles
    to un-defer and didn't must still let the run finalize."""
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session)
    _seed_job(session, run=run, table=table, destination=destination, status="succeeded")
    _seed_job(
        session,
        run=run,
        table=table,
        destination=destination,
        status="deferred",
        finished_at=NOW - timedelta(minutes=11),
    )
    session.commit()

    finalize_open_runs(session_factory, now=NOW)

    session.refresh(run)
    assert run.status == "partial"
    assert run.finished_at is not None


def test_mix_of_recently_and_long_deferred_jobs_waits_for_the_freshest_one(session, session_factory) -> None:
    """If even one deferred job under the run is still inside its grace window, the whole run
    must stay open -- a partially-eager finalize would still orphan that one fresh job the next
    time it un-defers."""
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session)
    _seed_job(
        session,
        run=run,
        table=table,
        destination=destination,
        status="deferred",
        finished_at=NOW - timedelta(hours=2),
    )
    _seed_job(
        session,
        run=run,
        table=table,
        destination=destination,
        status="deferred",
        finished_at=NOW - timedelta(seconds=30),
    )
    session.commit()

    finalize_open_runs(session_factory, now=NOW)

    session.refresh(run)
    assert run.status == "running"


def test_capacity_deferred_job_does_not_orphan_within_ten_minutes(session, session_factory) -> None:
    """Direct regression test for the 2026-09-15/16 incident: 29,832 jobs (~99% of that day's
    failures) were fast-failed as 'orphaned_deferred: parent run already finalized' because
    finalize_open_runs used the same 10-minute grace for a fleet-wide capacity defer
    (metastore_capacity) as for job-specific reasons -- but max_concurrent_jobs_per_metastore
    stayed over cap for HOURS, not minutes, during the incident. A job deferred on a capacity
    reason 20 minutes ago (well past the old 10-minute grace, nowhere near the new capacity
    grace) must still keep its run open."""
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session)
    _seed_job(session, run=run, table=table, destination=destination, status="succeeded")
    _seed_job(
        session,
        run=run,
        table=table,
        destination=destination,
        status="deferred",
        finished_at=NOW - timedelta(minutes=20),
        error="metastore_capacity:thrift://trino-prod-metastore.example.com:9083",
    )
    session.commit()

    finalize_open_runs(session_factory, now=NOW)

    session.refresh(run)
    assert run.status == "running"
    assert run.finished_at is None


def test_capacity_deferred_job_finalizes_once_past_its_own_longer_grace(session, session_factory) -> None:
    """The other half: a capacity-deferred job that has genuinely sat deferred for the full
    capacity grace window (hours, not minutes) must still let the run finalize eventually --
    this fix must not regress into the original 2026-08-24 bug (runs stuck open forever)."""
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    run = _seed_run(session)
    _seed_job(session, run=run, table=table, destination=destination, status="succeeded")
    _seed_job(
        session,
        run=run,
        table=table,
        destination=destination,
        status="deferred",
        finished_at=NOW - timedelta(hours=6, minutes=1),
        error="metastore_capacity:thrift://trino-prod-metastore.example.com:9083",
    )
    session.commit()

    finalize_open_runs(session_factory, now=NOW)

    session.refresh(run)
    assert run.status == "partial"
    assert run.finished_at is not None


def test_native_cluster_capacity_and_fleet_rate_cap_also_get_the_longer_grace(
    session, session_factory
) -> None:
    """The other two capacity-class defer reasons (native_admit.py) must be recognized the same
    way as metastore_capacity -- not just the one reason the original incident happened to hit."""
    _, table = _seed_source_table(session)
    destination = _seed_destination(session)
    for reason in ("native_cluster_capacity", "fleet_admission_rate_cap"):
        run = _seed_run(session)
        _seed_job(
            session,
            run=run,
            table=table,
            destination=destination,
            status="deferred",
            finished_at=NOW - timedelta(minutes=20),
            error=reason,
        )
        session.commit()

        finalize_open_runs(session_factory, now=NOW)

        session.refresh(run)
        assert run.status == "running", f"reason={reason} should still be in its capacity grace"
