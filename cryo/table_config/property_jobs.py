"""Queue an engine-routed table-property change as a job (#560).

The engine path (Iceberg v3, ``engine_route``) used to run the DDL inside the HTTP request.
On Spark-on-Kubernetes that is a driver pod and one to two minutes, past most proxy read
timeouts. It is now a one-table run with one ``set_table_properties`` job on the route's
destination. The governor admits it against live engine load and takes the per-table lock
like any other engine work; the worker plans it (``cryo.planner.table_properties``), renders
it per engine and audits the result. PyIceberg (v1/v2) applies stay synchronous.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, MaintenanceJob, MaintenanceRun, Table
from cryo.domain.enums import JobStatus, Mode, OperationType
from cryo.observability.correlation import correlation_summary
from cryo.planner.job_coverage import dedupe_key
from cryo.planner.table_properties import TABLE_PROPERTIES_PARAM
from cryo.table_config.engine_route import PropertyRoute

OPERATION = OperationType.SET_TABLE_PROPERTIES
QUEUED_STATUS = "QUEUED"
# Mode is required on a job but never read for this one: the planner builds the plan from
# the request alone. manifest_only is the mode with the smallest planned footprint.
_JOB_MODE = Mode.MANIFEST_ONLY


class PropertyChangeInProgressError(Exception):
    """Another property change for this table is still queued or running."""

    def __init__(self, table_id: uuid.UUID) -> None:
        super().__init__(
            "A property change for this table is already queued or running. Wait for it to "
            "finish, then apply again."
        )
        self.table_id = table_id


@dataclass(frozen=True)
class QueuedPropertyChange:
    run_id: uuid.UUID
    job_id: uuid.UUID
    engine: str
    destination: str
    audit_ids: tuple[int, ...] = ()

    def audit_detail(self) -> str:
        return (
            f"route=engine engine={self.engine} destination={self.destination} job_id={self.job_id}"
        )

    def result_metrics(self) -> dict[str, Any]:
        return {
            "route": "engine",
            "engine": self.engine,
            "destination": self.destination,
            "run_id": str(self.run_id),
            "job_id": str(self.job_id),
        }


def enqueue_property_change(
    session: Session,
    table: Table,
    route: PropertyRoute,
    *,
    set_properties: Mapping[str, str],
    remove_properties: Sequence[str] = (),
    actor: str | None,
    trigger: str,
    origin: str,
    audit_rows: Sequence[AuditLog] = (),
) -> QueuedPropertyChange:
    """Create the run and job, write ``audit_rows`` in the same transaction, commit, enqueue.

    ``audit_rows`` are the caller's QUEUED audit entries; their detail gets the job id, so the
    request and the worker's own audit row for the statement can be joined.
    """
    from cryo.infra.wiring import build_runtime_wiring
    from cryo.queue.run_events import build_job_status, build_log_line, safe_publish
    from cryo.scheduler.scheduler import default_run_deadline

    change = {
        "set": {str(key): str(value) for key, value in set_properties.items()},
        "remove": sorted({str(key) for key in remove_properties}),
        "origin": origin,
    }
    now = datetime.now(UTC)
    run = MaintenanceRun(
        kind="adhoc",
        preset_id=None,
        trigger=trigger,
        status="queued",
        deadline_at=default_run_deadline(schedule_cron=None, guardrails=None, now=now),
        summary=correlation_summary(
            {
                "actor": actor,
                "operations": [OPERATION.value],
                TABLE_PROPERTIES_PARAM: change,
            }
        ),
    )
    session.add(run)
    session.flush()
    job = MaintenanceJob(
        run_id=run.id,
        table_id=table.id,
        destination_id=route.destination_id,
        mode=_JOB_MODE.value,
        operations=[OPERATION.value],
        dedupe_key=dedupe_key([OPERATION.value]),
        status=JobStatus.QUEUED.value,
        verified=True,
        # Skips the maintenance scheduling gates (cooldown, off-peak, deadline, fleet GB
        # budget); engine capacity, the table lock, pause, ABAC and PII still apply.
        force_admit=True,
    )
    try:
        with session.begin_nested():
            session.add(job)
    except IntegrityError as exc:
        session.rollback()
        raise PropertyChangeInProgressError(table.id) from exc
    queued = QueuedPropertyChange(
        run_id=run.id,
        job_id=job.id,
        engine=route.engine.value,
        destination=route.destination_name,
    )
    for row in audit_rows:
        row.detail = f"{row.detail} {queued.audit_detail()}"[:4000]
        row.result_metrics = {**(row.result_metrics or {}), **queued.result_metrics()}
        session.add(row)

    from cryo.config import get_settings

    settings = get_settings()
    if settings.durable_intents_enabled:
        from cryo.intents.binding import build_and_bind_job_intent

        build_and_bind_job_intent(
            session,
            job_id=str(job.id),
            settings=settings,
            actor_type="api",
            actor_id=actor or "api",
        )
    session.commit()

    wiring = build_runtime_wiring()
    wiring.queue.enqueue(str(job.id), str(job.destination_id))
    keys = ", ".join([*sorted(change["set"]), *change["remove"]])
    safe_publish(
        wiring.run_events,
        run.id,
        build_log_line(
            run_id=run.id,
            job_id=job.id,
            table_id=table.id,
            message=(
                f"Queued a property change ({keys}) on {route.engine.value} "
                f"({route.destination_name})"
            ),
        ),
    )
    safe_publish(
        wiring.run_events,
        run.id,
        build_job_status(
            run_id=run.id,
            job_id=job.id,
            status=JobStatus.QUEUED.value,
            table_id=table.id,
            message="Job queued",
        ),
    )
    return QueuedPropertyChange(
        run_id=run.id,
        job_id=job.id,
        engine=queued.engine,
        destination=queued.destination,
        audit_ids=tuple(int(row.id) for row in audit_rows if row.id is not None),
    )
