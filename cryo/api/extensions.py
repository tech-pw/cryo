from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from cryo.ai.anomaly import detect_anomalies
from cryo.ai.copilot import (
    answer_question,
    answer_verified_question,
    build_table_fleet_rows,
    list_verified_questions,
)
from cryo.ai.embedding_cache import find_similar_tables_cached
from cryo.ai.explain import explain_health_change
from cryo.ai.llm import SUPPORTED_PROVIDERS, create_llm_client
from cryo.ai.pii import classify_table_columns
from cryo.ai.runtime import LLMInvokeContext, complete_with_budget_and_observation
from cryo.api.deps import (
    CurrentPrincipal,
    SessionDep,
    require_admin,
    require_evidence_collector,
    require_operator,
    require_viewer,
)
from cryo.api.extensions_helpers import (
    build_mcp_server,
    gather_copilot_context,
    provider_api_key,
    slack_webhook_url,
    stats_snapshot,
    table_stats_dict,
)
from cryo.api.extensions_routes.ai_features import router as ai_features_router
from cryo.api.extensions_routes.benefit import router as benefit_router
from cryo.api.extensions_routes.compliance import router as compliance_router
from cryo.api.extensions_routes.qpp import router as qpp_router
from cryo.api.extensions_routes.triggers import router as triggers_router
from cryo.api.schemas import JsonDict
from cryo.api.validators import validate_cron_field
from cryo.db.models import (
    AIProvider,
    SlackConfig,
    Table,
    TableStats,
    TableStatsHistory,
)
from cryo.security.crypto import encrypt_secret

router = APIRouter(prefix="/api/v1")
router.include_router(compliance_router)
router.include_router(benefit_router)
router.include_router(ai_features_router)
router.include_router(qpp_router)
router.include_router(triggers_router)


# Backwards-compatible aliases (legacy code refers to the underscore-prefixed names)
_provider_api_key = provider_api_key
_slack_webhook_url = slack_webhook_url
_stats_snapshot = stats_snapshot
_table_stats_dict = table_stats_dict
_mcp_server = build_mcp_server
_gather_copilot_context = gather_copilot_context


class AnomalyResponse(BaseModel):
    table_id: uuid.UUID
    anomalies: list[JsonDict]


class ExplainHealthRequest(BaseModel):
    before_scan_at: datetime | None = None
    after_scan_at: datetime | None = None
    preset_name: str | None = None


class ExplainHealthResponse(BaseModel):
    table_id: uuid.UUID
    explanation: str


class CopilotQuestionRequest(BaseModel):
    question_id: str
    context: JsonDict = Field(default_factory=dict)


class CopilotQuestionResponse(BaseModel):
    question_id: str
    result: JsonDict


class McpToolCallRequest(BaseModel):
    name: str
    arguments: JsonDict = Field(default_factory=dict)


class McpToolCallResponse(BaseModel):
    result: JsonDict


class ComplianceStatusResponse(BaseModel):
    table_id: uuid.UUID
    table_ref: str
    pending_deletion: JsonDict | None = None
    abac_policy_count: int = 0
    abac_enforcement_note: str | None = None


class ProtectSnapshotRequest(BaseModel):
    snapshot_id: int
    label: str | None = None


class ProtectSnapshotResponse(BaseModel):
    table_id: uuid.UUID
    snapshot_id: int
    label: str | None = None


class CatalogLatencyResponse(BaseModel):
    source_id: uuid.UUID
    source_name: str
    operations: JsonDict


# AI Provider schemas
class AIProviderCreate(BaseModel):
    name: str
    provider_type: str  # anthropic, openai
    api_key: str  # will be encrypted before storage
    model: str
    config: JsonDict = Field(default_factory=dict)
    enabled: bool = True
    is_default: bool = False


class AIProviderUpdate(BaseModel):
    name: str | None = None
    provider_type: str | None = None
    api_key: str | None = None  # only update if provided
    model: str | None = None
    config: JsonDict | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class AIProviderRead(BaseModel):
    id: uuid.UUID
    name: str
    provider_type: str
    model: str
    config: JsonDict
    enabled: bool
    is_default: bool
    has_api_key: bool  # don't expose actual key
    created_at: datetime
    updated_at: datetime


class AIProviderTestRequest(BaseModel):
    prompt: str = "Say hello in one sentence."


class AIProviderTestResponse(BaseModel):
    success: bool
    response: str | None = None
    error: str | None = None
    model: str | None = None
    tokens_used: int | None = None


class AIProviderTestConfigRequest(BaseModel):
    """A not-yet-saved provider config, so the create/edit drawer can verify it before saving."""

    provider_type: str
    api_key: str = ""
    model: str
    config: JsonDict = Field(default_factory=dict)
    prompt: str = "Say hello in one sentence."
    # Set when testing an *edit* where the API key field was left blank ("keep existing key") --
    # falls back to the real stored (encrypted) key instead of testing an empty string.
    provider_id: uuid.UUID | None = None


class AIChatRequest(BaseModel):
    prompt: str
    context: JsonDict = Field(default_factory=dict)


class AIChatResponse(BaseModel):
    response: str
    model: str
    provider: str
    tokens_used: int


@router.get(
    "/tables/{table_id}/anomalies",
    response_model=AnomalyResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def get_table_anomalies(table_id: uuid.UUID, session: SessionDep) -> AnomalyResponse:
    history = session.scalars(
        select(TableStatsHistory)
        .where(TableStatsHistory.table_id == table_id)
        .order_by(TableStatsHistory.scan_at)
        .limit(30)
    )
    rows = []
    for row in history:
        raw = dict(row.raw or {})
        rows.append(
            {
                "scan_at": row.scan_at.isoformat(),
                "small_files_count": row.small_files_count,
                "total_files": row.total_files,
                "total_size_gb": row.total_size_gb,
                "overall_score": row.overall_score,
                "total_snapshots": raw.get("total_snapshots"),
                "total_delete_files": raw.get("total_delete_files"),
                "manifest_count": raw.get("manifest_count"),
                "projected_savings_usd_month": raw.get("projected_savings_usd_month"),
                "avg_null_rate": raw.get("avg_null_rate"),
            }
        )
    return AnomalyResponse(
        table_id=table_id,
        anomalies=[
            {
                "metric": item.metric,
                "value": item.value,
                "baseline": item.baseline,
                "z_score": item.z_score,
                "severity": item.severity,
            }
            for item in detect_anomalies(
                rows,
                metrics=(
                    "small_files_count",
                    "total_files",
                    "total_size_gb",
                    "total_snapshots",
                    "total_delete_files",
                    "manifest_count",
                    "avg_null_rate",
                ),
            )
        ],
    )


@router.post(
    "/tables/{table_id}/explain-health",
    response_model=ExplainHealthResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def explain_table_health(
    table_id: uuid.UUID,
    payload: ExplainHealthRequest,
    session: SessionDep,
) -> ExplainHealthResponse:
    before = _stats_snapshot(session, table_id, payload.before_scan_at, offset=-2)
    after = _stats_snapshot(session, table_id, payload.after_scan_at, offset=-1)
    if before is None or after is None:
        raise HTTPException(status_code=404, detail="Insufficient stats history for comparison")
    explanation = explain_health_change(before, after, preset_name=payload.preset_name)
    return ExplainHealthResponse(table_id=table_id, explanation=explanation)


@router.get(
    "/ai/copilot/questions",
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def copilot_questions() -> list[dict[str, str]]:
    return list_verified_questions()


@router.post(
    "/ai/copilot/ask",
    response_model=CopilotQuestionResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def copilot_ask(payload: CopilotQuestionRequest, session: SessionDep) -> CopilotQuestionResponse:
    try:
        result = answer_verified_question(payload.question_id, payload.context, session)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CopilotQuestionResponse(question_id=payload.question_id, result=result)


class CopilotSearchRequest(BaseModel):
    question: str
    min_similarity: float = 0.55


class CopilotSearchResponse(BaseModel):
    source: str
    question_id: str | None = None
    similarity: float | None = None
    matched_text: str | None = None
    result: JsonDict | None = None
    answer: str | None = None


@router.post(
    "/ai/copilot/search",
    response_model=CopilotSearchResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def copilot_embedding_search(
    payload: CopilotSearchRequest,
    session: SessionDep,
) -> CopilotSearchResponse:
    """Embedding fallback when a question misses the verified library."""
    outcome = answer_question(
        payload.question,
        session,
        min_similarity=payload.min_similarity,
    )
    return CopilotSearchResponse(
        source=str(outcome.get("source", "none")),
        question_id=outcome.get("question_id"),
        similarity=outcome.get("similarity"),
        matched_text=outcome.get("matched_text"),
        result=outcome.get("result"),
        answer=outcome.get("answer"),
    )


class PiiClassificationResponse(BaseModel):
    table_id: uuid.UUID
    matches: list[JsonDict]


@router.get(
    "/tables/{table_id}/pii-classification",
    response_model=PiiClassificationResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai", "governance"],
)
def get_table_pii_classification(
    table_id: uuid.UUID,
    session: SessionDep,
    threshold: float = 0.72,
) -> PiiClassificationResponse:
    stats = session.get(TableStats, table_id)
    if stats is None:
        raise HTTPException(status_code=404, detail="Table stats not found")
    raw = stats.raw or {}
    columns = raw.get("column_quality") or []
    matches = classify_table_columns(columns, threshold=threshold)
    return PiiClassificationResponse(
        table_id=table_id,
        matches=[
            {
                "column_name": m.column_name,
                "column_type": m.column_type,
                "pii_category": m.pii_category,
                "similarity": m.similarity,
                "already_tagged": m.already_tagged,
            }
            for m in matches
        ],
    )


class SimilarTablesResponse(BaseModel):
    table_id: uuid.UUID
    similar: list[JsonDict]


@router.get(
    "/tables/{table_id}/similar",
    response_model=SimilarTablesResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def get_similar_tables(
    table_id: uuid.UUID,
    session: SessionDep,
    min_similarity: float = 0.90,
    limit: int = 20,
) -> SimilarTablesResponse:
    fleet = build_table_fleet_rows(session)
    target = next((row for row in fleet if row["table_id"] == str(table_id)), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Table not found")
    similar = find_similar_tables_cached(
        session, target, fleet, min_similarity=min_similarity, limit=limit
    )
    return SimilarTablesResponse(
        table_id=table_id,
        similar=[
            {
                "table_id": s.table_id,
                "schema_name": s.schema_name,
                "table_name": s.table_name,
                "similarity": s.similarity,
                "shared_signals": s.shared_signals,
            }
            for s in similar
        ],
    )


class BanditStatusResponse(BaseModel):
    observations: int
    confident: bool
    config: JsonDict


@router.get(
    "/governor/bandit/status",
    response_model=BanditStatusResponse,
    dependencies=[Depends(require_viewer)],
    tags=["governor"],
)
def governor_bandit_status(session: SessionDep) -> BanditStatusResponse:
    from cryo.governor.admission import AdmissionConfig
    from cryo.governor.bandit_store import load_bandit

    bandit = load_bandit(session)
    base = AdmissionConfig()
    tuned, confident = bandit.suggest_config(base)
    return BanditStatusResponse(
        observations=bandit.state.observations,
        confident=confident,
        config={
            "high_load_ratio": tuned.high_load_ratio,
            "high_load_fraction": tuned.high_load_fraction,
            "per_replica_cap": tuned.per_replica_cap,
        },
    )


class CostModelEvaluateResponse(BaseModel):
    mae_learned: float
    mae_heuristic: float
    beats_heuristic: bool
    band_coverage: float
    n_samples: int
    n_evaluated: int
    abstentions: int
    abstention_rate: float
    lineage_eligible: int
    lineage_complete: int
    lineage_completeness: float


@router.get(
    "/ai/cost-model/evaluate",
    response_model=CostModelEvaluateResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai", "cost"],
)
def evaluate_learned_cost_model(session: SessionDep) -> CostModelEvaluateResponse:
    from cryo.config import get_settings
    from cryo.intelligence.outcomes import build_outcome_dataset
    from cryo.ml.cost_model import evaluate_model, train_cost_model

    dataset = build_outcome_dataset(session)
    observations = dataset.calibration_rows()
    settings = get_settings()
    split = max(1, int(len(observations) * 0.75))
    training = observations[:split]
    holdout = observations[split:]
    model = train_cost_model(training, price_per_gb_month=settings.cost_price_per_gb_month)
    if model is None:
        return CostModelEvaluateResponse(
            mae_learned=0.0,
            mae_heuristic=0.0,
            beats_heuristic=False,
            band_coverage=0.0,
            n_samples=len(holdout),
            n_evaluated=0,
            abstentions=len(holdout),
            abstention_rate=1.0 if holdout else 0.0,
            lineage_eligible=dataset.eligible_count,
            lineage_complete=dataset.complete_count,
            lineage_completeness=dataset.completeness,
        )
    metrics = evaluate_model(holdout, model, price_per_gb_month=settings.cost_price_per_gb_month)
    return CostModelEvaluateResponse(
        **metrics,
        lineage_eligible=dataset.eligible_count,
        lineage_complete=dataset.complete_count,
        lineage_completeness=dataset.completeness,
    )


class IntervalGroupEvaluationResponse(BaseModel):
    engine: str | None
    action: str
    scope: str | None
    n_samples: int
    n_evaluated: int
    mape_median: float | None
    mape_p90: float | None
    interval_coverage: float | None


class IntervalEvaluationResponse(BaseModel):
    target: str
    target_coverage: float
    n_samples: int
    n_evaluated: int
    abstentions: int
    abstention_rate: float
    mape_median: float | None
    mape_p90: float | None
    interval_coverage: float | None
    by_group: list[IntervalGroupEvaluationResponse]


class RuntimeRewriteIntervalEvaluateResponse(BaseModel):
    rewrite_bytes: IntervalEvaluationResponse
    runtime_ms: IntervalEvaluationResponse
    lineage_eligible: int
    lineage_complete: int
    lineage_completeness: float


class DeleteCohortMetricsResponse(BaseModel):
    cohort: str
    observations: int
    median_delete_pct_reduction: float | None
    median_duration_ms: float | None
    median_rewrite_bytes: float | None
    median_value_per_rewrite_gib: float | None
    median_p95_latency_reduction_ms: float | None


class DeleteLifecycleEvidenceResponse(BaseModel):
    schema_version: str = Field(alias="schema")
    status: str
    qualifies_policy_gate: bool
    lifecycle: DeleteCohortMetricsResponse
    threshold_only: DeleteCohortMetricsResponse
    matched_engines: tuple[str, ...]
    observed_value_lift: float | None
    blockers: tuple[str, ...]


@router.get(
    "/ai/cost-model/runtime-intervals/evaluate",
    response_model=RuntimeRewriteIntervalEvaluateResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai", "cost"],
)
def evaluate_runtime_rewrite_intervals(
    session: SessionDep,
) -> RuntimeRewriteIntervalEvaluateResponse:
    """Chronological-holdout calibration report for I4 runtime/rewrite-byte intervals.

    This is a locally computed report over whatever maintenance-outcome lineage exists in this
    deployment's audit history. It is not a claim of production accuracy and does not by itself
    satisfy the Section 5.3 exit gate, which additionally requires a representative production
    fleet and a statistically significant sample.
    """
    from cryo.intelligence.outcomes import build_outcome_dataset
    from cryo.ml.interval_model import (
        REWRITE_BYTES,
        RUNTIME_MS,
        IntervalTarget,
        evaluate_interval_model,
        fit_interval_model,
    )

    dataset = build_outcome_dataset(session)
    observations = dataset.calibration_rows()
    split = max(1, int(len(observations) * 0.75))
    training = observations[:split]
    holdout = observations[split:]

    def _report(target: IntervalTarget) -> IntervalEvaluationResponse:
        model = fit_interval_model(training, target)
        evaluation = evaluate_interval_model(holdout, model)
        return IntervalEvaluationResponse(
            target=evaluation.target,
            target_coverage=evaluation.target_coverage,
            n_samples=evaluation.n_samples,
            n_evaluated=evaluation.n_evaluated,
            abstentions=evaluation.abstentions,
            abstention_rate=evaluation.abstention_rate,
            mape_median=evaluation.mape_median,
            mape_p90=evaluation.mape_p90,
            interval_coverage=evaluation.interval_coverage,
            by_group=[
                IntervalGroupEvaluationResponse(
                    engine=group.engine,
                    action=group.action,
                    scope=group.scope,
                    n_samples=group.n_samples,
                    n_evaluated=group.n_evaluated,
                    mape_median=group.mape_median,
                    mape_p90=group.mape_p90,
                    interval_coverage=group.interval_coverage,
                )
                for group in evaluation.by_group
            ],
        )

    return RuntimeRewriteIntervalEvaluateResponse(
        rewrite_bytes=_report(REWRITE_BYTES),
        runtime_ms=_report(RUNTIME_MS),
        lineage_eligible=dataset.eligible_count,
        lineage_complete=dataset.complete_count,
        lineage_completeness=dataset.completeness,
    )


@router.get(
    "/ai/delete-lifecycle/evaluate",
    response_model=DeleteLifecycleEvidenceResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai", "cost"],
)
def evaluate_delete_lifecycle_evidence(
    session: SessionDep,
) -> DeleteLifecycleEvidenceResponse:
    from cryo.intelligence.delete_lifecycle_evidence import evaluate_delete_lifecycle
    from cryo.intelligence.outcomes import build_outcome_dataset

    evidence = evaluate_delete_lifecycle(build_outcome_dataset(session))
    return DeleteLifecycleEvidenceResponse(
        schema=evidence.schema,
        status=evidence.status,
        qualifies_policy_gate=evidence.qualifies_policy_gate,
        lifecycle=DeleteCohortMetricsResponse(**dataclasses.asdict(evidence.lifecycle)),
        threshold_only=DeleteCohortMetricsResponse(**dataclasses.asdict(evidence.threshold_only)),
        matched_engines=evidence.matched_engines,
        observed_value_lift=evidence.observed_value_lift,
        blockers=evidence.blockers,
    )


class HealthClassifierResponse(BaseModel):
    table_id: uuid.UUID
    needs_emergency_compaction: bool
    probability: float
    method: str


@router.get(
    "/tables/{table_id}/health-classification",
    response_model=HealthClassifierResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def classify_table_health(
    table_id: uuid.UUID,
    session: SessionDep,
) -> HealthClassifierResponse:
    from cryo.ml.health_classifier import HealthLabel, classify_health_few_shot

    stats = session.get(TableStats, table_id)
    if stats is None:
        raise HTTPException(status_code=404, detail="Table stats not found")

    # Build labeled examples from fleet history (tables with emergency-grade health).
    labeled: list[HealthLabel] = []
    example_stats: dict[str, dict[str, Any]] = {}
    fleet_rows = session.execute(
        select(Table.id, TableStats)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(Table.is_active.is_(True))
        .limit(100)
    )
    for tid, row_stats in fleet_rows:
        tid_str = str(tid)
        sig = {
            "total_files": row_stats.total_files,
            "small_files_count": row_stats.small_files_count,
            "delete_file_pct": row_stats.delete_file_pct,
            "manifest_count": row_stats.manifest_count,
            "total_snapshots": row_stats.total_snapshots,
            "partition_skew_ratio": row_stats.partition_skew_ratio,
            "avg_file_size_mb": row_stats.avg_file_size_mb,
            "overall_score": row_stats.overall_score,
        }
        example_stats[tid_str] = sig
        emergency = row_stats.grade in ("E", "F") or (row_stats.overall_score or 100) < 50
        labeled.append(HealthLabel(table_id=tid_str, needs_emergency_compaction=emergency))

    target_sig = example_stats.get(str(table_id), {})
    target_sig = {**target_sig, "table_id": str(table_id)}
    pred = classify_health_few_shot(target_sig, labeled, example_stats)
    return HealthClassifierResponse(
        table_id=table_id,
        needs_emergency_compaction=pred.needs_emergency_compaction,
        probability=pred.probability,
        method=pred.method,
    )


class SchedulingBaselineRead(BaseModel):
    name: str
    order: list[str]
    utility: float
    regret: float
    regret_ratio: float


class AutoCompCompareResponse(BaseModel):
    table_id: uuid.UUID
    static_order: list[str]
    autocomp_order: list[str]
    improvement_ratio: float
    autocomp_wins: bool
    confidence: float
    evidence_sufficient: bool
    baselines: list[SchedulingBaselineRead]


@router.get(
    "/tables/{table_id}/autocomp/compare",
    response_model=AutoCompCompareResponse,
    dependencies=[Depends(require_viewer)],
    tags=["analytics"],
)
def compare_autocomp_scheduling(
    table_id: uuid.UUID,
    session: SessionDep,
) -> AutoCompCompareResponse:
    from cryo.planner.benefit_score import compare_scheduling_strategies

    result = compare_scheduling_strategies(session, table_id)
    return AutoCompCompareResponse(table_id=table_id, **result)


class PtoShadowRecommendationResponse(BaseModel):
    table_id: uuid.UUID
    recommendation_id: uuid.UUID | None = None
    status: str
    shadow: bool = True
    model: str = "heuristic_v1"
    abstention_reason: str | None = None
    candidates_considered: int
    pto_params: JsonDict | None = None
    predicted_prune_improvement_pct: float | None = None
    estimated_rewrite_gb: float | None = None


@router.post(
    "/tables/{table_id}/pto/recommend",
    response_model=PtoShadowRecommendationResponse,
    dependencies=[Depends(require_operator)],
    tags=["analytics"],
)
def refresh_pto_shadow_recommendation(
    table_id: uuid.UUID,
    session: SessionDep,
) -> PtoShadowRecommendationResponse:
    from cryo.optimizer.pto import generate_pto_recommendation

    try:
        result = generate_pto_recommendation(session, table_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    recommendation = result.recommendation
    if recommendation is None:
        return PtoShadowRecommendationResponse(
            table_id=table_id,
            status="abstained",
            abstention_reason=result.abstention_reason,
            candidates_considered=result.candidates_considered,
        )
    detail = dict(recommendation.detail or {})
    return PtoShadowRecommendationResponse(
        table_id=table_id,
        recommendation_id=recommendation.id,
        status=recommendation.status,
        candidates_considered=result.candidates_considered,
        pto_params=detail.get("pto_params"),
        predicted_prune_improvement_pct=detail.get("predicted_prune_improvement_pct"),
        estimated_rewrite_gb=detail.get("estimated_rewrite_gb"),
    )


class PtoSimulationResponse(BaseModel):
    table_id: uuid.UUID
    recommendation_id: uuid.UUID | None = None
    status: str
    method: str
    observations: int
    baseline_scan_fraction: float | None = None
    candidate_scan_fraction: float | None = None
    estimated_improvement_pct: float | None = None
    improvement_interval_pct: tuple[float, float] | None = None
    abstention_reason: str | None = None


@router.post(
    "/tables/{table_id}/pto/simulate",
    response_model=PtoSimulationResponse,
    dependencies=[Depends(require_operator)],
    tags=["analytics"],
)
def simulate_pto_shadow_recommendation(
    table_id: uuid.UUID,
    session: SessionDep,
) -> PtoSimulationResponse:
    from cryo.optimizer.pto import simulate_pto_recommendation

    result = simulate_pto_recommendation(session, table_id)
    return PtoSimulationResponse(table_id=table_id, **dataclasses.asdict(result))


class PtoHoldoutResponse(BaseModel):
    table_id: uuid.UUID
    recommendation_id: uuid.UUID | None = None
    status: str
    method: str
    train_observations: int
    holdout_observations: int
    train_scan_fraction: float | None = None
    holdout_scan_fraction: float | None = None
    stability_delta: float | None = None
    holdout_improvement_pct: float | None = None
    qualifies_for_canary: bool
    qualification_reason: str
    abstention_reason: str | None = None


@router.post(
    "/tables/{table_id}/pto/holdout",
    response_model=PtoHoldoutResponse,
    dependencies=[Depends(require_operator)],
    tags=["analytics"],
)
def record_pto_shadow_holdout(
    table_id: uuid.UUID,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> PtoHoldoutResponse:
    from cryo.optimizer.pto import record_pto_holdout

    result = record_pto_holdout(session, table_id, actor=principal.subject)
    return PtoHoldoutResponse(table_id=table_id, **dataclasses.asdict(result))


class CounterfactualArmPayload(BaseModel):
    query_count: int = Field(ge=0)
    query_signature_set_sha256: str
    total_scan_bytes: int = Field(ge=0)
    total_files_read: int = Field(ge=0)
    error_count: int = Field(ge=0)
    p95_latency_ms: float = Field(gt=0)


class CounterfactualExperimentPayload(BaseModel):
    experiment_id: str
    workload_sha256: str
    dataset_sha256: str
    pto_params_sha256: str
    rewrite_scope_sha256: str
    control: CounterfactualArmPayload
    candidate: CounterfactualArmPayload
    isolated_clone: bool
    randomization_unit: str = "query_signature"


class CounterfactualReportResponse(BaseModel):
    table_id: uuid.UUID
    status: str
    experiment_id: str
    scan_improvement_pct: float | None
    file_read_improvement_pct: float | None
    p95_latency_change_pct: float | None
    control_error_rate: float | None
    candidate_error_rate: float | None
    qualifies_for_canary: bool
    reasons: tuple[str, ...]


@router.post(
    "/tables/{table_id}/pto/controlled-shadow",
    response_model=CounterfactualReportResponse,
    dependencies=[Depends(require_operator)],
    tags=["analytics"],
)
def record_pto_controlled_shadow(
    table_id: uuid.UUID,
    payload: CounterfactualExperimentPayload,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> CounterfactualReportResponse:
    from cryo.optimizer.pto import (
        CounterfactualArm,
        CounterfactualExperiment,
        record_counterfactual_experiment,
    )

    experiment = CounterfactualExperiment(
        experiment_id=payload.experiment_id,
        workload_sha256=payload.workload_sha256,
        dataset_sha256=payload.dataset_sha256,
        pto_params_sha256=payload.pto_params_sha256,
        rewrite_scope_sha256=payload.rewrite_scope_sha256,
        control=CounterfactualArm(**payload.control.model_dump()),
        candidate=CounterfactualArm(**payload.candidate.model_dump()),
        isolated_clone=payload.isolated_clone,
        randomization_unit=payload.randomization_unit,
    )
    try:
        report = record_counterfactual_experiment(
            session,
            table_id,
            experiment,
            actor=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CounterfactualReportResponse(table_id=table_id, **dataclasses.asdict(report))


class ProductionSafetyResponse(BaseModel):
    table_id: uuid.UUID
    status: str
    destination_id: uuid.UUID
    policy_sha256: str | None
    attempts: int
    failed_attempts: int
    effects: int
    unknown_effects: int
    failure_rate: float | None
    unknown_effect_rate: float | None
    rollback_drill_at: str | None
    current_destination_paused: bool
    qualifies_for_canary: bool
    reasons: tuple[str, ...]


@router.post(
    "/tables/{table_id}/pto/production-safety/{destination_id}",
    response_model=ProductionSafetyResponse,
    dependencies=[Depends(require_operator)],
    tags=["analytics"],
)
def record_pto_production_safety(
    table_id: uuid.UUID,
    destination_id: uuid.UUID,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> ProductionSafetyResponse:
    from cryo.optimizer.production_safety import record_production_safety

    try:
        report = record_production_safety(
            session,
            table_id,
            destination_id,
            actor=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProductionSafetyResponse(table_id=table_id, **dataclasses.asdict(report))


class PtoDriftResponse(BaseModel):
    table_id: uuid.UUID
    recommendation_id: uuid.UUID | None = None
    status: str
    distance: float | None = None
    threshold: float
    abstention_reason: str | None = None


@router.get(
    "/tables/{table_id}/pto/drift",
    response_model=PtoDriftResponse,
    dependencies=[Depends(require_viewer)],
    tags=["analytics"],
)
def get_pto_drift(
    table_id: uuid.UUID,
    session: SessionDep,
) -> PtoDriftResponse:
    from cryo.optimizer.pto import assess_pto_drift

    result = assess_pto_drift(session, table_id)
    return PtoDriftResponse(table_id=table_id, **dataclasses.asdict(result))


class WairBoundaryReportResponse(BaseModel):
    table_id: uuid.UUID
    status: str
    boundary_file_count: int
    selected_file_count: int
    proposed_rewrite_gb: float
    selected_file_paths: tuple[str, ...]
    abstention_reason: str | None = None
    window_days: int
    clustering_column: str | None = None
    savings_cpu_seconds_per_window: float
    recluster_cpu_seconds: float
    net_cpu_seconds: float
    aggressiveness: float
    rewrite_gb_per_hour: float | None = None
    boundary_values: list[Any]
    spark_sql: str | None = None
    model_notes: list[str]


class DurationModelResponse(BaseModel):
    configured: str
    status: str
    calibration: dict[str, Any] | None = None


@router.get(
    "/maintenance/duration-model",
    response_model=DurationModelResponse,
    dependencies=[Depends(require_viewer)],
)
def get_duration_model(session: SessionDep) -> DurationModelResponse:
    """Ernest rewrite-duration fit on recent batch statements vs the pooled-rate estimate."""
    from cryo.config import get_settings
    from cryo.maintenance.coordinator import duration_calibration

    calibration = duration_calibration(session)
    return DurationModelResponse(
        configured=get_settings().maintenance_duration_model,
        status="insufficient_samples" if calibration is None else "calibrated",
        calibration=calibration.as_dict() if calibration is not None else None,
    )


class EngineRewriteSemanticsResponse(BaseModel):
    engine: str
    supported: bool
    strategy: str
    target_file_size_mb: int
    clustering_columns: tuple[str, ...]
    rendered_sql_sha256: str | None
    limitations: tuple[str, ...]


class RewriteScopeSemanticsResponse(BaseModel):
    bounded: bool
    equivalent: bool
    estimated_bytes: int | None
    file_count: int | None
    predicate_count: int
    normalized_predicates_sha256: str | None
    limitations: tuple[str, ...]


class RewriteOutputSnapshotPayload(BaseModel):
    engine: str
    row_count: int = Field(ge=0)
    row_multiset_sha256: str
    schema_sha256: str
    partition_spec_sha256: str
    touched_scope_sha256: tuple[str, ...]
    snapshot_id: int | None = Field(default=None, ge=1)
    metadata_location_sha256: str | None = None


class RewriteConformancePayload(BaseModel):
    fixture_id: str
    provenance: str = "synthetic_fixture"
    baseline: RewriteOutputSnapshotPayload
    trino: RewriteOutputSnapshotPayload
    spark: RewriteOutputSnapshotPayload
    allowed_scope_sha256: tuple[str, ...]


class DeployedRewriteConformancePayload(RewriteConformancePayload):
    provenance: str = "deployed_collector/v1"
    collected_at: datetime
    deployment_id: str = Field(min_length=1, max_length=256)
    environment: str
    pto_params_sha256: str
    rewrite_scope_sha256: str
    attestation_sha256: str


class RewriteConformanceResponse(BaseModel):
    table_id: uuid.UUID
    status: str
    fixture_id: str
    provenance: str
    conformant: bool
    invariant_checks: tuple[str, ...]
    reasons: tuple[str, ...]
    qualifies_production_gate: bool
    qualification_reason: str


class DifferentialOracleResponse(BaseModel):
    table_id: uuid.UUID
    recommendation_id: uuid.UUID | None
    status: str
    equivalent: bool
    canary_eligible: bool
    trino: EngineRewriteSemanticsResponse | None
    spark: EngineRewriteSemanticsResponse | None
    scope: RewriteScopeSemanticsResponse | None
    reasons: tuple[str, ...]


@router.get(
    "/tables/{table_id}/layout-differential",
    response_model=DifferentialOracleResponse,
    dependencies=[Depends(require_viewer)],
    tags=["analytics"],
)
def get_layout_differential(
    table_id: uuid.UUID,
    session: SessionDep,
) -> DifferentialOracleResponse:
    from cryo.optimizer.differential import evaluate_layout_recommendation

    try:
        report = evaluate_layout_recommendation(session, table_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DifferentialOracleResponse(table_id=table_id, **dataclasses.asdict(report))


@router.post(
    "/tables/{table_id}/layout-differential/record",
    response_model=DifferentialOracleResponse,
    dependencies=[Depends(require_operator)],
    tags=["analytics"],
)
def record_layout_differential(
    table_id: uuid.UUID,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> DifferentialOracleResponse:
    from cryo.optimizer.differential import record_layout_differential as record

    try:
        report = record(session, table_id, actor=principal.subject)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DifferentialOracleResponse(table_id=table_id, **dataclasses.asdict(report))


@router.post(
    "/tables/{table_id}/layout-conformance/record",
    response_model=RewriteConformanceResponse,
    dependencies=[Depends(require_operator)],
    tags=["analytics"],
)
def record_layout_conformance(
    table_id: uuid.UUID,
    payload: RewriteConformancePayload,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> RewriteConformanceResponse:
    from cryo.optimizer.conformance import (
        RewriteConformanceFixture,
        RewriteOutputSnapshot,
        record_rewrite_conformance,
    )

    fixture = RewriteConformanceFixture(
        fixture_id=payload.fixture_id,
        provenance=payload.provenance,
        baseline=RewriteOutputSnapshot(**payload.baseline.model_dump()),
        trino=RewriteOutputSnapshot(**payload.trino.model_dump()),
        spark=RewriteOutputSnapshot(**payload.spark.model_dump()),
        allowed_scope_sha256=payload.allowed_scope_sha256,
    )
    try:
        report = record_rewrite_conformance(session, table_id, fixture, actor=principal.subject)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RewriteConformanceResponse(table_id=table_id, **dataclasses.asdict(report))


@router.post(
    "/tables/{table_id}/layout-conformance/deployed",
    response_model=RewriteConformanceResponse,
    dependencies=[Depends(require_evidence_collector)],
    tags=["analytics"],
)
def record_deployed_layout_conformance(
    table_id: uuid.UUID,
    payload: DeployedRewriteConformancePayload,
    session: SessionDep,
    principal: CurrentPrincipal,
) -> RewriteConformanceResponse:
    from cryo.optimizer.conformance import (
        DeployedRewriteEvidence,
        RewriteConformanceFixture,
        RewriteOutputSnapshot,
        record_deployed_rewrite_conformance,
    )

    fixture = RewriteConformanceFixture(
        fixture_id=payload.fixture_id,
        provenance=payload.provenance,
        baseline=RewriteOutputSnapshot(**payload.baseline.model_dump()),
        trino=RewriteOutputSnapshot(**payload.trino.model_dump()),
        spark=RewriteOutputSnapshot(**payload.spark.model_dump()),
        allowed_scope_sha256=payload.allowed_scope_sha256,
    )
    evidence = DeployedRewriteEvidence(
        fixture=fixture,
        collected_at=payload.collected_at,
        deployment_id=payload.deployment_id,
        environment=payload.environment,
        pto_params_sha256=payload.pto_params_sha256,
        rewrite_scope_sha256=payload.rewrite_scope_sha256,
        attestation_sha256=payload.attestation_sha256,
    )
    try:
        report = record_deployed_rewrite_conformance(
            session,
            table_id,
            evidence,
            collector_id=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RewriteConformanceResponse(table_id=table_id, **dataclasses.asdict(report))


@router.post(
    "/tables/{table_id}/wair/analyze",
    response_model=WairBoundaryReportResponse,
    dependencies=[Depends(require_operator)],
    tags=["analytics"],
)
def analyze_wair_boundary_report(
    table_id: uuid.UUID,
    session: SessionDep,
    aggressiveness: Annotated[float, Query(ge=0.0, le=10.0)] = 1.0,
    cost_limit_cpu_seconds: Annotated[float | None, Query(gt=0.0)] = None,
) -> WairBoundaryReportResponse:
    from cryo.optimizer.wair import analyze_wair_boundaries

    try:
        report = analyze_wair_boundaries(
            session,
            table_id,
            aggressiveness=aggressiveness,
            cost_limit_cpu_seconds=cost_limit_cpu_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return WairBoundaryReportResponse(**dataclasses.asdict(report))


class NaturalLanguageRequest(BaseModel):
    question: str
    include_context: bool = True


class NaturalLanguageResponse(BaseModel):
    answer: str
    context_used: JsonDict | None = None
    model: str
    provider: str


@router.post(
    "/ai/copilot/natural",
    response_model=NaturalLanguageResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
async def copilot_natural_language(
    payload: NaturalLanguageRequest,
    session: SessionDep,
) -> NaturalLanguageResponse:
    """Answer a natural language question about the datalake using LLM."""
    # Gather context about the datalake
    context = None
    if payload.include_context:
        context = _gather_copilot_context(session)

    response, observation = await complete_with_budget_and_observation(
        session,
        payload.question,
        invoke=LLMInvokeContext(
            feature="copilot_natural",
            prompt_name="copilot_natural",
            extra={"include_context": payload.include_context},
        ),
        context=context,
        max_tokens=600,
    )
    if response is None:
        if observation.status == "budget_denied_reject":
            raise HTTPException(
                status_code=429,
                detail="LLM budget policy rejected this copilot request.",
            )
        fallback = answer_question(payload.question, session)
        fallback_answer = str(
            fallback.get("answer")
            or fallback.get("result")
            or "No LLM provider is configured; using deterministic copilot fallback."
        )
        return NaturalLanguageResponse(
            answer=fallback_answer,
            context_used=context,
            model="deterministic",
            provider="none",
        )

    return NaturalLanguageResponse(
        answer=response.content,
        context_used=context,
        model=response.model,
        provider=response.provider,
    )


# ============ AI Provider endpoints ============


@router.get(
    "/ai/providers/types",
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def list_provider_types() -> list[dict[str, Any]]:
    """List supported AI provider types with their available models."""
    return cast(list[dict[str, Any]], SUPPORTED_PROVIDERS)


@router.get(
    "/ai/providers",
    response_model=list[AIProviderRead],
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def list_ai_providers(session: SessionDep) -> list[AIProviderRead]:
    """List all configured AI providers."""
    providers = session.scalars(select(AIProvider).order_by(AIProvider.name))
    return [
        AIProviderRead(
            id=p.id,
            name=p.name,
            provider_type=p.provider_type,
            model=p.model,
            config=p.config,
            enabled=p.enabled,
            is_default=p.is_default,
            has_api_key=bool(p.api_key_encrypted),
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in providers
    ]


@router.post(
    "/ai/providers",
    response_model=AIProviderRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    tags=["ai"],
)
def create_ai_provider(payload: AIProviderCreate, session: SessionDep) -> AIProviderRead:
    """Create a new AI provider configuration."""
    # Validate provider type
    provider_types = cast(list[dict[str, Any]], SUPPORTED_PROVIDERS)
    valid_types = [p["type"] for p in provider_types]
    if payload.provider_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider_type. Must be one of: {valid_types}",
        )

    # If this is set as default, unset other defaults
    if payload.is_default:
        session.execute(select(AIProvider).where(AIProvider.is_default.is_(True)))
        for existing in session.scalars(select(AIProvider).where(AIProvider.is_default.is_(True))):
            existing.is_default = False

    provider = AIProvider(
        name=payload.name,
        provider_type=payload.provider_type,
        api_key_encrypted=encrypt_secret(payload.api_key),
        model=payload.model,
        config=payload.config,
        enabled=payload.enabled,
        is_default=payload.is_default,
    )
    session.add(provider)
    session.commit()
    session.refresh(provider)

    return AIProviderRead(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        model=provider.model,
        config=provider.config,
        enabled=provider.enabled,
        is_default=provider.is_default,
        has_api_key=bool(provider.api_key_encrypted),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@router.get(
    "/ai/providers/{provider_id}",
    response_model=AIProviderRead,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def get_ai_provider(provider_id: uuid.UUID, session: SessionDep) -> AIProviderRead:
    """Get a specific AI provider."""
    provider = session.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found")
    return AIProviderRead(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        model=provider.model,
        config=provider.config,
        enabled=provider.enabled,
        is_default=provider.is_default,
        has_api_key=bool(provider.api_key_encrypted),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@router.patch(
    "/ai/providers/{provider_id}",
    response_model=AIProviderRead,
    dependencies=[Depends(require_admin)],
    tags=["ai"],
)
def update_ai_provider(
    provider_id: uuid.UUID,
    payload: AIProviderUpdate,
    session: SessionDep,
) -> AIProviderRead:
    """Update an AI provider configuration."""
    provider = session.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found")

    if payload.name is not None:
        provider.name = payload.name
    if payload.provider_type is not None:
        provider.provider_type = payload.provider_type
    if payload.api_key is not None:
        provider.api_key_encrypted = encrypt_secret(payload.api_key)
    if payload.model is not None:
        provider.model = payload.model
    if payload.config is not None:
        provider.config = payload.config
    if payload.enabled is not None:
        provider.enabled = payload.enabled
    if payload.is_default is not None:
        if payload.is_default:
            # Unset other defaults
            for existing in session.scalars(
                select(AIProvider).where(
                    AIProvider.is_default.is_(True),
                    AIProvider.id != provider_id,
                )
            ):
                existing.is_default = False
        provider.is_default = payload.is_default

    session.commit()
    session.refresh(provider)

    return AIProviderRead(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        model=provider.model,
        config=provider.config,
        enabled=provider.enabled,
        is_default=provider.is_default,
        has_api_key=bool(provider.api_key_encrypted),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


@router.delete(
    "/ai/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    tags=["ai"],
)
def delete_ai_provider(provider_id: uuid.UUID, session: SessionDep) -> None:
    """Delete an AI provider."""
    provider = session.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found")
    session.delete(provider)
    session.commit()


@router.post(
    "/ai/providers/test",
    response_model=AIProviderTestResponse,
    dependencies=[Depends(require_operator)],
    tags=["ai"],
)
async def test_ai_provider_config(
    payload: AIProviderTestConfigRequest,
    session: SessionDep,
) -> AIProviderTestResponse:
    """Test a not-yet-saved AI provider config with the form's current values, so a user can
    verify the API key/model actually work before creating or updating the provider.

    Registered before ``/{provider_id}/test`` so ``provider_id="test"`` is not captured.
    """
    api_key = payload.api_key
    if not api_key and payload.provider_id is not None:
        provider = session.get(AIProvider, payload.provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="AI provider not found")
        api_key = _provider_api_key(provider.api_key_encrypted) or ""

    try:
        client = create_llm_client(
            payload.provider_type,
            api_key,
            payload.model,
            config=payload.config,
        )
        response = await client.complete(payload.prompt, max_tokens=100)
        return AIProviderTestResponse(
            success=True,
            response=response.content,
            model=response.model,
            tokens_used=response.input_tokens + response.output_tokens,
        )
    except Exception as e:
        return AIProviderTestResponse(
            success=False,
            error=str(e),
        )


@router.post(
    "/ai/providers/{provider_id}/test",
    response_model=AIProviderTestResponse,
    dependencies=[Depends(require_operator)],
    tags=["ai"],
)
async def test_ai_provider(
    provider_id: uuid.UUID,
    payload: AIProviderTestRequest,
    session: SessionDep,
) -> AIProviderTestResponse:
    """Test an AI provider connection."""
    provider = session.get(AIProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="AI provider not found")
    if not provider.api_key_encrypted:
        raise HTTPException(status_code=400, detail="No API key configured")

    try:
        client = create_llm_client(
            provider.provider_type,
            _provider_api_key(provider.api_key_encrypted) or "",
            provider.model,
            config=provider.config,
        )
        response = await client.complete(payload.prompt, max_tokens=100)
        return AIProviderTestResponse(
            success=True,
            response=response.content,
            model=response.model,
            tokens_used=response.input_tokens + response.output_tokens,
        )
    except Exception as e:
        return AIProviderTestResponse(
            success=False,
            error=str(e),
        )


@router.post(
    "/ai/chat",
    response_model=AIChatResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
async def ai_chat(payload: AIChatRequest, session: SessionDep) -> AIChatResponse:
    """Chat with the default AI provider."""
    response, observation = await complete_with_budget_and_observation(
        session,
        payload.prompt,
        invoke=LLMInvokeContext(feature="ai_chat", prompt_name="ai_chat"),
        context=payload.context if payload.context else None,
        max_tokens=800,
    )
    if response is None:
        if observation.status == "budget_denied_reject":
            raise HTTPException(
                status_code=429,
                detail="LLM budget policy rejected this request.",
            )
        raise HTTPException(
            status_code=503,
            detail=(
                "No LLM provider available (or budget denied). Configure provider or retry later."
            ),
        )
    return AIChatResponse(
        response=response.content,
        model=response.model,
        provider=response.provider,
        tokens_used=response.input_tokens + response.output_tokens,
    )


# ============ Slack Config endpoints ============


class SlackConfigCreate(BaseModel):
    name: str
    webhook_url: str  # will be encrypted before storage
    channel: str | None = None
    daily_report_enabled: bool = False
    daily_report_cron: str = "0 9 * * *"
    daily_report_use_ai: bool = True
    enabled: bool = True
    is_default: bool = False


class SlackConfigUpdate(BaseModel):
    name: str | None = None
    webhook_url: str | None = None  # only update if provided
    channel: str | None = None
    daily_report_enabled: bool | None = None
    daily_report_cron: str | None = None
    daily_report_use_ai: bool | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class SlackConfigRead(BaseModel):
    id: uuid.UUID
    name: str
    channel: str | None
    daily_report_enabled: bool
    daily_report_cron: str
    daily_report_use_ai: bool
    enabled: bool
    is_default: bool
    has_webhook: bool  # don't expose actual webhook URL
    created_at: datetime
    updated_at: datetime


@router.get(
    "/slack/configs",
    response_model=list[SlackConfigRead],
    dependencies=[Depends(require_viewer)],
    tags=["slack"],
)
def list_slack_configs(session: SessionDep) -> list[SlackConfigRead]:
    """List all Slack configurations."""
    configs = session.scalars(select(SlackConfig).order_by(SlackConfig.name))
    return [
        SlackConfigRead(
            id=c.id,
            name=c.name,
            channel=c.channel,
            daily_report_enabled=c.daily_report_enabled,
            daily_report_cron=c.daily_report_cron,
            daily_report_use_ai=c.daily_report_use_ai,
            enabled=c.enabled,
            is_default=c.is_default,
            has_webhook=bool(c.webhook_url_encrypted),
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in configs
    ]


@router.post(
    "/slack/configs",
    response_model=SlackConfigRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    tags=["slack"],
)
def create_slack_config(payload: SlackConfigCreate, session: SessionDep) -> SlackConfigRead:
    """Create a new Slack configuration."""
    try:
        daily_report_cron = validate_cron_field(payload.daily_report_cron, required=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # If this is set as default, unset other defaults
    if payload.is_default:
        for existing in session.scalars(
            select(SlackConfig).where(SlackConfig.is_default.is_(True))
        ):
            existing.is_default = False

    config = SlackConfig(
        name=payload.name,
        webhook_url_encrypted=encrypt_secret(payload.webhook_url),
        channel=payload.channel,
        daily_report_enabled=payload.daily_report_enabled,
        daily_report_cron=daily_report_cron,
        daily_report_use_ai=payload.daily_report_use_ai,
        enabled=payload.enabled,
        is_default=payload.is_default,
    )
    session.add(config)
    session.commit()
    session.refresh(config)

    return SlackConfigRead(
        id=config.id,
        name=config.name,
        channel=config.channel,
        daily_report_enabled=config.daily_report_enabled,
        daily_report_cron=config.daily_report_cron,
        daily_report_use_ai=config.daily_report_use_ai,
        enabled=config.enabled,
        is_default=config.is_default,
        has_webhook=bool(config.webhook_url_encrypted),
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.get(
    "/slack/configs/{config_id}",
    response_model=SlackConfigRead,
    dependencies=[Depends(require_viewer)],
    tags=["slack"],
)
def get_slack_config(config_id: uuid.UUID, session: SessionDep) -> SlackConfigRead:
    """Get a specific Slack configuration."""
    config = session.get(SlackConfig, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Slack config not found")
    return SlackConfigRead(
        id=config.id,
        name=config.name,
        channel=config.channel,
        daily_report_enabled=config.daily_report_enabled,
        daily_report_cron=config.daily_report_cron,
        daily_report_use_ai=config.daily_report_use_ai,
        enabled=config.enabled,
        is_default=config.is_default,
        has_webhook=bool(config.webhook_url_encrypted),
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.patch(
    "/slack/configs/{config_id}",
    response_model=SlackConfigRead,
    dependencies=[Depends(require_admin)],
    tags=["slack"],
)
def update_slack_config(
    config_id: uuid.UUID,
    payload: SlackConfigUpdate,
    session: SessionDep,
) -> SlackConfigRead:
    """Update a Slack configuration."""
    config = session.get(SlackConfig, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Slack config not found")

    if payload.name is not None:
        config.name = payload.name
    if payload.webhook_url is not None:
        config.webhook_url_encrypted = encrypt_secret(payload.webhook_url)
    if "channel" in payload.model_fields_set:
        config.channel = payload.channel or None
    if payload.daily_report_enabled is not None:
        config.daily_report_enabled = payload.daily_report_enabled
    if payload.daily_report_cron is not None:
        try:
            cron = validate_cron_field(
                payload.daily_report_cron,
                required=True,
            )
            if cron is not None:
                config.daily_report_cron = cron
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.daily_report_use_ai is not None:
        config.daily_report_use_ai = payload.daily_report_use_ai
    if payload.enabled is not None:
        config.enabled = payload.enabled
    if payload.is_default is not None:
        if payload.is_default:
            for existing in session.scalars(
                select(SlackConfig).where(
                    SlackConfig.is_default.is_(True),
                    SlackConfig.id != config_id,
                )
            ):
                existing.is_default = False
        config.is_default = payload.is_default

    session.commit()
    session.refresh(config)

    return SlackConfigRead(
        id=config.id,
        name=config.name,
        channel=config.channel,
        daily_report_enabled=config.daily_report_enabled,
        daily_report_cron=config.daily_report_cron,
        daily_report_use_ai=config.daily_report_use_ai,
        enabled=config.enabled,
        is_default=config.is_default,
        has_webhook=bool(config.webhook_url_encrypted),
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@router.delete(
    "/slack/configs/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    tags=["slack"],
)
def delete_slack_config(config_id: uuid.UUID, session: SessionDep) -> None:
    """Delete a Slack configuration."""
    config = session.get(SlackConfig, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Slack config not found")
    session.delete(config)
    session.commit()


@router.post(
    "/slack/configs/{config_id}/test",
    dependencies=[Depends(require_operator)],
    tags=["slack"],
)
async def test_slack_config(config_id: uuid.UUID, session: SessionDep) -> JsonDict:
    """Test a Slack configuration by sending a test message."""
    from cryo.reporting import SlackClient
    from cryo.reporting.slack import header_block, section_block

    config = session.get(SlackConfig, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Slack config not found")
    if not config.webhook_url_encrypted:
        raise HTTPException(status_code=400, detail="No webhook URL configured")

    client = SlackClient(_slack_webhook_url(config.webhook_url_encrypted) or "", config.channel)
    blocks = [
        header_block("🧊 Cryo Test Message"),
        section_block(
            "This is a test message from Cryo to verify your Slack integration "
            "is working correctly."
        ),
    ]

    success = await client.send_blocks(blocks, fallback_text="Cryo Test Message")

    if success:
        return {"status": "ok", "message": "Test message sent successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to send test message to Slack")


class SlackTestConfigRequest(BaseModel):
    """A not-yet-saved webhook URL, so the create/edit drawer can verify it before saving."""

    webhook_url: str = ""
    channel: str | None = None
    # Set when testing an *edit* where the webhook field was left blank ("keep existing webhook")
    # -- falls back to the real stored (encrypted) webhook instead of testing an empty string.
    config_id: uuid.UUID | None = None


@router.post(
    "/slack/test",
    dependencies=[Depends(require_operator)],
    tags=["slack"],
)
async def test_slack_config_values(
    payload: SlackTestConfigRequest, session: SessionDep
) -> JsonDict:
    """Test a not-yet-saved Slack webhook by sending a test message with the form's current
    values, so a user can verify it actually works before creating or updating the connection."""
    from cryo.reporting import SlackClient
    from cryo.reporting.slack import header_block, section_block

    webhook_url = payload.webhook_url
    if not webhook_url and payload.config_id is not None:
        config = session.get(SlackConfig, payload.config_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Slack config not found")
        webhook_url = _slack_webhook_url(config.webhook_url_encrypted) or ""

    client = SlackClient(webhook_url, payload.channel)
    blocks = [
        header_block("🧊 Cryo Test Message"),
        section_block(
            "This is a test message from Cryo to verify your Slack integration "
            "is working correctly."
        ),
    ]

    success = await client.send_blocks(blocks, fallback_text="Cryo Test Message")

    if success:
        return {"status": "ok", "message": "Test message sent successfully"}
    raise HTTPException(status_code=500, detail="Failed to send test message to Slack")


# ============ Reporting endpoints ============


class DailyReportResponse(BaseModel):
    report_date: str
    status: str = "CLEAR"
    summary: JsonDict
    grade_distribution: JsonDict
    maintenance_stats: JsonDict
    top_issues: list[JsonDict]
    critical_anomalies: list[JsonDict]
    action_items: list[JsonDict] = []
    cost: JsonDict
    bottom_line: str | None = None
    slack_sent: bool = False


@router.post(
    "/reporting/daily",
    response_model=DailyReportResponse,
    dependencies=[Depends(require_operator)],
    tags=["reporting"],
)
async def generate_report(
    session: SessionDep,
    send_to_slack: bool = False,
    use_ai: bool = True,
) -> DailyReportResponse:
    """Generate daily anomaly report, optionally send to Slack."""
    from cryo.reporting import SlackClient, generate_daily_report

    # Get the default Slack config from DB
    slack_config = session.scalar(
        select(SlackConfig).where(
            SlackConfig.is_default.is_(True),
            SlackConfig.enabled.is_(True),
        )
    )
    if slack_config is None:
        slack_config = session.scalar(
            select(SlackConfig).where(SlackConfig.enabled.is_(True)).limit(1)
        )

    slack_client = None
    if send_to_slack and slack_config and slack_config.webhook_url_encrypted:
        slack_client = SlackClient(
            _slack_webhook_url(slack_config.webhook_url_encrypted) or "",
            slack_config.channel,
        )

    result = await generate_daily_report(
        session,
        slack_client=slack_client,
        use_ai_summary=use_ai,
    )
    if result.get("slack_sent") and slack_config is not None:
        from cryo.reporting.scheduler import record_sent

        record_sent(session, f"daily_report:{slack_config.id}", datetime.now(UTC))

    return DailyReportResponse(**result)


# === Trino/S3/Iceberg Optimization Checklist ===


class ChecklistItemResponse(BaseModel):
    key: str
    label: str
    weight_pct: float
    tier: int
    status: str
    reason: str
    metric_value: float | None = None
    threshold: float | None = None


class TableChecklistResponse(BaseModel):
    table_id: uuid.UUID
    checked_at: datetime
    items: list[ChecklistItemResponse]
    summary: dict[str, Any]


def _latest_preset_idle_hours(session: Any, table_id: uuid.UUID) -> float | None:
    """Best-effort: pull partition_write_idle_hours from the most recent job's persisted plan
    for this table, since presets aren't directly bound to a table (they're applied at
    dispatch time) -- the plan a job actually ran with is the only durable record of what
    idle-hours gate was in effect. Returns None (checklist renders this as "yellow, verify")
    when no plan is on record yet, rather than guessing."""
    from cryo.db.models import MaintenanceJob

    job = session.scalar(
        select(MaintenanceJob)
        .where(MaintenanceJob.table_id == table_id, MaintenanceJob.plan.is_not(None))
        .order_by(MaintenanceJob.started_at.desc())
        .limit(1)
    )
    if job is None or not isinstance(job.plan, dict):
        return None
    # Real shape confirmed live: {"mode": ..., "notes": [...], "operations": [{"type": ...,
    # "params": {...}}, ...]} -- each operation carries its own params dict, not one shared
    # top-level params key. partition_write_idle_hours is planner-level, not operation-level,
    # but the planner threads the same params mapping into every operation it builds, so any
    # operation's params dict has it if it was set at all.
    for operation in job.plan.get("operations") or []:
        if not isinstance(operation, dict):
            continue
        params = operation.get("params")
        if not isinstance(params, dict):
            continue
        value = params.get("partition_write_idle_hours")
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _sample_file_paths(session: Any, table_id: uuid.UUID, limit: int = 5) -> list[str]:
    """Sample data-file paths for the storage_class checklist item.

    The scanner stores one live file path per partition (``raw.sample_file_path``). Oldest
    partitions are sampled first because cold data is what lifecycle rules move to
    restore-required classes. Returns [] when no scan has recorded paths yet, which the
    checklist reports as unknown rather than green."""
    from sqlalchemy import desc, select

    from cryo.db.models import TablePartition

    latest = session.scalar(
        select(TablePartition.scan_at)
        .where(TablePartition.table_id == table_id)
        .order_by(desc(TablePartition.scan_at))
        .limit(1)
    )
    if latest is None:
        return []
    rows = session.scalars(
        select(TablePartition.raw)
        .where(TablePartition.table_id == table_id, TablePartition.scan_at == latest)
        .limit(500)
    )
    found: list[tuple[str, str]] = []
    for raw in rows:
        path = (raw or {}).get("sample_file_path")
        if isinstance(path, str) and path:
            found.append((str((raw or {}).get("last_write_at") or ""), path))
    found.sort(key=lambda item: (item[0] == "", item[0]))
    return [path for _, path in found[:limit]]


@router.get(
    "/tables/{table_id}/checklist",
    response_model=TableChecklistResponse,
    dependencies=[Depends(require_viewer)],
    tags=["analytics"],
)
def get_table_checklist(table_id: uuid.UUID, session: SessionDep) -> TableChecklistResponse:
    """The 12-item Trino/S3/Iceberg optimization checklist for one table -- see
    cryo/scanner/checklist.py's module docstring for the full item inventory, weights, and the
    live incidents that grounded the three items (partition_match, cadence_match,
    storage_class) not already covered by health.py's existing deduction score."""
    from cryo.scanner.checklist import checklist_summary, compute_checklist

    items = compute_checklist(
        session,
        table_id,
        preset_idle_hours=_latest_preset_idle_hours(session, table_id),
        sample_file_paths=_sample_file_paths(session, table_id),
    )
    return TableChecklistResponse(
        table_id=table_id,
        checked_at=datetime.now(UTC),
        items=[
            ChecklistItemResponse(
                key=i.key,
                label=i.label,
                weight_pct=i.weight_pct,
                tier=i.tier,
                status=i.status,
                reason=i.reason,
                metric_value=i.metric_value,
                threshold=i.threshold,
            )
            for i in items
        ],
        summary=checklist_summary(items),
    )


class FleetChecklistRow(BaseModel):
    table_id: uuid.UUID
    schema_name: str
    table_name: str
    summary: dict[str, Any]
    red_items: list[str]


class FleetChecklistResponse(BaseModel):
    checked_at: datetime
    rows: list[FleetChecklistRow]


@router.get(
    "/checklist/fleet",
    response_model=FleetChecklistResponse,
    dependencies=[Depends(require_viewer)],
    tags=["analytics"],
)
def get_fleet_checklist(
    session: SessionDep,
    table_ids: str | None = None,
    limit: int = 20,
) -> FleetChecklistResponse:
    """Fleet-wide checklist rollup, scoped to an explicit table_id list (comma-separated) when
    given -- e.g. the top-20-by-cost cohort -- since computing all 12 items for the whole fleet
    on every request would be expensive (partition_match and storage_class each do real work
    per table). Without table_ids, falls back to the `limit` most recently scanned active
    tables so the endpoint is still useful standalone."""
    from cryo.db.models import Table, TableStats
    from cryo.scanner.checklist import checklist_summary, compute_checklist

    if table_ids:
        ids = [uuid.UUID(t.strip()) for t in table_ids.split(",") if t.strip()]
        tables = session.scalars(select(Table).where(Table.id.in_(ids))).all()
    else:
        tables = session.scalars(
            select(Table)
            .join(TableStats, TableStats.table_id == Table.id)
            .where(Table.is_active.is_(True))
            .order_by(TableStats.scan_at.desc())
            .limit(limit)
        ).all()

    rows: list[FleetChecklistRow] = []
    for table in tables:
        items = compute_checklist(
            session,
            table.id,
            preset_idle_hours=_latest_preset_idle_hours(session, table.id),
            sample_file_paths=_sample_file_paths(session, table.id),
        )
        rows.append(
            FleetChecklistRow(
                table_id=table.id,
                schema_name=table.schema_name,
                table_name=table.table_name,
                summary=checklist_summary(items),
                red_items=[i.key for i in items if i.status == "red"],
            )
        )
    return FleetChecklistResponse(checked_at=datetime.now(UTC), rows=rows)


# === Signal-Driven Triggers Endpoints ===


class SignalResultResponse(BaseModel):
    triggered: bool
    signal_name: str
    reason: str
    severity: str
    metric_value: float | None
    threshold: float | None


class TableSignalsResponse(BaseModel):
    table_id: uuid.UUID
    checked_at: datetime
    signals: list[SignalResultResponse]
    any_triggered: bool


@router.get(
    "/tables/{table_id}/signals",
    response_model=TableSignalsResponse,
    dependencies=[Depends(require_viewer)],
    tags=["analytics"],
)
def check_table_signals(
    table_id: uuid.UUID,
    session: SessionDep,
) -> TableSignalsResponse:
    """Check all maintenance trigger signals for a table."""
    from cryo.triggers.signals import any_signal_triggered, check_all_signals

    results = check_all_signals(session, table_id)

    return TableSignalsResponse(
        table_id=table_id,
        checked_at=datetime.now(UTC),
        signals=[
            SignalResultResponse(
                triggered=r.triggered,
                signal_name=r.signal_name,
                reason=r.reason,
                severity=r.severity,
                metric_value=r.metric_value,
                threshold=r.threshold,
            )
            for r in results
        ],
        any_triggered=any_signal_triggered(results),
    )
