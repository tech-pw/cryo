"""Maintenance decisions, effective policy, and workflows from stored rows."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.read_identity import (
    LimitInput,
    TableRefInput,
    candidate,
    clamp_limit,
    iso,
    make_envelope,
    relation_exists,
    require_table,
    scalar_count,
    table_link,
)
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.security import Role
from cryo.db.models import AuditLog, Preset, ScannerConfig, TableAssignment
from cryo.reporting.impact_signals import _RECLAIM_ACTIONS

_SKIP_STATUSES = ("skipped", "deferred", "failed")


class PolicyInput(TableRefInput):
    pass


def _last_reclaim(ctx: ToolContext, table_id: Any) -> AuditLog | None:
    return ctx.session.scalar(
        select(AuditLog)
        .where(
            AuditLog.table_id == table_id,
            AuditLog.status == "OK",
            AuditLog.action.in_(_RECLAIM_ACTIONS),
        )
        .order_by(AuditLog.ts.desc())
        .limit(1)
    )


def get_maintenance_decisions(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    table, error = require_table(ctx, parsed, "get_maintenance_decisions")
    if error is not None or table is None:
        return error or make_envelope(
            "get_maintenance_decisions",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    from cryo.db.models import MaintenanceJob

    reclaim = _last_reclaim(ctx, table.id)
    latest_job = ctx.session.scalar(
        select(MaintenanceJob)
        .where(MaintenanceJob.table_id == table.id)
        .order_by(MaintenanceJob.finished_at.desc(), MaintenanceJob.started_at.desc())
        .limit(1)
    )
    skip_job = ctx.session.scalar(
        select(MaintenanceJob)
        .where(MaintenanceJob.table_id == table.id, MaintenanceJob.status.in_(_SKIP_STATUSES))
        .order_by(MaintenanceJob.finished_at.desc())
        .limit(1)
    )
    recorded_reason = None
    if skip_job is not None and skip_job.error:
        recorded_reason = skip_job.error
    elif table.maintenance_missing_metadata_reason:
        recorded_reason = table.maintenance_missing_metadata_reason
    classification = "observed" if recorded_reason or reclaim is not None else "unknown"
    return make_envelope(
        "get_maintenance_decisions",
        {
            "summary": (
                "Only a successful reclaim audit is maintenance. "
                "A skip reason is reported only when one is stored."
            ),
            **candidate(table),
            "last_successful_reclaim": None
            if reclaim is None
            else {
                "action": reclaim.action,
                "status": reclaim.status,
                "ts": iso(reclaim.ts),
                "actual_bytes_freed": reclaim.actual_bytes_freed,
            },
            "latest_job_status": latest_job.status if latest_job is not None else None,
            "recorded_skip_reason": recorded_reason,
            "maintenance_retry_after": iso(table.maintenance_retry_after),
            "unknown": [] if recorded_reason else ["skip_reason"],
        },
        ctx=ctx,
        classification=classification,
        observed_at=reclaim.ts if reclaim is not None else None,
        source_links=[table_link(table)],
        source_record_ids=[{"table_id": str(table.id)}],
        warnings=[
            "Audit events that are not successful reclaim actions are not treated as maintenance."
        ],
        coverage={"as_of": "stored_audit"},
        measurement_method="audit_log",
    )


def get_effective_policy(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    table, error = require_table(ctx, parsed, "get_effective_policy")
    if error is not None or table is None:
        return error or make_envelope(
            "get_effective_policy",
            {"error": "not_found"},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
        )
    assignment = ctx.session.get(TableAssignment, table.id)
    if assignment is None:
        return make_envelope(
            "get_effective_policy",
            {
                "summary": "No pinned preset. Defaults were not presented as this table's policy.",
                **candidate(table),
                "preset": None,
                "unknown": ["preset_id", "operations", "schedule_cron"],
            },
            ctx=ctx,
            classification="unknown",
            source_links=[table_link(table)],
            source_record_ids=[{"table_id": str(table.id)}],
            warnings=["No TableAssignment. Effective policy is unknown, not a guessed default."],
        )
    preset = ctx.session.get(Preset, assignment.preset_id)
    if preset is None:
        return make_envelope(
            "get_effective_policy",
            {
                "summary": "Assignment points at a missing preset.",
                **candidate(table),
                "preset_id": str(assignment.preset_id),
                "preset": None,
            },
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
            warnings=["Assigned preset row was not found."],
        )
    return make_envelope(
        "get_effective_policy",
        {
            "summary": "Pinned preset plus assignment overrides. Not inferred platform defaults.",
            **candidate(table),
            "preset": {
                "preset_id": str(preset.id),
                "name": preset.name,
                "mode": preset.mode,
                "operations": preset.operations,
                "params": preset.params,
                "guardrails": preset.guardrails,
                "schedule_cron": preset.schedule_cron,
                "enabled": preset.enabled,
                "param_overrides": assignment.param_overrides,
                "pinned": assignment.pinned,
            },
        },
        ctx=ctx,
        source_links=[
            table_link(table),
            {"rel": "preset", "href": f"/operations/presets/{preset.id}"},
        ],
        source_record_ids=[{"table_id": str(table.id)}, {"preset_id": str(preset.id)}],
        coverage={"total": 1, "returned": 1},
        measurement_method="stored_policy",
    )


def list_workflows(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = LimitInput.model_validate(parsed.model_dump())
    limit = clamp_limit(spec.limit)
    preset_total = scalar_count(
        ctx.session,
        select(func.count()).select_from(Preset).where(Preset.schedule_cron.is_not(None)),
    )
    presets = list(
        ctx.session.scalars(
            select(Preset)
            .where(Preset.schedule_cron.is_not(None))
            .order_by(Preset.name)
            .limit(limit)
        )
    )
    scanner = ctx.session.get(ScannerConfig, 1)
    approvals: list[dict[str, Any]] = []
    approval_warning = None
    approval_total = None
    if not relation_exists(ctx.session, "approval_requests"):
        approval_warning = (
            "approval_requests source table is not present. Pending approvals were not invented."
        )
    else:
        try:
            from cryo.workflows.approvals import ApprovalRequest

            approval_total = scalar_count(
                ctx.session,
                select(func.count())
                .select_from(ApprovalRequest)
                .where(ApprovalRequest.status == "pending"),
            )
            pending = list(
                ctx.session.scalars(
                    select(ApprovalRequest)
                    .where(ApprovalRequest.status == "pending")
                    .order_by(ApprovalRequest.created_at.desc())
                    .limit(limit)
                )
            )
            approvals = [
                {
                    "id": str(row.id),
                    "table_id": str(row.table_id),
                    "operation_type": row.operation_type,
                    "status": row.status,
                    "source": "approval_requests",
                }
                for row in pending
            ]
        except SQLAlchemyError:
            approval_warning = (
                "approval_requests could not be read. Pending approvals were not invented."
            )
    warnings = ["Workflows are stored presets and approval rows, not a live scheduler probe."]
    if approval_warning:
        warnings.append(approval_warning)
    return make_envelope(
        "list_workflows",
        {
            "summary": "Scheduled presets and pending approvals from stored rows.",
            "presets": [
                {
                    "preset_id": str(preset.id),
                    "name": preset.name,
                    "schedule_cron": preset.schedule_cron,
                    "enabled": preset.enabled,
                    "mode": preset.mode,
                }
                for preset in presets
            ],
            "preset_total": preset_total,
            "scanner": None
            if scanner is None
            else {
                "enabled": scanner.enabled,
                "schedule_cron": scanner.schedule_cron,
                "timezone": scanner.timezone,
            },
            "pending_approvals": approvals,
            "pending_approval_total": approval_total,
        },
        ctx=ctx,
        classification="observed" if approval_warning is None else "unknown",
        warnings=warnings,
        unsupported_fields=[] if approval_warning is None else ["pending_approvals"],
        coverage={
            "preset_total": preset_total,
            "presets_returned": len(presets),
            "pending_approval_total": approval_total,
        },
        truncated=preset_total > len(presets),
        row_count=len(presets),
        measurement_method="stored_policy",
    )


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="get_maintenance_decisions",
        family="decisions",
        description="Last successful reclaim and stored skip reason. Other audits are not that.",
        input_model=TableRefInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_maintenance_decisions,
    ),
    ToolSpec(
        name="get_effective_policy",
        family="decisions",
        description="Pinned preset and overrides. A missing assignment is unknown.",
        input_model=PolicyInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_effective_policy,
    ),
    ToolSpec(
        name="list_workflows",
        family="decisions",
        description="Scheduled presets and pending approvals. A missing table is not invented.",
        input_model=LimitInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=list_workflows,
    ),
)
