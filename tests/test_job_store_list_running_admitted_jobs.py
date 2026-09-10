"""Tests for SQLJobStore.list_running_jobs/list_admitted_jobs' limit param -- 2026-09-10,
same incident class as test_job_store_list_queued_jobs.py.

cryo/worker/orphan_recovery.py's recover_orphaned_jobs called list_running_jobs() (and,
in its fallback loop, list_admitted_jobs()) unconditionally on every worker pod's 5s tick,
with no limit -- the same unbounded-scan pattern list_queued_jobs' own docstring already
documented and fixed for its own call site (28,445 rows, 2026-09-02), but this pair of
sibling functions was missed. Kernel-verified live (via /proc/1/smaps_rollup on a running
pod): Anonymous heap memory climbed linearly ~1GB per 5s tick, ~140MB to 8GB in ~42s,
immediately OOMKilled -- matches recover_orphaned_jobs' own poll interval exactly.

list_running_jobs orders oldest-running-first (started_at); list_admitted_jobs orders
oldest-run-first (joined MaintenanceRun.created_at, same as list_queued_jobs -- a job has
no created_at of its own) so a tick's own memory footprint stays bounded regardless of how
large the fleet-wide RUNNING/ADMITTED set grows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.job_store import SQLJobStore


def _seed_job(
    session, *, run_created_at: datetime, name: str, status: str, started_at: datetime | None
) -> MaintenanceJob:
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
        status=status,
        started_at=started_at,
    )
    session.add(job)
    session.commit()
    return job


class TestListRunningJobs:
    def test_no_limit_returns_every_running_job(self, session):
        now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        for i in range(5):
            _seed_job(
                session,
                run_created_at=now,
                name=f"t{i}",
                status="running",
                started_at=now - timedelta(hours=i),
            )

        store = SQLJobStore(lambda: session)
        records = store.list_running_jobs()

        assert len(records) == 5

    def test_limit_caps_the_result_count(self, session):
        now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        for i in range(10):
            _seed_job(
                session,
                run_created_at=now,
                name=f"t{i}",
                status="running",
                started_at=now - timedelta(hours=i),
            )

        store = SQLJobStore(lambda: session)
        records = store.list_running_jobs(limit=3)

        assert len(records) == 3

    def test_limit_returns_oldest_started_first(self, session):
        """Direct regression proof: the jobs that have been running longest (most likely to
        be genuine orphans from a crashed worker, not fresh dispatches) must be the ones
        returned when the RUNNING set exceeds the limit."""
        now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        oldest = _seed_job(
            session,
            run_created_at=now,
            name="oldest",
            status="running",
            started_at=now - timedelta(hours=10),
        )
        middle = _seed_job(
            session,
            run_created_at=now,
            name="middle",
            status="running",
            started_at=now - timedelta(hours=5),
        )
        _seed_job(
            session,
            run_created_at=now,
            name="newest",
            status="running",
            started_at=now - timedelta(hours=1),
        )

        store = SQLJobStore(lambda: session)
        records = store.list_running_jobs(limit=2)

        returned_ids = {r.id for r in records}
        assert str(oldest.id) in returned_ids
        assert str(middle.id) in returned_ids

    def test_limit_zero_returns_nothing(self, session):
        now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        _seed_job(session, run_created_at=now, name="t0", status="running", started_at=now)

        store = SQLJobStore(lambda: session)
        records = store.list_running_jobs(limit=0)

        assert records == []


class TestListAdmittedJobs:
    def test_no_limit_returns_every_admitted_job(self, session):
        now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        for i in range(5):
            _seed_job(
                session,
                run_created_at=now - timedelta(hours=i),
                name=f"t{i}",
                status="admitted",
                started_at=None,
            )

        store = SQLJobStore(lambda: session)
        records = store.list_admitted_jobs()

        assert len(records) == 5

    def test_limit_caps_the_result_count(self, session):
        now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        for i in range(10):
            _seed_job(
                session,
                run_created_at=now - timedelta(hours=i),
                name=f"t{i}",
                status="admitted",
                started_at=None,
            )

        store = SQLJobStore(lambda: session)
        records = store.list_admitted_jobs(limit=3)

        assert len(records) == 3

    def test_limit_returns_oldest_run_first(self, session):
        now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        oldest = _seed_job(
            session,
            run_created_at=now - timedelta(hours=10),
            name="oldest",
            status="admitted",
            started_at=None,
        )
        middle = _seed_job(
            session,
            run_created_at=now - timedelta(hours=5),
            name="middle",
            status="admitted",
            started_at=None,
        )
        _seed_job(
            session,
            run_created_at=now - timedelta(hours=1),
            name="newest",
            status="admitted",
            started_at=None,
        )

        store = SQLJobStore(lambda: session)
        records = store.list_admitted_jobs(limit=2)

        returned_ids = {r.id for r in records}
        assert str(oldest.id) in returned_ids
        assert str(middle.id) in returned_ids

    def test_limit_zero_returns_nothing(self, session):
        now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        _seed_job(session, run_created_at=now, name="t0", status="admitted", started_at=None)

        store = SQLJobStore(lambda: session)
        records = store.list_admitted_jobs(limit=0)

        assert records == []
