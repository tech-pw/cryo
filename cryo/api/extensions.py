from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, status
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
from cryo.api.deps import SessionDep, require_admin, require_operator, require_viewer
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
    AuditLog,
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


@router.get(
    "/ai/cost-model/evaluate",
    response_model=CostModelEvaluateResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai", "cost"],
)
def evaluate_learned_cost_model(session: SessionDep) -> CostModelEvaluateResponse:
    from cryo.config import get_settings
    from cryo.ml.cost_integration import load_cached_cost_model, refresh_cost_model_cache
    from cryo.ml.cost_model import evaluate_model, train_cost_model

    rows = session.execute(
        select(AuditLog, TableStats)
        .join(TableStats, TableStats.table_id == AuditLog.table_id)
        .where(AuditLog.actual_bytes_freed.isnot(None))
        .limit(500)
    )
    training: list[dict[str, Any]] = []
    for audit, stats in rows:
        training.append(
            {
                "action": audit.action,
                "actual_bytes_freed": audit.actual_bytes_freed,
                "predicted_bytes_freed": audit.actual_bytes_freed,
                "small_files_count": stats.small_files_count,
                "total_size_gb": stats.total_size_gb,
                "delete_file_pct": stats.delete_file_pct,
                "input_files_count": audit.input_files_count,
            }
        )
    settings = get_settings()
    refresh_cost_model_cache(session, settings)
    model = load_cached_cost_model()
    if model is None:
        model = train_cost_model(training, price_per_gb_month=settings.cost_price_per_gb_month)
    if model is None:
        return CostModelEvaluateResponse(
            mae_learned=0.0,
            mae_heuristic=0.0,
            beats_heuristic=False,
            band_coverage=0.0,
            n_samples=len(training),
        )
    holdout = training[-max(1, len(training) // 4) :]
    metrics = evaluate_model(holdout, model, price_per_gb_month=settings.cost_price_per_gb_month)
    return CostModelEvaluateResponse(**metrics)


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


class AutoCompCompareResponse(BaseModel):
    table_id: uuid.UUID
    static_order: list[str]
    autocomp_order: list[str]
    improvement_ratio: float
    autocomp_wins: bool


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
    verify the API key/model actually work before creating or updating the provider."""
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
    if payload.channel is not None:
        config.channel = payload.channel
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
    summary: JsonDict
    grade_distribution: JsonDict
    maintenance_stats: JsonDict
    top_issues: list[JsonDict]
    critical_anomalies: list[JsonDict]
    cost: JsonDict
    ai_summary: str | None = None
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

    return DailyReportResponse(**result)


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


