from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from cryo.db.model_utils import catalog_name_for_table, qualified_table_name
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Table,
    TablePartition,
)
from cryo.domain.enums import JobStatus, Mode
from cryo.worker.worker import (
    AuditRecord,
    DestinationConfig,
    MaintenanceJobRecord,
    TableMaintenanceData,
)


class SQLJobStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get_job(self, job_id: str) -> MaintenanceJobRecord:
        with self._session_factory() as session:
            job = _load_job(session, uuid.UUID(job_id))
            return _job_record(session, job)

    def get_destination(self, destination_id: str) -> DestinationConfig:
        destination = self.try_get_destination(destination_id)
        if destination is None:
            raise KeyError(f"destination not found: {destination_id}")
        return destination

    def try_get_destination(self, destination_id: str) -> DestinationConfig | None:
        with self._session_factory() as session:
            destination = session.get(Destination, uuid.UUID(destination_id))
            if destination is None:
                return None
            return DestinationConfig(
                id=str(destination.id),
                engine=destination.engine,
                config=dict(destination.config),
            )

    def get_table_data(self, table_id: str) -> TableMaintenanceData:
        with self._session_factory() as session:
            table = session.scalar(
                select(Table)
                .where(Table.id == uuid.UUID(table_id))
                .options(selectinload(Table.stats), selectinload(Table.source))
            )
            if table is None:
                raise KeyError(f"table not found: {table_id}")
            stats = _stats_dict(table)
            partitions = _latest_partitions(session, table.id)
            return TableMaintenanceData(stats=stats, partitions=partitions)

    def save_plan(self, job_id: str, plan: dict[str, Any]) -> None:
        with self._session_factory() as session:
            job = _load_job(session, uuid.UUID(job_id))
            job.plan = plan
            session.commit()

    def mark_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        engine_ref: str | None = None,
        replica: str | None = None,
        error: str | None = None,
    ) -> None:
        with self._session_factory() as session:
            job = _load_job(session, uuid.UUID(job_id))
            job.status = JobStatus(status).value
            now = datetime.now(UTC)
            if status is JobStatus.QUEUED:
                job.started_at = None
                job.finished_at = None
            if status in {JobStatus.QUEUED, JobStatus.ADMITTED, JobStatus.RUNNING}:
                job.finished_at = None
            if status is JobStatus.RUNNING and job.started_at is None:
                job.started_at = now
            if status in {
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.SKIPPED,
                JobStatus.DEFERRED,
            }:
                job.finished_at = now
            if engine_ref is not None:
                job.engine_ref = engine_ref
            if replica is not None:
                job.replica = replica
            job.error = error
            session.commit()

    def record_audit(self, record: AuditRecord) -> None:
        with self._session_factory() as session:
            session.add(
                AuditLog(
                    table_id=uuid.UUID(record.table_id),
                    job_id=uuid.UUID(record.job_id),
                    action=record.action.value,
                    status=record.status,
                    engine=str(record.engine),
                    detail=record.detail,
                    duration_ms=record.duration_ms,
                    actual_bytes_freed=record.actual_bytes_freed,
                    input_files_count=record.input_files_count,
                    output_files_count=record.output_files_count,
                    actor=record.actor,
                )
            )
            if record.status == "OK":
                from cryo.workflows.approvals import (
                    ApprovalRequest,
                    ApprovalStatus,
                    requires_approval,
                )

                if requires_approval(record.action):
                    stmt = (
                        select(ApprovalRequest)
                        .where(
                            ApprovalRequest.table_id == uuid.UUID(record.table_id),
                            ApprovalRequest.operation_type == record.action.value,
                            ApprovalRequest.status == ApprovalStatus.APPROVED,
                            ApprovalRequest.executed_at.is_(None),
                        )
                        .order_by(ApprovalRequest.created_at.asc())
                        .limit(1)
                    )
                    req = session.scalar(stmt)
                    if req:
                        req.executed_at = datetime.now(UTC)
                        req.execution_job_id = uuid.UUID(record.job_id)
            session.commit()

    def pending_jobs(self, destination_id: str) -> list[MaintenanceJobRecord]:
        with self._session_factory() as session:
            jobs = session.scalars(
                select(MaintenanceJob)
                .where(
                    MaintenanceJob.destination_id == uuid.UUID(destination_id),
                    MaintenanceJob.status == JobStatus.QUEUED.value,
                )
                .options(selectinload(MaintenanceJob.run).selectinload(MaintenanceRun.preset))
            )
            return [_job_record(session, job) for job in jobs]

    def list_queued_jobs(self) -> list[MaintenanceJobRecord]:
        with self._session_factory() as session:
            jobs = session.scalars(
                select(MaintenanceJob)
                .where(MaintenanceJob.status == JobStatus.QUEUED.value)
                .options(selectinload(MaintenanceJob.run).selectinload(MaintenanceRun.preset))
            )
            return [_job_record(session, job) for job in jobs]

    def list_deferred_jobs(self) -> list[MaintenanceJobRecord]:
        with self._session_factory() as session:
            jobs = session.scalars(
                select(MaintenanceJob)
                .where(MaintenanceJob.status == JobStatus.DEFERRED.value)
                .options(selectinload(MaintenanceJob.run).selectinload(MaintenanceRun.preset))
            )
            return [_job_record(session, job) for job in jobs]

    def destination_exists(self, destination_id: str) -> bool:
        with self._session_factory() as session:
            destination = session.get(Destination, uuid.UUID(destination_id))
            return destination is not None and destination.enabled


def _load_job(session: Session, job_id: uuid.UUID) -> MaintenanceJob:
    job = session.scalar(
        select(MaintenanceJob)
        .where(MaintenanceJob.id == job_id)
        .options(selectinload(MaintenanceJob.run).selectinload(MaintenanceRun.preset))
    )
    if job is None:
        raise KeyError(f"job not found: {job_id}")
    return job


def _job_record(session: Session, job: MaintenanceJob) -> MaintenanceJobRecord:
    preset = job.run.preset if job.run else None
    params: dict[str, Any] = dict(preset.params) if preset else {}
    guardrails = dict(preset.guardrails) if preset else {}
    summary = dict(job.run.summary or {}) if job.run else {}
    actor_raw = summary.get("actor")
    actor = actor_raw if isinstance(actor_raw, str) and actor_raw else None
    return MaintenanceJobRecord(
        id=str(job.id),
        run_id=str(job.run_id),
        table_id=str(job.table_id),
        destination_id=str(job.destination_id),
        mode=Mode(job.mode),
        params=params,
        status=JobStatus(job.status),
        plan=job.plan,
        engine_ref=job.engine_ref,
        replica=job.replica,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        deadline_at=job.run.deadline_at if job.run else None,
        cooldown_hours=int(guardrails.get("cooldown_hours") or 0),
        last_maintained_at=_last_maintained_at(session, job.table_id),
        actor=actor,
    )


def _last_maintained_at(session: Session, table_id: uuid.UUID) -> datetime | None:
    row = session.scalar(
        select(AuditLog.ts)
        .where(AuditLog.table_id == table_id, AuditLog.status == "OK")
        .order_by(desc(AuditLog.ts))
        .limit(1)
    )
    return row


def _stats_dict(table: Table) -> dict[str, Any]:
    stats = table.stats
    catalog = catalog_name_for_table(table) if table.source is not None else "iceberg"
    table_ref = (
        qualified_table_name(table)
        if table.source is not None
        else f"{catalog}.{table.schema_name}.{table.table_name}"
    )
    if stats is None:
        return {
            "catalog": catalog,
            "schema_name": table.schema_name,
            "table_name": table.table_name,
            "table_ref": table_ref,
        }
    return {
        "catalog": catalog,
        "schema_name": table.schema_name,
        "table_name": table.table_name,
        "table_ref": table_ref,
        "needs_compaction": stats.needs_compaction,
        "compaction_priority": stats.compaction_priority,
        "grade": stats.grade,
        "overall_score": stats.overall_score,
        "total_files": stats.total_files,
        "small_files_count": stats.small_files_count,
        "total_partitions": stats.total_partitions,
        "total_size_gb": stats.total_size_gb,
        "delete_file_pct": stats.delete_file_pct,
        "partition_col": stats.partition_col,
        "partition_col_type": stats.partition_col_type,
        "projected_savings_usd_month": stats.projected_savings_usd_month,
        "raw": dict(stats.raw or {}),
    }


def _latest_partitions(session: Session, table_id: uuid.UUID) -> Sequence[Mapping[str, Any]]:
    latest_scan = session.scalar(
        select(TablePartition.scan_at)
        .where(TablePartition.table_id == table_id)
        .order_by(desc(TablePartition.scan_at))
        .limit(1)
    )
    if latest_scan is None:
        return ()
    rows = session.scalars(
        select(TablePartition).where(
            TablePartition.table_id == table_id,
            TablePartition.scan_at == latest_scan,
        )
    )
    return [
        {
            "partition_value": row.partition_value,
            "partition_values": dict(row.partition_values or {}),
            "small_files_in_partition_count": row.small_files_in_partition_count,
            "partition_size_gb": row.partition_size_gb,
            "files_per_partition": row.files_per_partition,
            "last_write_at": (row.raw or {}).get("last_write_at"),
        }
        for row in rows
    ]
