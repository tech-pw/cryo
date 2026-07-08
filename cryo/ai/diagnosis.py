"""Run-failure diagnosis taxonomy + deterministic/LLM composition."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from cryo.ai.runtime import LLMInvokeContext, complete_with_budget_and_observation

DIAGNOSIS_CLASSES = {
    "commit_conflict",
    "engine_oom",
    "catalog_timeout_or_ratelimit",
    "permission",
    "sql_or_dialect",
    "storage_error",
    "unknown",
}

RUNBOOK_STEPS: dict[str, str] = {
    "commit_conflict": "Retry with jitter; verify table lock contention and concurrent writers.",
    "engine_oom": "Lower chunk size / partitions per chunk and increase engine memory limits.",
    "catalog_timeout_or_ratelimit": "Check catalog health, metastore latency, and API rate limits before retry.",
    "permission": "Verify destination credentials and catalog/table IAM or RBAC grants.",
    "sql_or_dialect": "Inspect rendered SQL/native statements and validate engine-specific syntax support.",
    "storage_error": "Check object-store connectivity, bucket policy, and missing/corrupt object paths.",
    "unknown": "Escalate with run/job evidence; gather coordinator logs before rerun.",
}

_HEURISTIC_RULES: list[tuple[str, str, float]] = [
    (r"(commit conflict|concurrent.*commit|optimistic.*lock)", "commit_conflict", 0.86),
    (r"(out of memory|oom|memory limit|gc overhead)", "engine_oom", 0.9),
    (r"(timeout|timed out|rate limit|429|metastore.*unavailable|catalog.*timeout)", "catalog_timeout_or_ratelimit", 0.82),
    (r"(permission denied|access denied|unauthorized|forbidden|not authorized)", "permission", 0.9),
    (r"(syntax error|parse error|cannot resolve|column .* not found|dialect)", "sql_or_dialect", 0.84),
    (r"(s3|object storage|i/o error|io error|file not found|path does not exist)", "storage_error", 0.8),
]


@dataclass(slots=True)
class DiagnosisResult:
    diagnosis_class: str
    confidence: float
    evidence_lines: list[str]
    runbook_step: str
    summary: str
    used_llm: bool


def diagnose_deterministic(evidence_lines: list[str]) -> DiagnosisResult:
    body = " ".join(evidence_lines).lower()
    for pattern, klass, confidence in _HEURISTIC_RULES:
        if re.search(pattern, body):
            step = RUNBOOK_STEPS.get(klass, RUNBOOK_STEPS["unknown"])
            return DiagnosisResult(
                diagnosis_class=klass,
                confidence=confidence,
                evidence_lines=evidence_lines[:6],
                runbook_step=step,
                summary=f"Likely {klass.replace('_', ' ')} based on error evidence.",
                used_llm=False,
            )
    return DiagnosisResult(
        diagnosis_class="unknown",
        confidence=0.35,
        evidence_lines=evidence_lines[:6],
        runbook_step=RUNBOOK_STEPS["unknown"],
        summary="Insufficient deterministic evidence for a high-confidence class.",
        used_llm=False,
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _coerce_llm_result(payload: dict[str, Any], evidence_lines: list[str]) -> DiagnosisResult | None:
    klass = str(payload.get("class", "unknown")).strip().lower()
    if klass not in DIAGNOSIS_CLASSES:
        klass = "unknown"
    confidence = float(payload.get("confidence", 0.0) or 0.0)
    evidence = payload.get("evidence_lines")
    if not isinstance(evidence, list):
        evidence = evidence_lines[:6]
    evidence_norm = [str(line) for line in evidence][:6]
    runbook_step = str(payload.get("runbook_step") or RUNBOOK_STEPS.get(klass, RUNBOOK_STEPS["unknown"]))
    summary = str(payload.get("summary") or f"Likely {klass.replace('_', ' ')}.")
    return DiagnosisResult(
        diagnosis_class=klass,
        confidence=max(0.0, min(confidence, 1.0)),
        evidence_lines=evidence_norm,
        runbook_step=runbook_step,
        summary=summary,
        used_llm=True,
    )


def _prompt_for(evidence_lines: list[str]) -> str:
    taxonomy = ", ".join(sorted(DIAGNOSIS_CLASSES))
    evidence_block = "\n".join(f"- {line}" for line in evidence_lines[:12])
    return (
        "Classify this Cryo maintenance failure.\n"
        f"Allowed classes: {taxonomy}.\n"
        "Return JSON only with keys: class, confidence, evidence_lines, runbook_step, summary.\n"
        "Use confidence in [0,1]. If uncertain, class must be unknown with confidence <= 0.5.\n"
        f"Evidence:\n{evidence_block}"
    )


async def diagnose_with_optional_llm(
    session: Session,
    *,
    evidence_lines: list[str],
    run_id: uuid.UUID,
    table_id: uuid.UUID | None = None,
    skillset_id: uuid.UUID | None = None,
    skillset_name: str | None = None,
    skillset_version: str | None = None,
) -> tuple[DiagnosisResult, uuid.UUID | None]:
    """Run LLM diagnosis when available, fallback to deterministic classification."""
    deterministic = diagnose_deterministic(evidence_lines)
    response, observation = await complete_with_budget_and_observation(
        session,
        _prompt_for(evidence_lines),
        invoke=LLMInvokeContext(
            feature="run_failure_diagnosis",
            prompt_name="run_failure_diagnosis",
            skillset_id=skillset_id,
            skillset_name=skillset_name,
            skillset_version=skillset_version,
            run_id=run_id,
            table_id=table_id,
        ),
        max_tokens=280,
    )
    if response is None:
        return deterministic, observation.id if observation is not None else None

    payload = _extract_json(response.content)
    if payload is None:
        return deterministic, observation.id
    llm = _coerce_llm_result(payload, evidence_lines)
    if llm is None:
        return deterministic, observation.id
    if llm.diagnosis_class == "unknown" or llm.confidence < 0.55:
        return deterministic, observation.id
    return llm, observation.id
