from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from cryo.db.models import AuditLog, Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.job_retention import prune_finished_jobs


def _fleet(session) -> tuple[uuid.UUID, uuid.UUID]:
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
    return table.id, destination.id, run.id


def _job(
    table_id: uuid.UUID,
    destination_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    finished_at: datetime | None,
) -> MaintenanceJob:
    return MaintenanceJob(
        id=uuid.uuid4(),
        run_id=run_id,
        table_id=table_id,
        destination_id=destination_id,
        mode="scheduled",
        plan={},
        status="succeeded" if finished_at else "running",
        finished_at=finished_at,
    )


def test_prune_finished_jobs_keeps_rows_inside_retention(session) -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    table_id, destination_id, run_id = _fleet(session)
    session.add_all(
        [
            _job(table_id, destination_id, run_id, finished_at=now - timedelta(days=181)),
            _job(table_id, destination_id, run_id, finished_at=now - timedelta(days=180)),
            _job(table_id, destination_id, run_id, finished_at=now - timedelta(days=1)),
        ]
    )
    session.commit()

    result = prune_finished_jobs(session, 180, now=now, batch_size=10, max_rows=10)

    assert result.deleted == 1
    assert result.remaining == 0
    assert session.scalar(select(func.count(MaintenanceJob.id))) == 2


def test_prune_finished_jobs_never_touches_in_flight_jobs(session) -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    table_id, destination_id, run_id = _fleet(session)
    session.add(_job(table_id, destination_id, run_id, finished_at=None))
    session.commit()

    result = prune_finished_jobs(session, 1, now=now, batch_size=10, max_rows=10)

    assert result.deleted == 0
    assert session.scalar(select(func.count(MaintenanceJob.id))) == 1


def test_prune_finished_jobs_nulls_out_audit_log_reference_instead_of_failing(session) -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    table_id, destination_id, run_id = _fleet(session)
    old_job = _job(table_id, destination_id, run_id, finished_at=now - timedelta(days=181))
    session.add(old_job)
    session.flush()
    audit_row = AuditLog(
        ts=now - timedelta(days=5),
        table_id=table_id,
        job_id=old_job.id,
        action="optimize",
        status="success",
    )
    session.add(audit_row)
    session.commit()

    result = prune_finished_jobs(session, 180, now=now, batch_size=10, max_rows=10)

    assert result.deleted == 1
    assert session.scalar(select(func.count(MaintenanceJob.id))) == 0
    refreshed = session.get(AuditLog, audit_row.id)
    assert refreshed is not None
    assert refreshed.job_id is None


def test_prune_finished_jobs_bounds_work_and_reports_backlog(session) -> None:
    now = datetime(2026, 8, 4, tzinfo=UTC)
    table_id, destination_id, run_id = _fleet(session)
    session.add_all(
        [
            _job(
                table_id,
                destination_id,
                run_id,
                finished_at=now - timedelta(days=200, seconds=index),
            )
            for index in range(5)
        ]
    )
    session.commit()

    result = prune_finished_jobs(session, 180, now=now, batch_size=2, max_rows=3)

    assert result.deleted == 3
    assert result.remaining == 2
    assert session.scalar(select(func.count(MaintenanceJob.id))) == 2


@pytest.mark.parametrize(
    ("retention_days", "batch_size", "max_rows"),
    [(0, 1, 1), (1, 0, 1), (1, 1, 0)],
)
def test_prune_finished_jobs_rejects_invalid_limits(
    session,
    retention_days: int,
    batch_size: int,
    max_rows: int,
) -> None:
    with pytest.raises(ValueError):
        prune_finished_jobs(
            session,
            retention_days,
            batch_size=batch_size,
            max_rows=max_rows,
        )
