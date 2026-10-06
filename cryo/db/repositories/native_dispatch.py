"""Repository for native K8s Job dispatches created on Governor admit."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import MaintenanceJob, NativeDispatch
from cryo.engines.native.packing import DispatchSpec
from cryo.engines.native.resources import MAX_POD_MEM_MIB


def create_dispatch_record(
    session: Session,
    *,
    spec: DispatchSpec,
    destination_id: str | uuid.UUID,
    k8s_job_name: str | None = None,
    k8s_namespace: str = "default",
    status: str = "pending",
) -> NativeDispatch:
    mem_mib = min(spec.mem_mib, MAX_POD_MEM_MIB)
    row = NativeDispatch(
        run_id=uuid.UUID(str(spec.run_id)),
        destination_id=uuid.UUID(str(destination_id)),
        k8s_job_name=k8s_job_name,
        k8s_namespace=k8s_namespace,
        cpu_milli=spec.cpu_milli,
        mem_mib=mem_mib,
        table_job_ids=list(spec.job_ids),
        status=status,
        dedicated=spec.dedicated,
    )
    session.add(row)
    session.flush()
    for job_id in spec.job_ids:
        job = session.get(MaintenanceJob, uuid.UUID(str(job_id)))
        if job is not None:
            job.dispatch_id = row.id
    return row


def mark_dispatch_running(
    session: Session,
    dispatch_id: uuid.UUID,
    *,
    k8s_job_name: str,
) -> NativeDispatch | None:
    row = session.get(NativeDispatch, dispatch_id)
    if row is None:
        return None
    row.k8s_job_name = k8s_job_name
    row.status = "running"
    return row


def mark_dispatch_finished(
    session: Session,
    dispatch_id: uuid.UUID,
    *,
    status: str,
    error: str | None = None,
) -> NativeDispatch | None:
    row = session.get(NativeDispatch, dispatch_id)
    if row is None:
        return None
    row.status = status
    row.error = error
    row.finished_at = datetime.now(UTC)
    return row


def list_active_dispatches(session: Session, *, limit: int | None = None) -> list[NativeDispatch]:
    """List active dispatches, oldest-created first.

    ``limit`` bounds how many the caller processes in one pass -- NativeJobWatcher.tick()
    calls read_namespaced_job (a real K8s API round-trip) for each one returned, so an
    unbounded call here scales the watcher's per-tick cost with however many dispatches
    happen to be in flight. Oldest-first ordering means a bounded call still makes forward
    progress every tick: whichever dispatches don't fit this pass are exactly the ones
    still in the "active" set (and therefore first in line) next tick, rather than being
    starved by newer dispatches created after them.
    """
    stmt = (
        select(NativeDispatch)
        .where(NativeDispatch.status.in_(("pending", "running", "cancel_requested", "cancelling")))
        .order_by(NativeDispatch.created_at.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


def get_dispatch(session: Session, dispatch_id: uuid.UUID) -> NativeDispatch | None:
    return session.get(NativeDispatch, dispatch_id)


def jobs_for_dispatch(session: Session, dispatch_id: uuid.UUID) -> list[MaintenanceJob]:
    return list(
        session.scalars(
            select(MaintenanceJob).where(MaintenanceJob.dispatch_id == dispatch_id)
        ).all()
    )


def attach_dispatch_to_jobs(
    session: Session,
    *,
    dispatch_id: uuid.UUID,
    job_ids: Sequence[str],
) -> None:
    for job_id in job_ids:
        job = session.get(MaintenanceJob, uuid.UUID(str(job_id)))
        if job is not None:
            job.dispatch_id = dispatch_id


def dispatch_to_dict(row: NativeDispatch) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "run_id": str(row.run_id),
        "destination_id": str(row.destination_id),
        "k8s_job_name": row.k8s_job_name,
        "k8s_namespace": row.k8s_namespace,
        "cpu_milli": row.cpu_milli,
        "mem_mib": row.mem_mib,
        "table_job_ids": list(row.table_job_ids or []),
        "status": row.status,
        "dedicated": bool(row.dedicated),
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }
