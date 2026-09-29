"""cli.py::_worker_scale_metric -- the metric KEDA's cryo-worker ScaledObject now scales on
(see keda-worker-scaledobject.yaml). Regression, 2026-07-31: the previous Redis admitted-list
metric never reflected sustained worker occupancy; this counts ADMITTED+RUNNING jobs directly
from Postgres, which stays elevated for a job's whole duration."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from cryo.cli import _worker_scale_metric
from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table


def _seed_job(session, *, status: str, pipeline: str | None = None) -> None:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(
        id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={"host": "trino"}
    )
    table = Table(id=uuid4(), source_id=source.id, schema_name="analytics", table_name="events")
    run = MaintenanceRun(
        id=uuid4(),
        kind="adhoc",
        trigger="api",
        status="queued",
        summary={"pipeline": pipeline} if pipeline is not None else {},
    )
    session.add_all([source, destination, table, run])
    session.flush()
    session.add(
        MaintenanceJob(
            id=uuid4(),
            run_id=run.id,
            table_id=table.id,
            destination_id=destination.id,
            mode="standard",
            status=status,
        )
    )
    session.commit()


def test_counts_only_admitted_and_running_jobs(session, monkeypatch) -> None:
    for status in ["admitted", "running", "running", "queued", "succeeded", "failed", "skipped"]:
        _seed_job(session, status=status)

    test_session_factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr("cryo.db.session.SessionLocal", test_session_factory)

    assert (
        _worker_scale_metric() == 3
    )  # 1 admitted + 2 running; queued/succeeded/failed/skipped excluded


def test_zero_when_nothing_active(session, monkeypatch) -> None:
    for status in ["queued", "succeeded", "failed", "skipped", "deferred"]:
        _seed_job(session, status=status)

    test_session_factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr("cryo.db.session.SessionLocal", test_session_factory)

    assert _worker_scale_metric() == 0


def test_watcher_owned_dispatches_do_not_request_worker_replicas(session, monkeypatch) -> None:
    _seed_job(session, status="running")
    session.execute(update(MaintenanceJob).values(dispatch_id=uuid4()))
    session.commit()
    _seed_job(session, status="running")
    _seed_job(session, status="admitted")
    monkeypatch.setattr(
        "cryo.db.session.SessionLocal",
        sessionmaker(bind=session.get_bind(), expire_on_commit=False),
    )
    assert _worker_scale_metric() == 2


@pytest.mark.parametrize("status", ["admitted", "running"])
def test_coordinator_owned_batch_jobs_do_not_scale_workers(session, monkeypatch, status) -> None:
    _seed_job(session, status=status, pipeline="batch")
    _seed_job(session, status="running")
    _seed_job(session, status="admitted", pipeline="worker")
    monkeypatch.setattr(
        "cryo.db.session.SessionLocal",
        sessionmaker(bind=session.get_bind(), expire_on_commit=False),
    )

    assert _worker_scale_metric() == 2


def test_only_batch_work_returns_zero_worker_demand(session, monkeypatch) -> None:
    _seed_job(session, status="running", pipeline="batch")
    monkeypatch.setattr(
        "cryo.db.session.SessionLocal",
        sessionmaker(bind=session.get_bind(), expire_on_commit=False),
    )

    assert _worker_scale_metric() == 0
