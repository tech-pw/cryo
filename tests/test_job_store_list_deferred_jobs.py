"""Tests for SQLJobStore.list_deferred_jobs's limit param -- the 2026-09-02 fix for a real
governor stall.

reconcile_deferred_jobs (cryo/queue/reconcile.py, called every 30s governor wave) previously
called this unbounded. Live-verified: with 4,227 deferred jobs and growing, each wave's
reconcile step took long enough to stall the governor entirely -- admitted-per-wave declined
15->8->7->5->5->5->2->2 across consecutive waves, then no wave completed at all for 3+ minutes
against the 30s interval. A pod restart didn't help (confirming the backlog's own size, not
pod state, was the bottleneck) until this was bounded.

limit, when given, returns the oldest-deferred-first slice (ordered by finished_at, the
timestamp mark_status sets when a job transitions to DEFERRED) so a wave's own runtime stays
bounded regardless of backlog size, and the jobs that have been waiting longest get evaluated
first -- the same fairness principle as fifo_priority (scheduler.py, same day).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.job_store import SQLJobStore


def _seed_deferred_job(session, *, finished_at: datetime, name: str) -> MaintenanceJob:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(
        id=uuid4(), name=f"dest-{uuid4()}", engine="native", config={"slot_cap": 5}
    )
    table = Table(
        id=uuid4(), source_id=source.id, schema_name="s", table_name=name, is_active=True
    )
    session.add_all([source, destination, table])
    session.flush()

    run = MaintenanceRun(id=uuid4(), kind="adhoc", trigger="api", status="running", summary={})
    session.add(run)
    session.flush()

    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status="deferred",
        finished_at=finished_at,
    )
    session.add(job)
    session.commit()
    return job


def test_no_limit_returns_every_deferred_job(session):
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    for i in range(5):
        _seed_deferred_job(session, finished_at=now - timedelta(hours=i), name=f"t{i}")

    store = SQLJobStore(lambda: session)
    records = store.list_deferred_jobs()

    assert len(records) == 5


def test_limit_caps_the_result_count(session):
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    for i in range(10):
        _seed_deferred_job(session, finished_at=now - timedelta(hours=i), name=f"t{i}")

    store = SQLJobStore(lambda: session)
    records = store.list_deferred_jobs(limit=3)

    assert len(records) == 3


def test_limit_returns_oldest_deferred_first(session):
    """Direct regression proof: the jobs that have been waiting longest (smallest
    finished_at) must be the ones returned when the backlog exceeds the limit -- not an
    arbitrary or newest-first slice, which would starve the oldest-waiting jobs forever on a
    backlog that never fully drains in one wave."""
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    oldest = _seed_deferred_job(session, finished_at=now - timedelta(hours=10), name="oldest")
    middle = _seed_deferred_job(session, finished_at=now - timedelta(hours=5), name="middle")
    _seed_deferred_job(session, finished_at=now - timedelta(hours=1), name="newest")

    store = SQLJobStore(lambda: session)
    records = store.list_deferred_jobs(limit=2)

    returned_ids = {r.id for r in records}
    assert str(oldest.id) in returned_ids
    assert str(middle.id) in returned_ids


def test_limit_zero_returns_nothing(session):
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    _seed_deferred_job(session, finished_at=now, name="t0")

    store = SQLJobStore(lambda: session)
    records = store.list_deferred_jobs(limit=0)

    assert records == []
