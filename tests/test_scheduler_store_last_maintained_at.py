"""Tests for SQLSchedulerStore.list_tables' TableCandidate.last_maintained_at population --
2026-09-10, cryo_cost_rescue plan's wave1-bytes item.

Before this fix, table_to_candidate never populated last_maintained_at at all, so
scheduler.drop_recently_maintained's `isinstance(last, datetime)` check was always False --
the cooldown filter at schedule-creation time silently never dropped anything, regardless
of how recently a table was actually maintained or what cooldown_hours a preset configured.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.db.models import AuditLog, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.scheduler_store import SQLSchedulerStore


def _seed_table_with_job(session, *, table_name: str = "events") -> tuple[Table, MaintenanceJob]:
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    session.add(Source(id=source_id, name=f"src-{source_id}", type="rest", config={}))
    table = Table(
        id=table_id,
        source_id=source_id,
        schema_name="analytics",
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.commit()

    run = MaintenanceRun(id=uuid.uuid4(), kind="adhoc", trigger="api", status="succeeded")
    session.add(run)
    session.flush()
    job = MaintenanceJob(
        id=uuid.uuid4(),
        run_id=run.id,
        table_id=table_id,
        destination_id=uuid.uuid4(),
        mode="standard",
        status="succeeded",
    )
    session.add(job)
    session.commit()
    return table, job


def test_list_tables_populates_last_maintained_at_from_real_reclaim_actions(session) -> None:
    table, job = _seed_table_with_job(session)
    reclaim_ts = datetime(2026, 9, 1, tzinfo=UTC)
    session.add(
        AuditLog(
            ts=reclaim_ts,
            table_id=table.id,
            job_id=job.id,
            action="optimize",
            status="OK",
        )
    )
    session.commit()

    store = SQLSchedulerStore(lambda: session, redis_client=None)
    candidates = {c.id: c for c in store.list_tables()}

    assert candidates[str(table.id)].last_maintained_at == reclaim_ts.replace(tzinfo=None)


def test_list_tables_ignores_expire_only_audit(session) -> None:
    table, job = _seed_table_with_job(session)
    session.add(
        AuditLog(
            ts=datetime(2026, 9, 1, tzinfo=UTC),
            table_id=table.id,
            job_id=job.id,
            action="expire_snapshots",
            status="OK",
        )
    )
    session.commit()

    store = SQLSchedulerStore(lambda: session, redis_client=None)
    candidates = {c.id: c for c in store.list_tables()}

    assert candidates[str(table.id)].last_maintained_at is None


def test_list_tables_leaves_never_maintained_table_as_none(session) -> None:
    table, _job = _seed_table_with_job(session)

    store = SQLSchedulerStore(lambda: session, redis_client=None)
    candidates = {c.id: c for c in store.list_tables()}

    assert candidates[str(table.id)].last_maintained_at is None
