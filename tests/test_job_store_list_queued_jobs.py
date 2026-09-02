"""Tests for SQLJobStore.list_queued_jobs's limit param -- 2026-09-02, same incident as
test_job_store_list_deferred_jobs.py.

Fixing reconcile_deferred_jobs's unbounded scan alone was not enough: a governor pod
restarted onto that fix still failed to complete a single wave for 2.5+ minutes, because
reconcile_queued_jobs (queue/reconcile.py) has the identical unbounded pattern and, at 28,445
queued jobs (far exceeding the 4,227 deferred that surfaced the first bug), was the larger
bottleneck once the deferred side was bounded.

limit, when given, returns the oldest-queued-first slice (ordered by the parent run's
created_at -- MaintenanceJob has no created_at of its own, a job is created at the same time
as its run) so a wave's own runtime stays bounded regardless of backlog size.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.job_store import SQLJobStore


def _seed_queued_job(session, *, run_created_at: datetime, name: str) -> MaintenanceJob:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(
        id=uuid4(), name=f"dest-{uuid4()}", engine="native", config={"slot_cap": 5}
    )
    table = Table(
        id=uuid4(), source_id=source.id, schema_name="s", table_name=name, is_active=True
    )
    session.add_all([source, destination, table])
    session.flush()

    run = MaintenanceRun(
        id=uuid4(),
        kind="adhoc",
        trigger="api",
        status="running",
        summary={},
        created_at=run_created_at,
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
    return job


def test_no_limit_returns_every_queued_job(session):
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    for i in range(5):
        _seed_queued_job(session, run_created_at=now - timedelta(hours=i), name=f"t{i}")

    store = SQLJobStore(lambda: session)
    records = store.list_queued_jobs()

    assert len(records) == 5


def test_limit_caps_the_result_count(session):
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    for i in range(10):
        _seed_queued_job(session, run_created_at=now - timedelta(hours=i), name=f"t{i}")

    store = SQLJobStore(lambda: session)
    records = store.list_queued_jobs(limit=3)

    assert len(records) == 3


def test_limit_returns_oldest_run_first(session):
    """Direct regression proof: jobs whose parent run has been waiting longest must be the
    ones returned when the backlog exceeds the limit -- otherwise a run created early in the
    day could be starved forever behind a stream of fresher runs on a backlog that never
    fully drains in one wave."""
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    oldest = _seed_queued_job(session, run_created_at=now - timedelta(hours=10), name="oldest")
    middle = _seed_queued_job(session, run_created_at=now - timedelta(hours=5), name="middle")
    _seed_queued_job(session, run_created_at=now - timedelta(hours=1), name="newest")

    store = SQLJobStore(lambda: session)
    records = store.list_queued_jobs(limit=2)

    returned_ids = {r.id for r in records}
    assert str(oldest.id) in returned_ids
    assert str(middle.id) in returned_ids


def test_limit_zero_returns_nothing(session):
    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    _seed_queued_job(session, run_created_at=now, name="t0")

    store = SQLJobStore(lambda: session)
    records = store.list_queued_jobs(limit=0)

    assert records == []
