from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, LayoutRecommendation, Table

MIN_ARM_QUERIES = 30
MIN_SCAN_IMPROVEMENT_PCT = 5.0
MAX_P95_REGRESSION_PCT = 10.0
MAX_ERROR_RATE_INCREASE = 0.005


@dataclass(frozen=True, slots=True)
class CounterfactualArm:
    query_count: int
    query_signature_set_sha256: str
    total_scan_bytes: int
    total_files_read: int
    error_count: int
    p95_latency_ms: float


@dataclass(frozen=True, slots=True)
class CounterfactualExperiment:
    experiment_id: str
    workload_sha256: str
    dataset_sha256: str
    pto_params_sha256: str
    rewrite_scope_sha256: str
    control: CounterfactualArm
    candidate: CounterfactualArm
    isolated_clone: bool
    randomization_unit: str = "query_signature"


@dataclass(frozen=True, slots=True)
class CounterfactualReport:
    status: str
    experiment_id: str
    scan_improvement_pct: float | None
    file_read_improvement_pct: float | None
    p95_latency_change_pct: float | None
    control_error_rate: float | None
    candidate_error_rate: float | None
    qualifies_for_canary: bool
    reasons: tuple[str, ...]


def evaluate_counterfactual_experiment(
    experiment: CounterfactualExperiment,
) -> CounterfactualReport:
    reasons: list[str] = []
    for name, value in (
        ("workload", experiment.workload_sha256),
        ("dataset", experiment.dataset_sha256),
        ("pto_params", experiment.pto_params_sha256),
        ("rewrite_scope", experiment.rewrite_scope_sha256),
    ):
        if not _is_sha256(value):
            reasons.append(f"invalid_{name}_sha256")
    if not experiment.isolated_clone:
        reasons.append("candidate_not_isolated_clone")
    if experiment.randomization_unit != "query_signature":
        reasons.append("unsupported_randomization_unit")
    for label, arm in (("control", experiment.control), ("candidate", experiment.candidate)):
        if not _is_sha256(arm.query_signature_set_sha256):
            reasons.append(f"invalid_{label}_query_signature_set_sha256")
        if arm.query_count < MIN_ARM_QUERIES:
            reasons.append(f"insufficient_{label}_queries")
        if arm.total_scan_bytes <= 0:
            reasons.append(f"invalid_{label}_scan_bytes")
        if arm.total_files_read <= 0:
            reasons.append(f"invalid_{label}_files_read")
        if arm.error_count < 0 or arm.error_count > arm.query_count:
            reasons.append(f"invalid_{label}_error_count")
        if arm.p95_latency_ms <= 0:
            reasons.append(f"invalid_{label}_p95_latency")
    if (
        experiment.control.query_signature_set_sha256
        != experiment.candidate.query_signature_set_sha256
        or experiment.control.query_signature_set_sha256 != experiment.workload_sha256
    ):
        reasons.append("workload_signature_mismatch")
    if reasons:
        return _report(experiment.experiment_id, reasons=reasons)

    control = experiment.control
    candidate = experiment.candidate
    scan_improvement = _improvement(control.total_scan_bytes, candidate.total_scan_bytes)
    file_improvement = _improvement(control.total_files_read, candidate.total_files_read)
    latency_change = (
        (candidate.p95_latency_ms - control.p95_latency_ms) / control.p95_latency_ms * 100
    )
    control_error_rate = control.error_count / control.query_count
    candidate_error_rate = candidate.error_count / candidate.query_count
    if scan_improvement < MIN_SCAN_IMPROVEMENT_PCT:
        reasons.append("scan_improvement_below_threshold")
    if latency_change > MAX_P95_REGRESSION_PCT:
        reasons.append("p95_latency_regression")
    if candidate_error_rate - control_error_rate > MAX_ERROR_RATE_INCREASE:
        reasons.append("error_rate_regression")
    return _report(
        experiment.experiment_id,
        reasons=reasons,
        scan_improvement=scan_improvement,
        file_improvement=file_improvement,
        latency_change=latency_change,
        control_error_rate=control_error_rate,
        candidate_error_rate=candidate_error_rate,
    )


def record_counterfactual_experiment(
    session: Session,
    table_id: uuid.UUID,
    experiment: CounterfactualExperiment,
    *,
    actor: str,
) -> CounterfactualReport:
    if session.get(Table, table_id) is None:
        raise ValueError(f"Table not found: {table_id}")
    recommendation = session.scalar(
        select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
    )
    if recommendation is None or (recommendation.detail or {}).get("source") != "pto":
        raise ValueError(f"PTO recommendation not found for table: {table_id}")
    detail = dict(recommendation.detail or {})
    differential = detail.get("differential_oracle")
    if not isinstance(differential, dict):
        raise ValueError("Recorded differential evidence is required")
    report = evaluate_counterfactual_experiment(experiment)
    binding_reasons = list(report.reasons)
    if experiment.pto_params_sha256 != differential.get("pto_params_sha256"):
        binding_reasons.append("pto_params_evidence_mismatch")
    if experiment.rewrite_scope_sha256 != differential.get("rewrite_scope_sha256"):
        binding_reasons.append("rewrite_scope_evidence_mismatch")
    if binding_reasons != list(report.reasons):
        report = _report(
            experiment.experiment_id,
            reasons=binding_reasons,
            scan_improvement=report.scan_improvement_pct,
            file_improvement=report.file_read_improvement_pct,
            latency_change=report.p95_latency_change_pct,
            control_error_rate=report.control_error_rate,
            candidate_error_rate=report.candidate_error_rate,
        )
    evidence: dict[str, object] = {
        "schema": "pto-controlled-shadow/v1",
        "evaluated_at": datetime.now(UTC).isoformat(),
        "experiment": asdict(experiment),
        "report": asdict(report),
    }
    detail["holdout_evidence"] = evidence
    recommendation.detail = detail
    session.add(
        AuditLog(
            table_id=table_id,
            action="pto_controlled_shadow_recorded",
            status="QUALIFIED" if report.qualifies_for_canary else "BLOCKED",
            engine="pto_shadow",
            detail=json.dumps(evidence, sort_keys=True, separators=(",", ":")),
            actor=actor,
        )
    )
    session.commit()
    return report


def _report(
    experiment_id: str,
    *,
    reasons: list[str],
    scan_improvement: float | None = None,
    file_improvement: float | None = None,
    latency_change: float | None = None,
    control_error_rate: float | None = None,
    candidate_error_rate: float | None = None,
) -> CounterfactualReport:
    qualifies = not reasons
    return CounterfactualReport(
        status="qualified" if qualifies else "rejected",
        experiment_id=experiment_id,
        scan_improvement_pct=_rounded(scan_improvement),
        file_read_improvement_pct=_rounded(file_improvement),
        p95_latency_change_pct=_rounded(latency_change),
        control_error_rate=_rounded(control_error_rate, 6),
        candidate_error_rate=_rounded(candidate_error_rate, 6),
        qualifies_for_canary=qualifies,
        reasons=tuple(sorted(set(reasons))),
    )


def _improvement(control: int, candidate: int) -> float:
    return (control - candidate) / control * 100


def _rounded(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)
