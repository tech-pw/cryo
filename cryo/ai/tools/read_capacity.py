"""Admission, queue, and engine status from stored job rows. No live engine probe."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import func, select

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.read_identity import iso, make_envelope, scalar_count
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.routes_engine_load import load_engine_admission
from cryo.api.security import Role
from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Preset


class EmptyInput(BaseModel):
    pass


_WAITING = ("queued", "admitted", "deferred")


def _status_counts(ctx: ToolContext) -> dict[str, int]:
    rows = ctx.session.execute(
        select(MaintenanceJob.status, func.count()).group_by(MaintenanceJob.status)
    ).all()
    return {str(status): int(count) for status, count in rows}


def get_admission_state(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    del parsed
    load = load_engine_admission(ctx.session)
    counts = _status_counts(ctx)
    engines = [
        {
            "destination_id": str(row.destination_id),
            "name": row.name,
            "engine": row.engine,
            "slots_used": row.slots_used,
            "slots_limit": row.slots_limit,
            "admission": row.admission,
            "live_probe": row.live_probe,
        }
        for row in load.engines
    ]
    return make_envelope(
        "get_admission_state",
        {
            "summary": (
                "Slot use is a count of admitted or running jobs. "
                "Live engine load was not probed. Waiting is not a failure."
            ),
            "engines": engines,
            "job_status_counts": counts,
            "waiting_statuses": list(_WAITING),
        },
        ctx=ctx,
        units={"slots_used": "jobs", "slots_limit": "jobs"},
        coverage={"engines": len(engines), "live_probe": "not recorded"},
        unsupported_fields=["live_engine_probe", "redis_stream_depth"],
        warnings=["Admission state is from stored jobs, not a live engine probe."],
        row_count=len(engines),
        measurement_method="stored_jobs",
    )


def get_queue_summary(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    del parsed
    counts = _status_counts(ctx)
    total = scalar_count(ctx.session, select(func.count()).select_from(MaintenanceJob))
    oldest = ctx.session.scalar(
        select(func.min(MaintenanceRun.created_at)).where(MaintenanceRun.status.in_(_WAITING))
    )
    return make_envelope(
        "get_queue_summary",
        {
            "summary": "Stored queue counts. Age is not a failure. Redis depth was not read.",
            "job_status_counts": counts,
            "job_total": total,
            "waiting": {status: counts.get(status) for status in _WAITING},
            "oldest_waiting_run_created_at": iso(oldest),
            "redis_stream_depth": None,
        },
        ctx=ctx,
        classification="observed",
        coverage={"job_total": total, "redis_stream_depth": "not read"},
        unsupported_fields=["redis_stream_depth"],
        warnings=["Queued, admitted, and deferred jobs are waiting, not failed."],
        measurement_method="stored_jobs",
        units={"job_total": "jobs"},
    )


def get_engine_status(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    del parsed
    total = scalar_count(ctx.session, select(func.count()).select_from(Destination))
    enabled = scalar_count(
        ctx.session,
        select(func.count()).select_from(Destination).where(Destination.enabled.is_(True)),
    )
    rows = list(
        ctx.session.execute(
            select(Destination.id, Destination.name, Destination.engine, Destination.enabled)
            .order_by(Destination.name)
            .limit(200)
        )
    )
    scheduled = scalar_count(
        ctx.session,
        select(func.count())
        .select_from(Preset)
        .where(Preset.enabled.is_(True), Preset.schedule_cron.is_not(None)),
    )
    return make_envelope(
        "get_engine_status",
        {
            "summary": "Configured destinations. Live engine health was not probed.",
            "destinations": [
                {
                    "destination_id": str(row.id),
                    "name": row.name,
                    "engine": row.engine,
                    "enabled": row.enabled,
                }
                for row in rows
            ],
            "destination_total": total,
            "enabled_total": enabled,
            "scheduled_preset_total": scheduled,
            "live_probe": None,
        },
        ctx=ctx,
        coverage={"total": total, "returned": len(rows), "enabled_total": enabled},
        truncated=total > len(rows),
        row_count=len(rows),
        unsupported_fields=["live_engine_probe"],
        warnings=["Engine status is configuration plus stored job slots, not a live probe."],
        measurement_method="stored_config",
    )


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_admission_state",
        family="capacity",
        description="Stored slot use. Live engine load is not probed.",
        input_model=EmptyInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_admission_state,
    ),
    ToolSpec(
        name="get_queue_summary",
        family="capacity",
        description="Stored queue counts. Waiting is not failed. Redis depth is not read.",
        input_model=EmptyInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_queue_summary,
    ),
    ToolSpec(
        name="get_engine_status",
        family="capacity",
        description="Configured engines. Live probes are unsupported.",
        input_model=EmptyInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_engine_status,
    ),
)
