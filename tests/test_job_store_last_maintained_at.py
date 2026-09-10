"""Tests for SQLJobStore._last_maintained_at's reclaim-action scoping -- 2026-09-10,
cryo_cost_rescue plan's wave1-bytes item.

Governor's cooldown_elapsed (cryo/governor/admission.py) gates real admission decisions
on this value -- it must mean "when did this table last actually have bytes reclaimed",
not "when did any OK audit last touch it". Before this fix, ANY status='OK' AuditLog row
counted, including expire_snapshots (metadata-only on the native engine -- reclaims
nothing on its own) and property-autopilot applies -- so a table that only ever had
metadata-only maintenance run against it still started a real cooldown as if actual
compaction happened, silently blocking the scheduler from re-attempting real reclaim work.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from cryo.db.models import AuditLog, Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.job_store import SQLJobStore


def _seed_table_with_job(session) -> tuple[Table, MaintenanceJob]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
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

    run = MaintenanceRun(id=uuid4(), kind="adhoc", trigger="api", status="queued", summary={})
    session.add(run)
    session.flush()

    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status="succeeded",
    )
    session.add(job)
    session.commit()
    return table, job


def test_last_maintained_at_ignores_expire_only_audit(session) -> None:
    """The exact bug: an expire_snapshots-only audit (metadata-only on native, reclaims
    nothing) must not count as "maintained" for cooldown purposes."""
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

    store = SQLJobStore(lambda: session)
    record = store.get_job(str(job.id))

    assert record.last_maintained_at is None


def test_last_maintained_at_counts_real_reclaim_action(session) -> None:
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

    store = SQLJobStore(lambda: session)
    record = store.get_job(str(job.id))

    assert record.last_maintained_at == reclaim_ts.replace(tzinfo=None)


def test_last_maintained_at_prefers_latest_reclaim_over_later_expire_only(session) -> None:
    """A later expire-only audit must not shadow an earlier real reclaim -- the returned
    timestamp is the latest REAL maintenance, not just the latest audit row of any kind."""
    table, job = _seed_table_with_job(session)
    reclaim_ts = datetime(2026, 9, 1, tzinfo=UTC)
    session.add_all(
        [
            AuditLog(
                ts=reclaim_ts,
                table_id=table.id,
                job_id=job.id,
                action="remove_orphan_files",
                status="OK",
            ),
            AuditLog(
                ts=datetime(2026, 9, 5, tzinfo=UTC),
                table_id=table.id,
                job_id=job.id,
                action="expire_snapshots",
                status="OK",
            ),
        ]
    )
    session.commit()

    store = SQLJobStore(lambda: session)
    record = store.get_job(str(job.id))

    assert record.last_maintained_at == reclaim_ts.replace(tzinfo=None)
