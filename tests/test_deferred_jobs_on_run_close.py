"""A run that closes with jobs still deferred skips them with their reason (#383).

End to end over SQLJobStore: the run passes its deadline, the finalizer closes it once the
deferral grace is over, and reconcile_deferred_jobs records the leftover jobs as skipped
instead of failing them as orphaned.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.job_store import SQLJobStore
from cryo.db.repositories.run_finalizer import finalize_open_runs
from cryo.queue.reconcile import reconcile_deferred_jobs
from cryo.queue.redis_queue import InMemoryMaintenanceQueue

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


def test_closed_run_skips_its_deferred_jobs_with_the_deferral_reason(
    session, session_factory
) -> None:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={})
    session.add_all([source, destination])
    session.flush()
    run = MaintenanceRun(
        id=uuid4(),
        kind="scheduled",
        trigger="cron",
        status="queued",
        created_at=NOW - timedelta(days=1),
        deadline_at=NOW - timedelta(hours=1),
    )
    session.add(run)
    session.flush()
    jobs = {}
    for name, status, error in (
        ("done", "succeeded", None),
        ("late", "deferred", "deadline_near"),
        ("unapproved", "deferred", "Destructive operation requires approval"),
    ):
        table = Table(
            id=uuid4(), source_id=source.id, schema_name="s", table_name=name, is_active=True
        )
        session.add(table)
        session.flush()
        job = MaintenanceJob(
            id=uuid4(),
            run_id=run.id,
            table_id=table.id,
            destination_id=destination.id,
            mode="standard",
            status=status,
            error=error,
            finished_at=NOW - timedelta(hours=2),
            deferred_since=NOW - timedelta(hours=2) if status == "deferred" else None,
        )
        session.add(job)
        jobs[name] = job.id
    session.commit()

    assert finalize_open_runs(session_factory, now=NOW) == 1
    reconcile_deferred_jobs(SQLJobStore(session_factory), InMemoryMaintenanceQueue(), now=NOW)

    with session_factory() as fresh:
        closed = fresh.get(MaintenanceRun, run.id)
        assert closed is not None
        assert closed.status == "partial"
        late = fresh.get(MaintenanceJob, jobs["late"])
        unapproved = fresh.get(MaintenanceJob, jobs["unapproved"])
        assert late is not None and unapproved is not None
        assert (late.status, late.error) == (
            "skipped",
            "run_closed_while_deferred: deadline_near",
        )
        assert (unapproved.status, unapproved.error) == (
            "skipped",
            "run_closed_while_deferred: Destructive operation requires approval",
        )
        assert late.deferred_since is None
        failed = fresh.query(MaintenanceJob).filter_by(run_id=run.id, status="failed").count()
        assert failed == 0
