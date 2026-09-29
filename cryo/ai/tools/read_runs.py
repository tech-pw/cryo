"""Run, operation, and failure evidence from stored job rows. Citations must be stored text."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select

from cryo.ai.diagnosis import diagnose_deterministic
from cryo.ai.tools.evidence import EvidenceEnvelope
from cryo.ai.tools.read_identity import (
    clamp_limit,
    iso,
    make_envelope,
    parse_offset,
    scalar_count,
)
from cryo.ai.tools.registry import ToolContext, ToolSpec
from cryo.api.security import Role
from cryo.db.models import AuditLog, JobStep, MaintenanceJob, MaintenanceRun


class RunListInput(BaseModel):
    limit: int = 50
    cursor: str | None = None
    status: str | None = None


class RunRefInput(BaseModel):
    run_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    limit: int = 50


class LogInput(BaseModel):
    run_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    max_chars: int = 2000


def _run_row(run: MaintenanceRun) -> dict[str, Any]:
    return {
        "run_id": str(run.id),
        "kind": run.kind,
        "status": run.status,
        "trigger": run.trigger,
        "created_at": iso(run.created_at),
        "finished_at": iso(run.finished_at),
        "preset_id": str(run.preset_id) if run.preset_id else None,
    }


def list_runs(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = RunListInput.model_validate(parsed.model_dump())
    offset = parse_offset(spec.cursor)
    if offset is None:
        return make_envelope(
            "list_runs",
            {"error": "invalid_cursor"},
            ctx=ctx,
            classification="unknown",
            error_category="invalid_arguments",
            warnings=["cursor must be a non-negative integer offset."],
        )
    limit = clamp_limit(spec.limit)
    filters = []
    if spec.status:
        filters.append(MaintenanceRun.status == spec.status)
    total = scalar_count(
        ctx.session, select(func.count()).select_from(MaintenanceRun).where(*filters)
    )
    rows = list(
        ctx.session.scalars(
            select(MaintenanceRun)
            .where(*filters)
            .order_by(MaintenanceRun.created_at.asc(), MaintenanceRun.id)
            .offset(offset)
            .limit(limit)
        )
    )
    truncated = offset + len(rows) < total
    return make_envelope(
        "list_runs",
        {
            "summary": "Stored runs. total is a count query. Waiting is not inferred from age.",
            "runs": [_run_row(run) for run in rows],
            "total": total,
            "cursor": str(offset + len(rows)) if truncated else None,
        },
        ctx=ctx,
        coverage={"total": total, "returned": len(rows), "offset": offset},
        truncated=truncated,
        row_count=len(rows),
        measurement_method="stored_runs",
        source_links=[{"rel": "run", "href": f"/runs/{run.id}"} for run in rows],
    )


def get_run(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = RunRefInput.model_validate(parsed.model_dump())
    if spec.run_id is None:
        return make_envelope(
            "get_run",
            {"error": "run_required"},
            ctx=ctx,
            classification="unknown",
            error_category="invalid_arguments",
            warnings=["run_id is required."],
        )
    run = ctx.session.get(MaintenanceRun, spec.run_id)
    if run is None:
        return make_envelope(
            "get_run",
            {"error": "not_found", "run_id": str(spec.run_id)},
            ctx=ctx,
            classification="unknown",
            error_category="not_found",
            warnings=["Run was not found."],
        )
    status_rows = ctx.session.execute(
        select(MaintenanceJob.status, func.count())
        .where(MaintenanceJob.run_id == run.id)
        .group_by(MaintenanceJob.status)
    ).all()
    job_total = scalar_count(
        ctx.session,
        select(func.count()).select_from(MaintenanceJob).where(MaintenanceJob.run_id == run.id),
    )
    jobs = list(
        ctx.session.scalars(
            select(MaintenanceJob)
            .where(MaintenanceJob.run_id == run.id)
            .order_by(MaintenanceJob.started_at.asc())
            .limit(clamp_limit(spec.limit))
        )
    )
    return make_envelope(
        "get_run",
        {
            "summary": "Stored run. Job status counts come from COUNT, not from the page length.",
            **_run_row(run),
            "job_status_counts": {status: int(count) for status, count in status_rows},
            "job_total": job_total,
            "jobs": [
                {
                    "job_id": str(job.id),
                    "table_id": str(job.table_id),
                    "status": job.status,
                    "error": job.error,
                    "started_at": iso(job.started_at),
                    "finished_at": iso(job.finished_at),
                }
                for job in jobs
            ],
        },
        ctx=ctx,
        source_links=[{"rel": "run", "href": f"/runs/{run.id}"}],
        source_record_ids=[{"run_id": str(run.id)}],
        coverage={"job_total": job_total, "jobs_returned": len(jobs)},
        truncated=job_total > len(jobs),
        row_count=len(jobs),
        measurement_method="stored_runs",
    )


def _jobs_for(ctx: ToolContext, spec: RunRefInput) -> list[MaintenanceJob]:
    stmt = select(MaintenanceJob)
    if spec.job_id is not None:
        stmt = stmt.where(MaintenanceJob.id == spec.job_id)
    elif spec.run_id is not None:
        stmt = stmt.where(MaintenanceJob.run_id == spec.run_id)
    else:
        return []
    return list(ctx.session.scalars(stmt.limit(clamp_limit(spec.limit))))


def get_operation_evidence(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = RunRefInput.model_validate(parsed.model_dump())
    if spec.run_id is None and spec.job_id is None:
        return make_envelope(
            "get_operation_evidence",
            {"error": "run_or_job_required"},
            ctx=ctx,
            classification="unknown",
            error_category="invalid_arguments",
            warnings=["run_id or job_id is required."],
        )
    filters = []
    if spec.job_id is not None:
        filters.append(AuditLog.job_id == spec.job_id)
    if spec.run_id is not None:
        job_ids = select(MaintenanceJob.id).where(MaintenanceJob.run_id == spec.run_id)
        filters.append(AuditLog.job_id.in_(job_ids))
    total = scalar_count(ctx.session, select(func.count()).select_from(AuditLog).where(*filters))
    rows = list(
        ctx.session.scalars(
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.ts.asc())
            .limit(clamp_limit(spec.limit))
        )
    )
    return make_envelope(
        "get_operation_evidence",
        {
            "summary": "Stored audit rows for the run or job. Null bytes freed stay null.",
            "rows": [
                {
                    "audit_id": str(row.id),
                    "action": row.action,
                    "status": row.status,
                    "ts": iso(row.ts),
                    "detail": row.detail,
                    "duration_ms": row.duration_ms,
                    "actual_bytes_freed": row.actual_bytes_freed,
                    "engine": row.engine,
                }
                for row in rows
            ],
            "total": total,
        },
        ctx=ctx,
        units={"actual_bytes_freed": "bytes", "duration_ms": "ms"},
        coverage={"total": total, "returned": len(rows)},
        truncated=total > len(rows),
        row_count=len(rows),
        measurement_method="audit_log",
        source_record_ids=[{"audit_id": str(row.id)} for row in rows],
    )


def _stored_lines(ctx: ToolContext, spec: LogInput) -> list[tuple[str, str]]:
    lines: list[tuple[str, str]] = []
    jobs = _jobs_for(ctx, RunRefInput(run_id=spec.run_id, job_id=spec.job_id))
    for job in jobs:
        if job.error:
            lines.append((job.error, f"job:{job.id}"))
        steps = ctx.session.scalars(select(JobStep).where(JobStep.job_id == job.id))
        for step in steps:
            if step.detail:
                lines.append((step.detail, f"step:{step.id}"))
    audit_filters = []
    if spec.job_id is not None:
        audit_filters.append(AuditLog.job_id == spec.job_id)
    elif spec.run_id is not None:
        job_ids = [job.id for job in jobs]
        if job_ids:
            audit_filters.append(AuditLog.job_id.in_(job_ids))
    if audit_filters:
        for row in ctx.session.scalars(select(AuditLog).where(*audit_filters)):
            if row.detail:
                lines.append((row.detail, f"audit:{row.id}"))
    return lines


def get_log_excerpt(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = LogInput.model_validate(parsed.model_dump())
    if spec.run_id is None and spec.job_id is None:
        return make_envelope(
            "get_log_excerpt",
            {"error": "run_or_job_required"},
            ctx=ctx,
            classification="unknown",
            error_category="invalid_arguments",
            warnings=["run_id or job_id is required."],
        )
    stored = _stored_lines(ctx, spec)
    cap = max(1, min(spec.max_chars, 8000))
    excerpts = []
    used = 0
    for text, source in stored:
        if used >= cap:
            break
        chunk = text[: cap - used]
        excerpts.append({"text": chunk, "source": source})
        used += len(chunk)
    if not excerpts:
        return make_envelope(
            "get_log_excerpt",
            {"summary": "No stored error or log lines.", "lines": []},
            ctx=ctx,
            classification="unknown",
            warnings=["No stored log lines. None were invented."],
            measurement_method="stored_logs",
        )
    return make_envelope(
        "get_log_excerpt",
        {"summary": "Excerpt of stored error and step lines only.", "lines": excerpts},
        ctx=ctx,
        coverage={"stored_lines": len(stored), "returned": len(excerpts), "max_chars": cap},
        truncated=len(stored) > len(excerpts) or used < sum(len(text) for text, _ in stored),
        row_count=len(excerpts),
        measurement_method="stored_logs",
        source_record_ids=[{"source": source} for _, source in stored[: len(excerpts)]],
    )


def _citations(stored: list[str], claimed: list[str]) -> list[str]:
    kept: list[str] = []
    for line in claimed:
        if any(line and line in source for source in stored):
            kept.append(line)
    return kept


def diagnose_failure(ctx: ToolContext, parsed: BaseModel) -> EvidenceEnvelope:
    spec = LogInput.model_validate(parsed.model_dump())
    if spec.run_id is None and spec.job_id is None:
        return make_envelope(
            "diagnose_failure",
            {"error": "run_or_job_required"},
            ctx=ctx,
            classification="unknown",
            error_category="invalid_arguments",
            warnings=["run_id or job_id is required."],
        )
    stored_pairs = _stored_lines(ctx, spec)
    stored = [text for text, _ in stored_pairs]
    if not stored:
        return make_envelope(
            "diagnose_failure",
            {
                "summary": "No stored error lines, so no diagnosis and no citations.",
                "diagnosis_class": None,
                "citations": [],
            },
            ctx=ctx,
            classification="unknown",
            warnings=["Diagnosis was not invented. There are no stored error lines to cite."],
            measurement_method="stored_logs",
        )
    diagnosis = diagnose_deterministic(stored)
    citations = _citations(stored, diagnosis.evidence_lines)
    return make_envelope(
        "diagnose_failure",
        {
            "summary": diagnosis.summary,
            "diagnosis_class": diagnosis.diagnosis_class,
            "confidence": diagnosis.confidence,
            "runbook_step": diagnosis.runbook_step,
            "citations": citations,
            "used_llm": False,
        },
        ctx=ctx,
        classification="inferred",
        warnings=[
            "Citations are substrings of stored error or log lines. Model quotes are not accepted."
        ],
        coverage={"stored_lines": len(stored), "citations": len(citations)},
        row_count=len(citations),
        measurement_method="deterministic_diagnosis",
        source_record_ids=[{"source": source} for _, source in stored_pairs],
    )


SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_runs",
        family="runs",
        description="Stored maintenance runs. total is a count query.",
        input_model=RunListInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=list_runs,
    ),
    ToolSpec(
        name="get_run",
        family="runs",
        description="One stored run and its job status counts.",
        input_model=RunRefInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_run,
    ),
    ToolSpec(
        name="get_operation_evidence",
        family="runs",
        description="Audit rows for a run or job. Null bytes freed stay null.",
        input_model=RunRefInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_operation_evidence,
    ),
    ToolSpec(
        name="get_log_excerpt",
        family="runs",
        description="Stored error and step lines only.",
        input_model=LogInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=get_log_excerpt,
    ),
    ToolSpec(
        name="diagnose_failure",
        family="runs",
        description="Deterministic diagnosis. Citations must be substrings of stored lines.",
        input_model=LogInput,
        output_model=EvidenceEnvelope,
        tier=0,
        min_role=Role.VIEWER,
        handler=diagnose_failure,
    ),
)
