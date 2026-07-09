"""AI feature endpoints: diagnosis, translation, narration, anomaly brief, and sizing."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from cryo.ai.anomaly_brief import compose_root_cause_brief
from cryo.ai.diagnosis import diagnose_with_optional_llm
from cryo.ai.filter_translate import build_filter_chips, translate_with_optional_llm
from cryo.ai.resource_predictor import predict_resources_for_table
from cryo.ai.runtime import LLMInvokeContext, complete_with_budget_and_observation
from cryo.ai.skillset import load_skillset, record_outcome
from cryo.api.deps import SessionDep, require_editor, require_viewer
from cryo.api.schemas import (
    PlanNarrationRequest,
    PlanNarrationResponse,
    RunFailureDiagnosisFeedbackWrite,
    RunFailureDiagnosisRead,
    TableFilterTranslationRequest,
    TableFilterTranslationResponse,
)
from cryo.api.security import Principal
from cryo.config import get_settings
from cryo.db.models import AuditLog, MaintenanceJob, MaintenanceRun, RunFailureDiagnosis, Table

router = APIRouter(prefix="/api/v1", tags=["ai"])


class AnomalyHypothesisRead(BaseModel):
    title: str
    confidence: float
    because: str


class AnomalyRootCauseBriefRead(BaseModel):
    table_id: uuid.UUID
    evidence: list[str] = Field(default_factory=list)
    hypotheses: list[AnomalyHypothesisRead] = Field(default_factory=list)
    used_llm: bool = False
    observation_id: uuid.UUID | None = None


class ResourcePredictionRead(BaseModel):
    table_id: uuid.UUID
    predicted_p50_seconds: float
    predicted_p90_seconds: float
    recommended_cpu_cores: float
    recommended_memory_gb: float
    confidence: float
    samples: int
    shadow_mode: bool


def _diagnosis_observation_id(row: RunFailureDiagnosis) -> uuid.UUID | None:
    raw = row.raw_response if isinstance(row.raw_response, dict) else {}
    value = raw.get("observation_id")
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


def _to_diagnosis_read(row: RunFailureDiagnosis) -> RunFailureDiagnosisRead:
    return RunFailureDiagnosisRead(
        id=row.id,
        run_id=row.run_id,
        job_id=row.job_id,
        table_id=row.table_id,
        diagnosis_class=row.diagnosis_class,
        confidence=float(row.confidence),
        summary=row.summary,
        runbook_step=row.runbook_step,
        evidence=[str(line) for line in (row.evidence or [])],
        feedback=row.feedback,
        feedback_note=row.feedback_note,
        diagnosed_at=row.diagnosed_at,
        skillset_id=row.skillset_id,
        observation_id=_diagnosis_observation_id(row),
    )


def _build_deterministic_plan_summary(payload: PlanNarrationRequest) -> str:
    op_order = ", ".join(payload.operations) if payload.operations else "no maintenance operations"
    notes = "; ".join(payload.notes[:3]) if payload.notes else "no planner notes were emitted"
    return (
        f"Plan for {payload.table_ref} executes {op_order}. "
        f"Planner notes: {notes}. "
        "Verifier output and rendered SQL remain the source of truth."
    )


@router.get(
    "/ai/runs/{run_id}/diagnosis-latest",
    response_model=RunFailureDiagnosisRead | None,
    dependencies=[Depends(require_viewer)],
)
def get_latest_run_diagnosis(
    run_id: uuid.UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> RunFailureDiagnosisRead | None:
    row = session.scalar(
        select(RunFailureDiagnosis)
        .where(RunFailureDiagnosis.run_id == run_id)
        .order_by(desc(RunFailureDiagnosis.diagnosed_at))
        .limit(1)
    )
    if row is None:
        return None
    return _to_diagnosis_read(row)


@router.post(
    "/ai/runs/{run_id}/diagnose",
    response_model=RunFailureDiagnosisRead,
    dependencies=[Depends(require_viewer)],
)
async def diagnose_run_failure(
    run_id: uuid.UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
    force: bool = Query(False, description="Re-run diagnosis even when a cached one exists."),
) -> RunFailureDiagnosisRead:
    settings = get_settings()
    if not settings.ai_feature_diagnosis_enabled:
        raise HTTPException(status_code=503, detail="Run diagnosis is disabled.")

    run = session.get(MaintenanceRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not force:
        existing = session.scalar(
            select(RunFailureDiagnosis)
            .where(RunFailureDiagnosis.run_id == run_id)
            .order_by(desc(RunFailureDiagnosis.diagnosed_at))
            .limit(1)
        )
        if existing is not None:
            return _to_diagnosis_read(existing)

    jobs = list(
        session.scalars(
            select(MaintenanceJob)
            .where(MaintenanceJob.run_id == run_id)
            .order_by(desc(MaintenanceJob.started_at))
        )
    )
    if not jobs:
        raise HTTPException(status_code=404, detail="No jobs found for run")

    failed_jobs = [
        job for job in jobs if (job.status or "").lower() in {"failed", "partial_failed"}
    ]
    job = failed_jobs[0] if failed_jobs else jobs[0]

    evidence: list[str] = []
    if run.summary:
        for key in ("error", "last_error", "detail"):
            value = run.summary.get(key)
            if isinstance(value, str) and value.strip():
                evidence.append(f"run.summary.{key}: {value.strip()}")
    for candidate in failed_jobs[:3]:
        if candidate.error:
            evidence.append(f"job {candidate.id} error: {candidate.error}")
        if candidate.plan and isinstance(candidate.plan, dict):
            ops = candidate.plan.get("operations")
            if isinstance(ops, list) and ops:
                first = ops[0]
                if isinstance(first, dict):
                    evidence.append(f"job {candidate.id} first operation: {first.get('type')}")

    job_ids = [item.id for item in jobs]
    audits = list(
        session.scalars(
            select(AuditLog)
            .where(AuditLog.job_id.in_(job_ids))
            .order_by(desc(AuditLog.ts))
            .limit(10)
        )
    )
    for audit in audits:
        if audit.detail:
            detail = audit.detail if isinstance(audit.detail, str) else json.dumps(audit.detail)
            evidence.append(f"audit[{audit.action}]: {detail[:280]}")
        if audit.status and audit.status.upper() != "OK":
            evidence.append(f"audit status={audit.status} action={audit.action}")

    if not evidence:
        evidence = [
            f"Run {run_id} has status {run.status}",
            f"Job {job.id} has status {job.status}",
            "No explicit error payloads found in run/job/audit records.",
        ]

    skillset = load_skillset(session, "iceberg_advisor")
    diagnosis, observation_id = await diagnose_with_optional_llm(
        session,
        evidence_lines=evidence,
        run_id=run_id,
        table_id=job.table_id,
        skillset_id=uuid.UUID(skillset.id) if skillset.id else None,
        skillset_name=skillset.name,
        skillset_version=skillset.version,
    )
    row = RunFailureDiagnosis(
        run_id=run_id,
        job_id=job.id,
        table_id=job.table_id,
        skillset_id=uuid.UUID(skillset.id) if skillset.id else None,
        diagnosis_class=diagnosis.diagnosis_class,
        confidence=diagnosis.confidence,
        summary=diagnosis.summary,
        runbook_step=diagnosis.runbook_step,
        evidence=diagnosis.evidence_lines,
        raw_response={
            "used_llm": diagnosis.used_llm,
            "observation_id": str(observation_id) if observation_id else None,
        },
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    out = _to_diagnosis_read(row)
    return out.model_copy(update={"observation_id": observation_id})


@router.post(
    "/ai/runs/diagnoses/{diagnosis_id}/feedback",
    response_model=RunFailureDiagnosisRead,
    dependencies=[Depends(require_editor)],
)
def submit_diagnosis_feedback(
    diagnosis_id: uuid.UUID,
    payload: RunFailureDiagnosisFeedbackWrite,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_editor)],
) -> RunFailureDiagnosisRead:
    row = session.get(RunFailureDiagnosis, diagnosis_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Diagnosis not found")

    row.feedback = payload.feedback
    row.feedback_note = payload.note
    row.feedback_at = datetime.now(UTC)
    session.flush()

    if row.skillset_id is not None:
        record_outcome(
            session,
            row.skillset_id,
            prompt_name="run_failure_diagnosis",
            recommendation=row.summary or row.diagnosis_class,
            decision="accepted" if payload.feedback == "up" else "rejected",
            score=row.confidence,
            table_id=row.table_id,
        )
    else:
        session.commit()

    session.refresh(row)
    return _to_diagnosis_read(row)


@router.post(
    "/ai/table-filter/translate",
    response_model=TableFilterTranslationResponse,
    dependencies=[Depends(require_viewer)],
)
async def translate_table_filter(
    payload: TableFilterTranslationRequest,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> TableFilterTranslationResponse:
    settings = get_settings()
    if not settings.ai_feature_filter_translation_enabled:
        raise HTTPException(status_code=503, detail="Filter translation is disabled.")

    translated = await translate_with_optional_llm(
        session,
        payload.query,
        use_llm=payload.use_llm,
    )
    return TableFilterTranslationResponse(
        filter=translated.filter,
        chips=build_filter_chips(translated.filter),
        warnings=translated.warnings,
        errors=translated.errors,
        used_llm=translated.used_llm,
        provider=translated.provider,
        model=translated.model,
        observation_id=uuid.UUID(translated.observation_id) if translated.observation_id else None,
    )


@router.post(
    "/ai/plan/narrate",
    response_model=PlanNarrationResponse,
    dependencies=[Depends(require_viewer)],
)
async def narrate_plan(
    payload: PlanNarrationRequest,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> PlanNarrationResponse:
    deterministic = _build_deterministic_plan_summary(payload)
    settings = get_settings()
    if not settings.ai_feature_plan_narration_enabled or not payload.sql_statements:
        return PlanNarrationResponse(deterministic_summary=deterministic, used_llm=False)

    prompt = (
        "Summarize SQL intent in 2-3 short sentences. Focus on operation purpose, not syntax.\n"
        f"Table: {payload.table_ref}\n"
        f"Operations: {', '.join(payload.operations)}\n"
        f"SQL:\n" + "\n".join(payload.sql_statements[:8])
    )
    response, observation = await complete_with_budget_and_observation(
        session,
        prompt,
        invoke=LLMInvokeContext(
            feature="plan_narration",
            prompt_name="plan_narration",
            table_id=None,
            extra={"table_ref": payload.table_ref},
        ),
        max_tokens=180,
    )
    if response is None:
        return PlanNarrationResponse(
            deterministic_summary=deterministic,
            llm_narration=None,
            used_llm=False,
            observation_id=observation.id,
        )
    return PlanNarrationResponse(
        deterministic_summary=deterministic,
        llm_narration=response.content.strip(),
        used_llm=True,
        observation_id=observation.id,
    )


@router.get(
    "/ai/tables/{table_id}/anomaly-brief",
    response_model=AnomalyRootCauseBriefRead,
    dependencies=[Depends(require_viewer)],
)
async def anomaly_root_cause_brief(
    table_id: uuid.UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> AnomalyRootCauseBriefRead:
    settings = get_settings()
    if not settings.ai_feature_anomaly_brief_enabled:
        raise HTTPException(status_code=503, detail="Anomaly brief is disabled.")
    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")
    brief = await compose_root_cause_brief(session, table_id)
    return AnomalyRootCauseBriefRead(
        table_id=table_id,
        evidence=brief.evidence,
        hypotheses=[
            AnomalyHypothesisRead(
                title=item.title,
                confidence=item.confidence,
                because=item.because,
            )
            for item in brief.hypotheses
        ],
        used_llm=brief.used_llm,
        observation_id=brief.observation_id,
    )


@router.get(
    "/ai/tables/{table_id}/resource-prediction",
    response_model=ResourcePredictionRead,
    dependencies=[Depends(require_viewer)],
)
def resource_prediction(
    table_id: uuid.UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> ResourcePredictionRead:
    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")
    pred = predict_resources_for_table(session, table_id)
    return ResourcePredictionRead(
        table_id=pred.table_id,
        predicted_p50_seconds=pred.predicted_p50_seconds,
        predicted_p90_seconds=pred.predicted_p90_seconds,
        recommended_cpu_cores=pred.recommended_cpu_cores,
        recommended_memory_gb=pred.recommended_memory_gb,
        confidence=pred.confidence,
        samples=pred.samples,
        shadow_mode=pred.shadow_mode,
    )
