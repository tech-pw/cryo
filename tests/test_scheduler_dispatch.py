"""Tests for SchedulerService._dispatch_scheduled_runs -- the one-off future-scheduled
maintenance run feature (docs/ux-improvement-loop.md).

Same session-patching pattern as tests/test_scheduler_telemetry.py: the scheduler opens its
own sessions via SessionLocal() rather than accepting an injected one, so tests monkeypatch
cryo.scheduler.service.SessionLocal to point at a factory bound to the test session's engine.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler.service import SchedulerService
from tests.conftest import (
    _assign_missing_autoincrement_primary_keys,
    _assign_missing_uuid_primary_keys,
)


def _bound_session_factory(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    event.listen(factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    return factory


class _NullStore:
    def list_due_presets(self, now):
        return []

    def list_tables(self):
        return []

    def list_presets(self):
        return []

    def list_rules(self):
        return []

    def list_assignments(self):
        return []

    def list_destinations(self):
        return []

    def persist_run(self, run):
        pass

    def mark_preset_run(self, preset_id, run_at):
        pass


def _seed_run(
    session, *, scheduled_at, status="scheduled"
) -> tuple[MaintenanceRun, MaintenanceJob, Destination]:
    source = Source(
        id=uuid4(), name=f"source-{uuid4()}", type="rest", config={"catalog_name": "lake"}
    )
    destination = Destination(
        id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={"host": "trino"}
    )
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name="events",
        is_active=True,
    )
    session.add_all([source, destination, table])
    session.flush()

    run = MaintenanceRun(
        id=uuid4(),
        kind="adhoc",
        trigger="api",
        status=status,
        scheduled_at=scheduled_at,
        summary={},
    )
    session.add(run)
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
    session.commit()
    return run, job, destination


def test_dispatch_enqueues_run_once_scheduled_at_has_passed(session, monkeypatch) -> None:
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    past = datetime.now(UTC) - timedelta(minutes=1)
    run, job, destination = _seed_run(session, scheduled_at=past)

    queue = InMemoryMaintenanceQueue()
    service = SchedulerService(_NullStore(), queue)

    # Not enqueued before dispatch runs.
    assert queue.pending(str(destination.id)) == []

    service._dispatch_scheduled_runs(datetime.now(UTC))

    assert str(job.id) in queue.pending(str(destination.id))
    session.refresh(run)
    assert run.status == "queued"


def test_dispatch_does_not_enqueue_before_scheduled_at(session, monkeypatch) -> None:
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    future = datetime.now(UTC) + timedelta(hours=1)
    run, job, destination = _seed_run(session, scheduled_at=future)

    queue = InMemoryMaintenanceQueue()
    service = SchedulerService(_NullStore(), queue)

    service._dispatch_scheduled_runs(datetime.now(UTC))

    assert queue.pending(str(destination.id)) == []
    session.refresh(run)
    assert run.status == "scheduled"


def test_dispatch_ignores_runs_that_are_not_scheduled(session, monkeypatch) -> None:
    """A run with no scheduled_at (the normal immediate-dispatch case) must never be picked
    up by this tick -- it's already been enqueued synchronously at creation time."""
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    run, job, destination = _seed_run(session, scheduled_at=None, status="queued")

    queue = InMemoryMaintenanceQueue()
    service = SchedulerService(_NullStore(), queue)

    service._dispatch_scheduled_runs(datetime.now(UTC))

    assert queue.pending(str(destination.id)) == []
    session.refresh(run)
    assert run.status == "queued"


def test_tick_dispatches_scheduled_runs_alongside_due_presets(session, monkeypatch) -> None:
    """The dispatch step must run every tick, not only when a cron preset happens to be due
    (SchedulerService.tick() returns early when there are no due presets)."""
    session_factory = _bound_session_factory(session)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", session_factory)

    past = datetime.now(UTC) - timedelta(minutes=1)
    run, job, destination = _seed_run(session, scheduled_at=past)

    queue = InMemoryMaintenanceQueue()
    service = SchedulerService(_NullStore(), queue)  # _NullStore.list_due_presets() -> []

    service.tick(now=datetime.now(UTC))

    assert str(job.id) in queue.pending(str(destination.id))
