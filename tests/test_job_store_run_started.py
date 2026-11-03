"""SQLJobStore.mark_status moves a run from queued to running when a job is admitted or starts.

#384: maintenance runs were created "queued" and went straight to a terminal status, so a run
doing work showed "queued" and the stuck-run health check (status == "running") never saw it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.job_store import SQLJobStore
from cryo.db.repositories.run_finalizer import finalize_open_runs
from cryo.domain.enums import JobStatus


def _seed(session, *, run_status: str = "queued", jobs: int = 1) -> tuple[MaintenanceRun, list]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={})
    session.add_all([source, destination])
    session.flush()
    run = MaintenanceRun(id=uuid4(), kind="scheduled", trigger="cron", status=run_status)
    session.add(run)
    session.flush()
    rows = []
    for i in range(jobs):
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
            mode="standard",
            status="queued",
        )
        session.add(job)
        rows.append(job)
    session.commit()
    return run, rows


def _run_status(session_factory: sessionmaker, run_id) -> str:
    with session_factory() as fresh:
        run = fresh.get(MaintenanceRun, run_id)
        assert run is not None
        return run.status


@pytest.mark.parametrize("status", [JobStatus.ADMITTED, JobStatus.RUNNING])
def test_first_admitted_or_started_job_moves_queued_run_to_running(
    session, session_factory, status: JobStatus
) -> None:
    run, (job,) = _seed(session)
    store = SQLJobStore(session_factory)

    store.mark_status(str(job.id), status)

    assert _run_status(session_factory, run.id) == "running"


@pytest.mark.parametrize(
    "status", [JobStatus.QUEUED, JobStatus.DEFERRED, JobStatus.SKIPPED, JobStatus.FAILED]
)
def test_jobs_that_never_start_leave_the_run_queued(
    session, session_factory, status: JobStatus
) -> None:
    run, (job,) = _seed(session)
    store = SQLJobStore(session_factory)

    store.mark_status(str(job.id), status)

    assert _run_status(session_factory, run.id) == "queued"


@pytest.mark.parametrize("run_status", ["scheduled", "cancelled", "succeeded", "partial", "failed"])
def test_only_a_queued_run_moves(session, session_factory, run_status: str) -> None:
    run, (job,) = _seed(session, run_status=run_status)
    store = SQLJobStore(session_factory)

    store.mark_status(str(job.id), JobStatus.ADMITTED)

    assert _run_status(session_factory, run.id) == run_status


def test_transition_is_idempotent_across_jobs_and_steps(session, session_factory) -> None:
    run, jobs = _seed(session, jobs=3)
    store = SQLJobStore(session_factory)

    for job in jobs:
        store.mark_status(str(job.id), JobStatus.ADMITTED)
        store.mark_status(str(job.id), JobStatus.RUNNING)

    assert _run_status(session_factory, run.id) == "running"
    with session_factory() as fresh:
        statuses = {j.status for j in fresh.query(MaintenanceJob).filter_by(run_id=run.id)}
    assert statuses == {"running"}


def test_finalizer_closes_a_run_that_went_running(session, session_factory) -> None:
    run, jobs = _seed(session, jobs=2)
    store = SQLJobStore(session_factory)
    for job in jobs:
        store.mark_status(str(job.id), JobStatus.RUNNING)
    assert _run_status(session_factory, run.id) == "running"

    store.mark_status(str(jobs[0].id), JobStatus.SUCCEEDED)
    assert finalize_open_runs(session_factory) == 0
    assert _run_status(session_factory, run.id) == "running"

    store.mark_status(str(jobs[1].id), JobStatus.SUCCEEDED)
    assert finalize_open_runs(session_factory) == 1
    assert _run_status(session_factory, run.id) == "succeeded"
