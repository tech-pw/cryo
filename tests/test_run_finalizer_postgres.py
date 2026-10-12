"""Real-Postgres concurrency test for review finding F09 (cryo/db/repositories/run_finalizer.py).

Same rationale as tests/test_analysis_tasks_postgres.py: FOR UPDATE SKIP LOCKED is a real
Postgres row-locking behavior that the SQLite-backed `session`/`session_factory` fixtures used
by tests/test_run_finalizer.py cannot exercise -- SQLite accepts the syntax but does not enforce
the locking semantics, so those tests can only prove the sequential-re-entry fixes (query scope,
immutable finished_at), not the genuine concurrent-race fix.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.run_finalizer import finalize_open_runs
from cryo.db.session import SessionLocal


def _postgres_available() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(), reason="requires a reachable local Postgres with migrations applied"
)


@pytest.fixture(autouse=True)
def _clean_up():
    created: dict[str, list] = {"runs": [], "tables": [], "sources": [], "destinations": []}
    yield created
    with SessionLocal() as session:
        for run_id in created["runs"]:
            session.execute(text("DELETE FROM cryo.jobs WHERE run_id = :r"), {"r": run_id})
            session.execute(text("DELETE FROM cryo.runs WHERE id = :r"), {"r": run_id})
        for table_id in created["tables"]:
            session.execute(text("DELETE FROM cryo.tables WHERE id = :t"), {"t": table_id})
        for source_id in created["sources"]:
            session.execute(text("DELETE FROM cryo.sources WHERE id = :s"), {"s": source_id})
        for dest_id in created["destinations"]:
            session.execute(text("DELETE FROM cryo.destinations WHERE id = :d"), {"d": dest_id})
        session.commit()


def _seed_finalizable_run(session, created) -> uuid.UUID:
    """A run with exactly one succeeded job -- unambiguously finalizable to status="succeeded"
    the instant finalize_open_runs examines it, no grace-window waiting involved."""
    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4().hex[:8]}", type="rest", config={})
    table = Table(
        id=uuid.uuid4(), source_id=source.id, schema_name="s", table_name="t", is_active=True
    )
    destination = Destination(
        id=uuid.uuid4(), name=f"dest-{uuid.uuid4().hex[:8]}", engine="native", config={}
    )
    run = MaintenanceRun(id=uuid.uuid4(), kind="scheduled", status="running", trigger="cron")
    session.add_all([source, table, destination, run])
    session.flush()
    job = MaintenanceJob(
        id=uuid.uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="scheduled",
        plan={},
        status="succeeded",
        finished_at=datetime.now(UTC) - timedelta(hours=1),
    )
    session.add(job)
    session.commit()
    created["runs"].append(run.id)
    created["tables"].append(table.id)
    created["sources"].append(source.id)
    created["destinations"].append(destination.id)
    return run.id


def test_two_concurrent_finalize_calls_never_both_finalize_the_same_run(_clean_up):
    """Review finding F09: two finalize_open_runs invocations (simulating two governor
    replicas, or overlapping ticks) racing the same open run must not both decide its outcome --
    FOR UPDATE SKIP LOCKED means whichever one locks the row first finalizes it; the other's
    SELECT simply skips it (sees zero rows this tick) rather than also loading and committing a
    second, possibly divergent, finalization."""
    with SessionLocal() as session:
        run_id = _seed_finalizable_run(session, _clean_up)

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _run_finalizer() -> None:
        barrier.wait(timeout=5)
        count = finalize_open_runs(SessionLocal, now=datetime.now(UTC))
        results.append(count)

    threads = [threading.Thread(target=_run_finalizer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # Across both concurrent invocations, this one run must have been finalized exactly once
    # (either both report 0 except one reporting 1, or -- if they didn't actually overlap in
    # time due to scheduling -- one reports 1 and the other reports 0 because the run was
    # already terminal by the time it ran). Never both reporting a finalize for the same run.
    assert sum(results) == 1, (
        f"expected exactly one finalize across both racing calls, got {results}"
    )

    with SessionLocal() as session:
        run = session.get(MaintenanceRun, run_id)
        assert run.status == "succeeded"
        assert run.finished_at is not None
