"""Write-ahead job step ledger (Postgres source of truth)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import JobStep, MaintenanceJob
from cryo.domain.enums import JobStepStatus, OperationType


@dataclass(frozen=True, slots=True)
class JobStepRecord:
    id: str
    job_id: str
    step_index: int
    operation_type: OperationType
    detail: str | None
    status: JobStepStatus
    engine_ref: str | None
    started_at: datetime | None
    finished_at: datetime | None


class JobStepStore:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def begin_step(
        self,
        job_id: str,
        step_index: int,
        operation_type: OperationType | str,
        detail: str | None = None,
    ) -> JobStepRecord:
        if isinstance(operation_type, OperationType):
            op = operation_type
        else:
            op = OperationType(operation_type)
        with self._session_factory() as session:
            job_uuid = uuid.UUID(job_id)
            existing = session.scalar(
                select(JobStep).where(
                    JobStep.job_id == job_uuid,
                    JobStep.step_index == step_index,
                )
            )
            if existing is not None:
                if existing.status == JobStepStatus.STARTED.value:
                    job = session.get(MaintenanceJob, job_uuid)
                    if job is not None:
                        job.current_step_index = step_index
                        session.commit()
                    return _record(existing)
                if existing.status in {
                    JobStepStatus.SUCCEEDED.value,
                    JobStepStatus.SKIPPED.value,
                }:
                    return _record(existing)
                existing.status = JobStepStatus.STARTED.value
                existing.detail = detail
                existing.operation_type = op.value
                existing.engine_ref = None
                existing.started_at = datetime.now(UTC)
                existing.finished_at = None
                job = session.get(MaintenanceJob, job_uuid)
                if job is not None:
                    job.current_step_index = step_index
                session.commit()
                session.refresh(existing)
                return _record(existing)

            row = JobStep(
                job_id=job_uuid,
                step_index=step_index,
                operation_type=op.value,
                detail=detail,
                status=JobStepStatus.STARTED.value,
            )
            session.add(row)
            job = session.get(MaintenanceJob, job_uuid)
            if job is not None:
                job.current_step_index = step_index
            session.commit()
            session.refresh(row)
            return _record(row)

    def set_step_engine_ref(self, job_id: str, step_index: int, engine_ref: str) -> None:
        with self._session_factory() as session:
            row = session.scalar(
                select(JobStep).where(
                    JobStep.job_id == uuid.UUID(job_id),
                    JobStep.step_index == step_index,
                )
            )
            if row is None:
                return
            row.engine_ref = engine_ref
            job = session.get(MaintenanceJob, uuid.UUID(job_id))
            if job is not None:
                job.engine_ref = engine_ref
            session.commit()

    def complete_step(
        self,
        job_id: str,
        step_index: int,
        status: JobStepStatus | str,
        *,
        engine_ref: str | None = None,
        detail: str | None = None,
    ) -> JobStepRecord | None:
        final = JobStepStatus(status)
        with self._session_factory() as session:
            row = session.scalar(
                select(JobStep).where(
                    JobStep.job_id == uuid.UUID(job_id),
                    JobStep.step_index == step_index,
                )
            )
            if row is None:
                return None
            row.status = final.value
            row.finished_at = datetime.now(UTC)
            if engine_ref is not None:
                row.engine_ref = engine_ref
            if detail is not None:
                row.detail = detail
            session.commit()
            session.refresh(row)
            return _record(row)

    def get_started_step(self, job_id: str) -> JobStepRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(JobStep)
                .where(
                    JobStep.job_id == uuid.UUID(job_id),
                    JobStep.status == JobStepStatus.STARTED.value,
                )
                .order_by(JobStep.step_index)
            )
            return _record(row) if row is not None else None

    def get_step(self, job_id: str, step_index: int) -> JobStepRecord | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(JobStep).where(
                    JobStep.job_id == uuid.UUID(job_id),
                    JobStep.step_index == step_index,
                )
            )
            return _record(row) if row is not None else None

    def list_steps(self, job_id: str) -> list[JobStepRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(JobStep)
                .where(JobStep.job_id == uuid.UUID(job_id))
                .order_by(JobStep.step_index)
            )
            return [_record(row) for row in rows]

    def has_started_step(self, job_id: str) -> bool:
        return self.get_started_step(job_id) is not None


def _record(row: JobStep) -> JobStepRecord:
    return JobStepRecord(
        id=str(row.id),
        job_id=str(row.job_id),
        step_index=int(row.step_index),
        operation_type=OperationType(row.operation_type),
        detail=row.detail,
        status=JobStepStatus(row.status),
        engine_ref=row.engine_ref,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


class InMemoryJobStepStore:
    """Test double for the write-ahead step ledger."""

    def __init__(self) -> None:
        self._steps: dict[tuple[str, int], JobStepRecord] = {}
        self.begin_order: list[tuple[str, int]] = []

    def begin_step(
        self,
        job_id: str,
        step_index: int,
        operation_type: OperationType | str,
        detail: str | None = None,
    ) -> JobStepRecord:
        if isinstance(operation_type, OperationType):
            op = operation_type
        else:
            op = OperationType(operation_type)
        key = (job_id, step_index)
        existing = self._steps.get(key)
        if existing is not None and existing.status in {
            JobStepStatus.SUCCEEDED,
            JobStepStatus.SKIPPED,
            JobStepStatus.STARTED,
        }:
            if existing.status is JobStepStatus.STARTED:
                self.begin_order.append(key)
            return existing
        record = JobStepRecord(
            id=str(uuid.uuid4()),
            job_id=job_id,
            step_index=step_index,
            operation_type=op,
            detail=detail,
            status=JobStepStatus.STARTED,
            engine_ref=None,
            started_at=datetime.now(UTC),
            finished_at=None,
        )
        self._steps[key] = record
        self.begin_order.append(key)
        return record

    def set_step_engine_ref(self, job_id: str, step_index: int, engine_ref: str) -> None:
        key = (job_id, step_index)
        existing = self._steps.get(key)
        if existing is None:
            return
        self._steps[key] = JobStepRecord(
            id=existing.id,
            job_id=existing.job_id,
            step_index=existing.step_index,
            operation_type=existing.operation_type,
            detail=existing.detail,
            status=existing.status,
            engine_ref=engine_ref,
            started_at=existing.started_at,
            finished_at=existing.finished_at,
        )

    def complete_step(
        self,
        job_id: str,
        step_index: int,
        status: JobStepStatus | str,
        *,
        engine_ref: str | None = None,
        detail: str | None = None,
    ) -> JobStepRecord | None:
        key = (job_id, step_index)
        existing = self._steps.get(key)
        if existing is None:
            return None
        final = JobStepStatus(status)
        record = JobStepRecord(
            id=existing.id,
            job_id=existing.job_id,
            step_index=existing.step_index,
            operation_type=existing.operation_type,
            detail=detail if detail is not None else existing.detail,
            status=final,
            engine_ref=engine_ref if engine_ref is not None else existing.engine_ref,
            started_at=existing.started_at,
            finished_at=datetime.now(UTC),
        )
        self._steps[key] = record
        return record

    def get_started_step(self, job_id: str) -> JobStepRecord | None:
        started = [
            step
            for (jid, _), step in self._steps.items()
            if jid == job_id and step.status is JobStepStatus.STARTED
        ]
        if not started:
            return None
        return sorted(started, key=lambda s: s.step_index)[0]

    def get_step(self, job_id: str, step_index: int) -> JobStepRecord | None:
        return self._steps.get((job_id, step_index))

    def list_steps(self, job_id: str) -> list[JobStepRecord]:
        return sorted(
            (step for (jid, _), step in self._steps.items() if jid == job_id),
            key=lambda s: s.step_index,
        )

    def has_started_step(self, job_id: str) -> bool:
        return self.get_started_step(job_id) is not None
