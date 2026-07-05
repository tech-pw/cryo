from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

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
from cryo.api.deps import SessionDep, require_admin, require_operator, require_viewer
from cryo.api.preview_cost import savings_projection_from_stats, savings_projection_to_dict
from cryo.api.schemas import JsonDict
from cryo.api.serializers import table_to_read
from cryo.api.validators import validate_cron_field
from cryo.compliance.governance import track_pending_deletions
from cryo.config import get_settings
from cryo.db.models import (
    AIProvider,
    AuditLog,
    LayoutRecommendation,
    ProtectedSnapshot,
    SlackConfig,
    Source,
    Table,
    TableStats,
    TableStatsHistory,
)
from cryo.mcp.server import CryoMcpServer, build_default_mcp_server
from cryo.security.crypto import decrypt_secret, encrypt_secret
from cryo.sources.latency import get_catalog_latency_monitor

router = APIRouter(prefix="/api/v1")


def _provider_api_key(stored: str | None) -> str | None:
    return decrypt_secret(stored)


def _slack_webhook_url(stored: str | None) -> str | None:
    return decrypt_secret(stored)


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
    # Get the default AI provider
    provider = session.scalar(
        select(AIProvider).where(
            AIProvider.is_default.is_(True),
            AIProvider.enabled.is_(True),
        )
    )
    if provider is None:
        provider = session.scalar(select(AIProvider).where(AIProvider.enabled.is_(True)).limit(1))
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail="No AI provider configured. Add one in Connections > AI Providers.",
        )
    if not provider.api_key_encrypted:
        raise HTTPException(status_code=400, detail="AI provider has no API key configured")

    # Gather context about the datalake
    context = None
    if payload.include_context:
        context = _gather_copilot_context(session)

    # Call the LLM
    client = create_llm_client(
        provider.provider_type,
        _provider_api_key(provider.api_key_encrypted) or "",
        provider.model,
        config=provider.config,
    )
    response = await client.complete(payload.question, context=context)

    return NaturalLanguageResponse(
        answer=response.content,
        context_used=context,
        model=response.model,
        provider=response.provider,
    )


def _gather_copilot_context(session: Session) -> dict[str, Any]:
    """Gather datalake context for LLM."""
    # Get summary stats
    tables_count = session.execute(select(Table.id).where(Table.is_active.is_(True))).fetchall()

    # Get unhealthy tables
    unhealthy = session.execute(
        select(Table.schema_name, Table.table_name, TableStats.grade, TableStats.overall_score)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(TableStats.overall_score < 70)
        .order_by(TableStats.overall_score.asc())
        .limit(5)
    ).fetchall()

    # Get compaction backlog
    compaction = session.execute(
        select(Table.schema_name, Table.table_name, TableStats.small_files_count)
        .join(TableStats, TableStats.table_id == Table.id)
        .where(TableStats.needs_compaction.is_(True))
        .order_by(TableStats.small_files_count.desc())
        .limit(5)
    ).fetchall()

    return {
        "total_tables": len(tables_count),
        "unhealthy_tables": [
            {"table": f"{r[0]}.{r[1]}", "grade": r[2], "score": r[3]} for r in unhealthy
        ],
        "compaction_backlog": [
            {"table": f"{r[0]}.{r[1]}", "small_files": r[2]} for r in compaction
        ],
    }


@router.get(
    "/mcp/tools",
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
def mcp_list_tools(session: SessionDep) -> list[dict[str, str]]:
    return _mcp_server(session).list_tools()


@router.post(
    "/mcp/tools/call",
    response_model=McpToolCallResponse,
    dependencies=[Depends(require_operator)],
    tags=["ai"],
)
def mcp_call_tool(payload: McpToolCallRequest, session: SessionDep) -> McpToolCallResponse:
    server = _mcp_server(session)
    try:
        result = server.call(payload.name, payload.arguments)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return McpToolCallResponse(result=result)


@router.get(
    "/tables/{table_id}/compliance",
    response_model=ComplianceStatusResponse,
    dependencies=[Depends(require_viewer)],
    tags=["compliance"],
)
def get_compliance_status(table_id: uuid.UUID, session: SessionDep) -> ComplianceStatusResponse:
    from cryo.security.abac import ABACPolicy

    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
    stats_dict = _table_stats_dict(table, stats)
    pending = track_pending_deletions(
        f"iceberg.{table.schema_name}.{table.table_name}",
        stats_dict,
    )
    abac_count = len(
        session.scalars(select(ABACPolicy).where(ABACPolicy.table_id == table_id)).all()
    )
    abac_note = (
        "ABAC maintenance enforcement active; scan-time row/column filters require catalog proxy."
        if abac_count
        else None
    )
    return ComplianceStatusResponse(
        table_id=table_id,
        table_ref=f"iceberg.{table.schema_name}.{table.table_name}",
        pending_deletion=(
            {
                "pending_rows": pending.pending_rows,
                "blocked_until": pending.blocked_until.isoformat()
                if pending.blocked_until
                else None,
                "reason": pending.reason,
            }
            if pending
            else None
        ),
        abac_policy_count=abac_count,
        abac_enforcement_note=abac_note,
    )


@router.post(
    "/tables/{table_id}/protected-snapshots",
    response_model=ProtectSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_operator)],
    tags=["compliance"],
)
def protect_snapshot(
    table_id: uuid.UUID,
    payload: ProtectSnapshotRequest,
    session: SessionDep,
) -> ProtectSnapshotResponse:
    if session.get(Table, table_id) is None:
        raise HTTPException(status_code=404, detail="Table not found")
    row = ProtectedSnapshot(
        table_id=table_id,
        snapshot_id=payload.snapshot_id,
        label=payload.label,
    )
    session.add(row)
    session.commit()
    return ProtectSnapshotResponse(
        table_id=table_id,
        snapshot_id=payload.snapshot_id,
        label=payload.label,
    )


@router.get(
    "/sources/{source_id}/catalog-latency",
    response_model=CatalogLatencyResponse,
    dependencies=[Depends(require_viewer)],
    tags=["compliance"],
)
def get_catalog_latency(source_id: uuid.UUID, session: SessionDep) -> CatalogLatencyResponse:
    source = session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    monitor = get_catalog_latency_monitor(str(source_id))
    return CatalogLatencyResponse(
        source_id=source_id,
        source_name=source.name,
        operations=monitor.all_summaries(),
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
    "/ai/chat",
    response_model=AIChatResponse,
    dependencies=[Depends(require_viewer)],
    tags=["ai"],
)
async def ai_chat(payload: AIChatRequest, session: SessionDep) -> AIChatResponse:
    """Chat with the default AI provider."""
    # Get the default provider
    provider = session.scalar(
        select(AIProvider).where(
            AIProvider.is_default.is_(True),
            AIProvider.enabled.is_(True),
        )
    )
    if provider is None:
        # Fall back to any enabled provider
        provider = session.scalar(select(AIProvider).where(AIProvider.enabled.is_(True)).limit(1))
    if provider is None:
        raise HTTPException(
            status_code=400,
            detail="No AI provider configured. Add one in Connections > AI Providers.",
        )
    if not provider.api_key_encrypted:
        raise HTTPException(status_code=400, detail="AI provider has no API key configured")

    client = create_llm_client(
        provider.provider_type,
        _provider_api_key(provider.api_key_encrypted) or "",
        provider.model,
        config=provider.config,
    )
    response = await client.complete(
        payload.prompt,
        context=payload.context if payload.context else None,
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


def _mcp_server(session: Session) -> CryoMcpServer:
    settings = get_settings()

    def get_table_health(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        table = session.scalar(
            select(Table)
            .where(Table.id == table_id)
            .options(selectinload(Table.stats), selectinload(Table.cost))
        )
        if table is None:
            raise KeyError("table not found")
        return table_to_read(table).model_dump(mode="json")

    def list_stale_tables(args: dict[str, Any]) -> dict[str, Any]:
        cooldown_hours = int(args.get("cooldown_hours") or 168)
        cutoff = datetime.now(UTC) - timedelta(hours=cooldown_hours)
        tables = []
        for table in session.scalars(select(Table).where(Table.is_active.is_(True)).limit(50)):
            last = session.scalar(
                select(AuditLog.ts)
                .where(AuditLog.table_id == table.id)
                .order_by(desc(AuditLog.ts))
                .limit(1)
            )
            if last is None or last < cutoff:
                tables.append(
                    {
                        "table_id": str(table.id),
                        "schema_name": table.schema_name,
                        "table_name": table.table_name,
                        "last_maintained_at": last.isoformat() if last else None,
                    }
                )
        limit = int(args.get("limit") or 20)
        return {"tables": tables[:limit]}

    def list_compaction_backlog(args: dict[str, Any]) -> dict[str, Any]:
        limit = int(args.get("limit") or 20)
        rows = session.execute(
            select(
                Table.schema_name, Table.table_name, TableStats.small_files_count, TableStats.grade
            )
            .join(TableStats, TableStats.table_id == Table.id)
            .where(TableStats.needs_compaction.is_(True))
            .order_by(TableStats.small_files_count.desc())
            .limit(limit)
        )
        return {
            "tables": [
                {
                    "schema_name": schema,
                    "table_name": name,
                    "small_files_count": small_files,
                    "grade": grade,
                }
                for schema, name, small_files, grade in rows
            ]
        }

    def explain_tool(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        before = _stats_snapshot(session, table_id, None, offset=-2)
        after = _stats_snapshot(session, table_id, None, offset=-1)
        if before is None or after is None:
            raise KeyError("insufficient history")
        return {
            "explanation": explain_health_change(
                before,
                after,
                preset_name=args.get("preset_name"),
            )
        }

    def simulate_preset(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        table = session.get(Table, table_id)
        stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
        if table is None or stats is None:
            raise KeyError("table not found")
        stats_dict = _table_stats_dict(table, stats)
        projection = savings_projection_from_stats(stats_dict, settings)
        return savings_projection_to_dict(projection)

    def get_table_schema(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        stats = session.scalar(select(TableStats).where(TableStats.table_id == table_id))
        if stats is None:
            raise KeyError("table stats not found")
        raw = dict(stats.raw or {})
        return {
            "table_id": str(table_id),
            "partition_fields": raw.get("partition_fields") or [],
            "column_quality": raw.get("column_quality") or [],
            "format_version": raw.get("format_version"),
        }

    def get_snapshot_history(args: dict[str, Any]) -> dict[str, Any]:
        table_id = uuid.UUID(str(args["table_id"]))
        limit = int(args.get("limit") or 30)
        rows = session.scalars(
            select(TableStatsHistory)
            .where(TableStatsHistory.table_id == table_id)
            .order_by(TableStatsHistory.scan_at.desc())
            .limit(limit)
        ).all()
        history = []
        for row in reversed(rows):
            raw = dict(row.raw or {})
            history.append(
                {
                    "scan_at": row.scan_at.isoformat(),
                    "total_snapshots": raw.get("total_snapshots"),
                    "grade": row.grade,
                    "overall_score": row.overall_score,
                }
            )
        return {"table_id": str(table_id), "history": history}

    def run_preset(args: dict[str, Any]) -> dict[str, Any]:
        from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Preset
        from cryo.domain.enums import Mode
        from cryo.infra.wiring import build_runtime_wiring

        if not args.get("confirm"):
            raise ValueError("run_preset requires confirm=true")
        table_id = uuid.UUID(str(args["table_id"]))
        destination_id = uuid.UUID(str(args["destination_id"]))
        preset_id = uuid.UUID(str(args["preset_id"])) if args.get("preset_id") else None

        if session.get(Destination, destination_id) is None:
            raise KeyError("destination not found")
        if preset_id and session.get(Preset, preset_id) is None:
            raise KeyError("preset not found")
        table = session.get(Table, table_id)
        if table is None:
            raise KeyError("table not found")

        mode = Mode(str(args.get("mode") or "standard"))
        run = MaintenanceRun(
            kind="adhoc",
            preset_id=preset_id,
            trigger="mcp",
            status="queued",
        )
        session.add(run)
        session.flush()
        job = MaintenanceJob(
            run_id=run.id,
            table_id=table_id,
            destination_id=destination_id,
            mode=mode.value,
            status="queued",
        )
        session.add(job)
        session.commit()
        wiring = build_runtime_wiring()
        wiring.queue.enqueue(str(job.id), str(destination_id))
        return {
            "run_id": str(run.id),
            "job_ids": [str(job.id)],
            "status": run.status,
        }

    return build_default_mcp_server(
        get_table_health=get_table_health,
        list_stale_tables=list_stale_tables,
        list_compaction_backlog=list_compaction_backlog,
        explain_health_change=explain_tool,
        simulate_preset=simulate_preset,
        get_table_schema=get_table_schema,
        get_snapshot_history=get_snapshot_history,
        run_preset=run_preset,
    )


def _stats_snapshot(
    session: Session,
    table_id: uuid.UUID,
    scan_at: datetime | None,
    *,
    offset: int,
) -> dict[str, Any] | None:
    query = select(TableStatsHistory).where(TableStatsHistory.table_id == table_id)
    if scan_at is not None:
        query = query.where(TableStatsHistory.scan_at == scan_at)
    rows = list(session.scalars(query.order_by(TableStatsHistory.scan_at)))
    if not rows:
        return None
    row = rows[offset] if scan_at is None else rows[0]
    raw = dict(row.raw or {})
    return {
        "grade": row.grade,
        "overall_score": row.overall_score,
        "small_files_count": row.small_files_count,
        "delete_file_pct": raw.get("delete_file_pct"),
        "manifest_count": raw.get("manifest_count"),
        "metadata_to_data_ratio": raw.get("metadata_to_data_ratio"),
        "total_snapshots": raw.get("total_snapshots"),
    }


def _table_stats_dict(table: Table, stats: TableStats | None) -> dict[str, Any]:
    if stats is None:
        return {"schema_name": table.schema_name, "table_name": table.table_name}
    raw = dict(stats.raw or {})
    return {
        "schema_name": table.schema_name,
        "table_name": table.table_name,
        **raw,
        "pending_soft_delete_rows": raw.get("pending_soft_delete_rows"),
        "delete_file_pct": stats.delete_file_pct,
        "small_files_count": stats.small_files_count,
        "target_file_size_bytes": stats.target_file_size_bytes,
        "projected_savings_usd_month": stats.projected_savings_usd_month,
    }


# ============================================================================
# Query Telemetry & Layout Optimization
# ============================================================================


class QueryPatternRead(BaseModel):
    column_name: str
    filter_count: int
    equality_filter_count: int
    range_filter_count: int
    join_count: int
    projection_count: int
    avg_selectivity: float | None = None


class TableQueryPatternsResponse(BaseModel):
    table_id: uuid.UUID
    schema_name: str
    table_name: str
    total_queries: int
    patterns: list[QueryPatternRead]
    dominant_filter_column: str | None = None
    multi_column_filter_ratio: float = 0.0


class LayoutRecommendationRead(BaseModel):
    id: uuid.UUID
    table_id: uuid.UUID
    current_sort_order: list[JsonDict] | None = None
    recommended_strategy: str
    recommended_columns: list[JsonDict]
    estimated_prune_improvement: float | None = None
    current_prune_efficiency: float | None = None
    analysis_query_count: int
    confidence: float
    status: str
    applied_at: datetime | None = None
    detail: JsonDict | None = None
    created_at: datetime
    updated_at: datetime


class ClusteringPlanRequest(BaseModel):
    strategy: Literal["sort", "zorder", "binpack"] = "sort"
    columns: list[str] | None = None
    partition_filter: str | None = None
    target_file_size_mb: int = 256
    rewrite_all: bool = False


class ClusteringPlanResponse(BaseModel):
    table_id: uuid.UUID
    table_ref: str
    strategy: str
    columns: list[str]
    spark_sql: str
    spark_python: str
    trino_sql: str | None = None


class TelemetryIngestRequest(BaseModel):
    source_id: uuid.UUID
    since_hours: int = 24
    limit: int = 1000


class TelemetryIngestResponse(BaseModel):
    queries_ingested: int
    patterns_updated: int


@router.get(
    "/tables/{table_id}/query-patterns",
    response_model=TableQueryPatternsResponse,
    dependencies=[Depends(require_viewer)],
    tags=["optimization"],
)
def get_table_query_patterns_api(
    table_id: uuid.UUID, session: SessionDep
) -> TableQueryPatternsResponse:
    """Get aggregated query patterns for a table."""
    from cryo.telemetry.patterns import get_table_query_patterns

    try:
        patterns = get_table_query_patterns(session, table_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return TableQueryPatternsResponse(
        table_id=table_id,
        schema_name=patterns.schema_name,
        table_name=patterns.table_name,
        total_queries=patterns.total_queries,
        patterns=[
            QueryPatternRead(
                column_name=p.column_name,
                filter_count=p.filter_count,
                equality_filter_count=p.equality_filter_count,
                range_filter_count=p.range_filter_count,
                join_count=p.join_count,
                projection_count=p.projection_count,
                avg_selectivity=p.avg_selectivity,
            )
            for p in patterns.columns
        ],
        dominant_filter_column=patterns.dominant_filter_column,
        multi_column_filter_ratio=patterns.multi_column_filter_ratio,
    )


@router.get(
    "/tables/{table_id}/layout-recommendation",
    response_model=LayoutRecommendationRead,
    dependencies=[Depends(require_viewer)],
    tags=["optimization"],
)
def get_layout_recommendation(
    table_id: uuid.UUID,
    session: SessionDep,
    refresh: bool = False,
) -> LayoutRecommendationRead:
    """Get layout optimization recommendation for a table."""
    from cryo.optimizer.layout_analyzer import generate_layout_recommendation

    recommendation = generate_layout_recommendation(session, table_id, force_refresh=refresh)

    return LayoutRecommendationRead(
        id=recommendation.id,
        table_id=recommendation.table_id,
        current_sort_order=recommendation.current_sort_order,
        recommended_strategy=recommendation.recommended_strategy,
        recommended_columns=recommendation.recommended_columns,
        estimated_prune_improvement=recommendation.estimated_prune_improvement,
        current_prune_efficiency=recommendation.current_prune_efficiency,
        analysis_query_count=recommendation.analysis_query_count,
        confidence=recommendation.confidence,
        status=recommendation.status,
        applied_at=recommendation.applied_at,
        detail=recommendation.detail,
        created_at=recommendation.created_at,
        updated_at=recommendation.updated_at,
    )


@router.post(
    "/tables/{table_id}/clustering-plan",
    response_model=ClusteringPlanResponse,
    dependencies=[Depends(require_operator)],
    tags=["optimization"],
)
def create_clustering_plan(
    table_id: uuid.UUID,
    payload: ClusteringPlanRequest,
    session: SessionDep,
) -> ClusteringPlanResponse:
    """Generate a clustering plan (sort/z-order) for a table."""
    from cryo.optimizer.clustering import build_clustering_plan

    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")

    source = session.get(Source, table.source_id)
    catalog_name = (source.config or {}).get("catalog_name", source.name) if source else "iceberg"
    table_ref = f"{catalog_name}.{table.schema_name}.{table.table_name}"

    # Get columns from recommendation if not specified
    columns = payload.columns
    if not columns and payload.strategy != "binpack":
        recommendation = session.scalar(
            select(LayoutRecommendation).where(LayoutRecommendation.table_id == table_id)
        )
        if recommendation and recommendation.recommended_columns:
            columns = [
                c.get("column", "") for c in recommendation.recommended_columns if c.get("column")
            ]

    if not columns and payload.strategy != "binpack":
        raise HTTPException(
            status_code=400,
            detail="No columns specified and no recommendation available. "
            "Either specify columns or generate a layout recommendation first.",
        )

    plan = build_clustering_plan(
        table_ref=table_ref,
        strategy=payload.strategy,
        columns=columns,
        target_file_size_bytes=payload.target_file_size_mb * 1024 * 1024,
        partition_filter=payload.partition_filter,
        rewrite_all=payload.rewrite_all,
    )

    return ClusteringPlanResponse(
        table_id=table_id,
        table_ref=table_ref,
        strategy=payload.strategy,
        columns=columns or [],
        spark_sql=plan.to_spark_rewrite_call(),
        spark_python=plan.to_spark_python(),
        trino_sql=plan.to_trino_optimize(),
    )


@router.post(
    "/telemetry/ingest",
    response_model=TelemetryIngestResponse,
    dependencies=[Depends(require_operator)],
    tags=["optimization"],
)
async def ingest_query_telemetry(
    payload: TelemetryIngestRequest,
    session: SessionDep,
) -> TelemetryIngestResponse:
    """Ingest query logs from Trino and aggregate patterns."""
    from cryo.telemetry.ingestion import TrinoQueryIngester, ingest_trino_query_log
    from cryo.telemetry.patterns import aggregate_query_patterns

    source = session.get(Source, payload.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    # Get Trino connection info from source config or a linked destination
    # For now, we'll look for a Trino destination
    from cryo.db.models import Destination

    trino_dest = session.scalar(
        select(Destination)
        .where(Destination.engine == "trino", Destination.enabled.is_(True))
        .limit(1)
    )
    if trino_dest is None:
        raise HTTPException(
            status_code=400,
            detail="No Trino destination configured. Add a Trino engine in Connections first.",
        )

    trino_url = (trino_dest.config or {}).get("url", "http://localhost:8080")

    ingester = TrinoQueryIngester(trino_url=trino_url)
    since = datetime.now(UTC) - timedelta(hours=payload.since_hours)

    queries_ingested = await ingest_trino_query_log(
        session=session,
        source=source,
        ingester=ingester,
        since=since,
        limit=payload.limit,
    )

    patterns_updated = aggregate_query_patterns(session, since=since)

    return TelemetryIngestResponse(
        queries_ingested=queries_ingested,
        patterns_updated=patterns_updated,
    )


@router.get(
    "/optimization/recommendations",
    response_model=list[LayoutRecommendationRead],
    dependencies=[Depends(require_viewer)],
    tags=["optimization"],
)
def list_layout_recommendations(
    session: SessionDep,
    status: str | None = None,
    min_confidence: float = 0.0,
    limit: int = 50,
) -> list[LayoutRecommendationRead]:
    """List layout recommendations across all tables."""
    query = select(LayoutRecommendation).where(LayoutRecommendation.confidence >= min_confidence)
    if status:
        query = query.where(LayoutRecommendation.status == status)
    query = query.order_by(
        LayoutRecommendation.estimated_prune_improvement.desc().nulls_last()
    ).limit(limit)

    recommendations = session.scalars(query).all()

    return [
        LayoutRecommendationRead(
            id=r.id,
            table_id=r.table_id,
            current_sort_order=r.current_sort_order,
            recommended_strategy=r.recommended_strategy,
            recommended_columns=r.recommended_columns,
            estimated_prune_improvement=r.estimated_prune_improvement,
            current_prune_efficiency=r.current_prune_efficiency,
            analysis_query_count=r.analysis_query_count,
            confidence=r.confidence,
            status=r.status,
            applied_at=r.applied_at,
            detail=r.detail,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in recommendations
    ]


@router.patch(
    "/optimization/recommendations/{rec_id}",
    response_model=LayoutRecommendationRead,
    dependencies=[Depends(require_operator)],
    tags=["optimization"],
)
def update_recommendation_status(
    rec_id: uuid.UUID,
    session: SessionDep,
    status: str,
) -> LayoutRecommendationRead:
    """Update a recommendation's status (e.g., mark as applied or rejected)."""
    rec = session.get(LayoutRecommendation, rec_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    valid_statuses = {"pending", "applied", "rejected", "scheduled"}
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Status must be one of: {valid_statuses}")

    rec.status = status
    if status == "applied":
        rec.applied_at = datetime.now(UTC)

    session.commit()
    session.refresh(rec)

    return LayoutRecommendationRead(
        id=rec.id,
        table_id=rec.table_id,
        current_sort_order=rec.current_sort_order,
        recommended_strategy=rec.recommended_strategy,
        recommended_columns=rec.recommended_columns,
        estimated_prune_improvement=rec.estimated_prune_improvement,
        current_prune_efficiency=rec.current_prune_efficiency,
        analysis_query_count=rec.analysis_query_count,
        confidence=rec.confidence,
        status=rec.status,
        applied_at=rec.applied_at,
        detail=rec.detail,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )


# === Benefit Scores & Effectiveness Ratio Endpoints ===


class BenefitScoreResponse(BaseModel):
    table_id: uuid.UUID
    partitions_scored: int
    top_candidates: list[JsonDict]


class EffectivenessStatsResponse(BaseModel):
    table_id: uuid.UUID
    total_input_files: int
    total_output_files: int
    run_count: int
    effectiveness_ratio: float
    effectiveness: str
    avg_duration_ms: int
    recommendation: str


@router.get(
    "/tables/{table_id}/benefit-scores",
    response_model=BenefitScoreResponse,
    dependencies=[Depends(require_viewer)],
    tags=["analytics"],
)
def get_table_benefit_scores(
    table_id: uuid.UUID,
    session: SessionDep,
    top_n: int = 10,
) -> BenefitScoreResponse:
    """Get AutoComp benefit scores for table partitions."""
    from cryo.planner.benefit_score import get_top_compaction_candidates

    candidates = get_top_compaction_candidates(session, table_id, min_score=0.1, limit=top_n)

    return BenefitScoreResponse(
        table_id=table_id,
        partitions_scored=len(candidates),
        top_candidates=[
            {
                "partition_value": p.partition_value,
                "small_files": p.small_files_in_partition_count,
                "size_gb": p.partition_size_gb,
                "query_frequency": p.query_frequency,
                "benefit_score": p.compaction_benefit_score,
            }
            for p in candidates
        ],
    )


@router.post(
    "/tables/{table_id}/benefit-scores/update",
    response_model=BenefitScoreResponse,
    dependencies=[Depends(require_operator)],
    tags=["analytics"],
)
def update_table_benefit_scores(
    table_id: uuid.UUID,
    session: SessionDep,
) -> BenefitScoreResponse:
    """Trigger immediate benefit score update for a table."""
    from cryo.planner.benefit_score import (
        get_top_compaction_candidates,
        update_partition_benefit_scores,
    )

    updated = update_partition_benefit_scores(session, table_id)
    candidates = get_top_compaction_candidates(session, table_id, min_score=0.1, limit=10)

    return BenefitScoreResponse(
        table_id=table_id,
        partitions_scored=updated,
        top_candidates=[
            {
                "partition_value": p.partition_value,
                "small_files": p.small_files_in_partition_count,
                "size_gb": p.partition_size_gb,
                "query_frequency": p.query_frequency,
                "benefit_score": p.compaction_benefit_score,
            }
            for p in candidates
        ],
    )


@router.get(
    "/tables/{table_id}/effectiveness",
    response_model=EffectivenessStatsResponse,
    dependencies=[Depends(require_viewer)],
    tags=["analytics"],
)
def get_effectiveness_stats(
    table_id: uuid.UUID,
    session: SessionDep,
    lookback_days: int = 30,
) -> EffectivenessStatsResponse:
    """Get compaction effectiveness ratio for a table."""
    from cryo.planner.benefit_score import get_compaction_effectiveness_stats

    stats = get_compaction_effectiveness_stats(session, table_id, lookback_days)

    return EffectivenessStatsResponse(
        table_id=table_id,
        total_input_files=stats["total_input_files"],
        total_output_files=stats["total_output_files"],
        run_count=stats["run_count"],
        effectiveness_ratio=stats["effectiveness_ratio"],
        effectiveness=stats["effectiveness"],
        avg_duration_ms=stats["avg_duration_ms"],
        recommendation=stats["recommendation"],
    )


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
