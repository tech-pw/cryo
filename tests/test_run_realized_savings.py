"""Run-detail realized savings must not treat missing byte evidence as zero."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from cryo.api.routes import force_admit_job
from cryo.api.security import Principal, Role
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
)
from cryo.db.repositories.datalake import realized_savings_for_run


def _seed_job(session, *, status: str = "succeeded") -> MaintenanceJob:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="spark_emr", config={})
    session.add_all([source, destination])
    session.flush()
    table = Table(id=uuid4(), source_id=source.id, schema_name="s", table_name="t", is_active=True)
    run = MaintenanceRun(id=uuid4(), kind="adhoc", status="running")
    session.add_all([table, run])
    session.flush()
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status=status,
    )
    session.add(job)
    session.commit()
    return job


def test_realized_savings_distinguishes_unmeasured_success_from_zero(session) -> None:
    job = _seed_job(session)
    session.add(
        AuditLog(
            table_id=job.table_id,
            job_id=job.id,
            action="optimize",
            status="succeeded",
            actual_bytes_freed=None,
        )
    )
    session.commit()

    result = realized_savings_for_run(session, job.run_id)

    assert result["realized_actions_succeeded"] == 1
    assert result["realized_actions_measured"] == 0
    assert result["realized_bytes_freed"] is None


def test_realized_savings_sums_only_measured_actions(session) -> None:
    job = _seed_job(session)
    session.add_all(
        [
            AuditLog(
                table_id=job.table_id,
                job_id=job.id,
                action="optimize",
                status="succeeded",
                actual_bytes_freed=100,
            ),
            AuditLog(
                table_id=job.table_id,
                job_id=job.id,
                action="remove_orphan_files",
                status="succeeded",
                actual_bytes_freed=None,
            ),
            AuditLog(
                table_id=job.table_id,
                job_id=job.id,
                action="force_admit",
                status="OK",
                actual_bytes_freed=None,
            ),
        ]
    )
    session.commit()

    result = realized_savings_for_run(session, job.run_id)

    assert result["realized_bytes_freed"] == 100
    assert result["realized_actions_measured"] == 1
    assert result["realized_actions_succeeded"] == 2


def test_force_admit_writes_an_audit_row(session) -> None:
    job = _seed_job(session, status="queued")
    principal = Principal(subject="operator:test", roles=frozenset({Role.OPERATOR}))

    force_admit_job(job.id, session, principal)

    audit = session.scalar(select(AuditLog).where(AuditLog.action == "force_admit"))
    assert audit is not None
    assert audit.actor == "operator:test"
    assert audit.job_id == job.id
    assert audit.detail == "previous_status=queued"
    assert session.get(MaintenanceJob, job.id).force_admit is True
