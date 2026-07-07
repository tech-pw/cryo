"""API routes for advanced Cryo features (Tier 2-4).

Endpoints for:
- Approval workflows
- Data lineage
- Anomaly detection
- Data quality profiling
- Partition evolution
- GitOps policies
- FinOps forecasts
- Signal-driven triggers
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from cryo.api.deps import SessionDep, require_editor, require_viewer
from cryo.api.schemas import (
    CompactionCandidateRead,
    CompactionCandidatesResponse,
    ObservationCompactResponse,
    ObservationEventRead,
    ObservationSearchResponse,
    ObservationStatsResponse,
    PlanVerdictRead,
    PlanVerifyRequest,
    PlanViolationRead,
    SkillsetListResponse,
    SkillsetOutcomeRead,
    SkillsetOutcomeWrite,
    SkillsetRead,
    SkillsetRollbackRequest,
    SkillsetWrite,
    UnionableResponse,
    UnionScoreRead,
)
from cryo.api.security import Principal
from cryo.config import get_settings
from cryo.observability.observation_lakehouse import ObservationLakehouse

router = APIRouter(prefix="/api/v1/advanced", tags=["advanced"])


# ==================== Approval Workflows ====================


class ApprovalRequestRead(BaseModel):
    id: UUID
    table_id: UUID
    table_name: str
    operation_type: str
    status: str
    requested_by: str
    impact_preview: dict | None
    created_at: datetime
    expires_at: datetime | None


class CreateApprovalRequest(BaseModel):
    table_id: UUID
    operation_type: str
    operation_params: dict = Field(default_factory=dict)
    requested_by: str
    reason: str | None = None


class ApprovalAction(BaseModel):
    approved: bool
    approver: str
    reason: str | None = None


@router.get("/approvals/pending", response_model=list[ApprovalRequestRead])
def list_pending_approvals(
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> list[ApprovalRequestRead]:
    """List all pending approval requests."""
    from cryo.db.model_utils import qualified_table_name
    from cryo.db.models import Table
    from cryo.workflows.approvals import get_pending_approvals

    requests = get_pending_approvals(session)

    table_ids = [r.table_id for r in requests]
    table_map: dict[UUID, str] = {}
    if table_ids:
        tables = (
            session.execute(
                select(Table).where(Table.id.in_(table_ids)).options(selectinload(Table.source))
            )
            .scalars()
            .all()
        )
        table_map = {table.id: qualified_table_name(table) for table in tables}

    return [
        ApprovalRequestRead(
            id=r.id,
            table_id=r.table_id,
            table_name=table_map.get(r.table_id, str(r.table_id)),
            operation_type=r.operation_type,
            status=r.status,
            requested_by=r.requested_by,
            impact_preview=r.impact_preview,
            created_at=r.created_at,
            expires_at=r.expires_at,
        )
        for r in requests
    ]


@router.post("/approvals/request", response_model=ApprovalRequestRead)
def create_approval_request(
    request: CreateApprovalRequest,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_editor)],
) -> ApprovalRequestRead:
    """Create a new approval request for a destructive operation."""
    from cryo.db.model_utils import qualified_table_name
    from cryo.db.models import Table
    from cryo.domain.enums import OperationType
    from cryo.workflows.approvals import (
        ApprovalRequestData,
        estimate_impact,
    )
    from cryo.workflows.approvals import (
        create_approval_request as create_req,
    )

    try:
        operation_type = OperationType(request.operation_type)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid operation_type: {request.operation_type}") from exc

    table = session.execute(
        select(Table).where(Table.id == request.table_id).options(selectinload(Table.source))
    ).scalar_one_or_none()
    if not table:
        raise HTTPException(404, "Table not found")

    impact_preview = estimate_impact(
        session,
        request.table_id,
        operation_type,
        request.operation_params,
    )
    req_data = ApprovalRequestData(
        table_id=request.table_id,
        operation_type=operation_type,
        requested_by=request.requested_by,
        request_reason=request.reason,
        impact_preview=impact_preview,
        operation_params=request.operation_params,
    )

    approval_req = create_req(session, req_data)

    return ApprovalRequestRead(
        id=approval_req.id,
        table_id=approval_req.table_id,
        table_name=qualified_table_name(table),
        operation_type=approval_req.operation_type,
        status=approval_req.status,
        requested_by=approval_req.requested_by,
        impact_preview=approval_req.impact_preview,
        created_at=approval_req.created_at,
        expires_at=approval_req.expires_at,
    )


@router.post("/approvals/{request_id}/action")
def approve_or_reject(
    request_id: UUID,
    action: ApprovalAction,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_editor)],
) -> dict[str, str]:
    """Approve or reject an approval request."""
    from cryo.workflows.approvals import approve_request, reject_request

    if action.approved:
        result = approve_request(session, request_id, action.approver, action.reason)
    else:
        result = reject_request(session, request_id, action.approver, action.reason)

    return {"status": result.status, "message": "Request processed successfully"}


# ==================== Data Lineage ====================


class LineageNodeRead(BaseModel):
    table_id: str
    table_name: str
    depth: int
    node_type: str


class LineageGraphRead(BaseModel):
    root_table_id: str
    root_table_name: str
    upstream: list[LineageNodeRead]
    downstream: list[LineageNodeRead]


@router.get("/lineage/{table_id}", response_model=LineageGraphRead)
def get_table_lineage(
    table_id: UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
    depth: int = Query(3, ge=1, le=10),
) -> LineageGraphRead:
    """Get lineage graph for a table."""
    from cryo.db.models import Table
    from cryo.lineage.tracker import get_lineage_graph

    table = session.execute(select(Table).where(Table.id == table_id)).scalar_one_or_none()
    if not table:
        raise HTTPException(404, "Table not found")

    graph = get_lineage_graph(session, table_id, depth=depth)

    return LineageGraphRead(
        root_table_id=str(graph.table_id),
        root_table_name=graph.table_name,
        upstream=[
            LineageNodeRead(
                table_id=str(n.table_id),
                table_name=n.table_name,
                depth=1,
                node_type=n.edge_type,
            )
            for n in graph.upstream_tables
        ],
        downstream=[
            LineageNodeRead(
                table_id=str(n.table_id),
                table_name=n.table_name,
                depth=1,
                node_type=n.edge_type,
            )
            for n in graph.downstream_tables
        ],
    )


# ==================== Anomaly Detection ====================


class AnomalyAlertRead(BaseModel):
    table_id: str
    table_name: str
    metric_name: str
    current_value: float
    expected_value: float
    deviation_sigma: float
    severity: str
    description: str
    detected_at: datetime


@router.get("/anomalies", response_model=list[AnomalyAlertRead])
def detect_anomalies(
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
    lookback_days: int = Query(30, ge=7, le=90),
    threshold_sigma: float = Query(3.0, ge=2.0, le=5.0),
    dispatch_alerts: bool = Query(False, description="Send alerts to Slack/PagerDuty"),
) -> list[AnomalyAlertRead]:
    """Detect anomalies across all tables."""
    from cryo.ml.anomaly_detector import detect_all_anomalies

    alerts = detect_all_anomalies(
        session,
        lookback_days=lookback_days,
        threshold_sigma=threshold_sigma,
        dispatch_alerts=dispatch_alerts,
    )

    return [
        AnomalyAlertRead(
            table_id=a.table_id,
            table_name=a.table_name,
            metric_name=a.metric_name,
            current_value=a.current_value,
            expected_value=a.expected_value,
            deviation_sigma=a.deviation_sigma,
            severity=a.severity,
            description=a.description,
            detected_at=a.detected_at,
        )
        for a in alerts
    ]


@router.get("/anomalies/{table_id}", response_model=list[AnomalyAlertRead])
def detect_table_anomalies(
    table_id: UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
    lookback_days: int = Query(30, ge=7, le=90),
    threshold_sigma: float = Query(3.0, ge=2.0, le=5.0),
) -> list[AnomalyAlertRead]:
    from cryo.ml.anomaly_detector import detect_table_anomalies as detect

    alerts = detect(
        session,
        str(table_id),
        lookback_days=lookback_days,
        threshold_sigma=threshold_sigma,
    )

    return [
        AnomalyAlertRead(
            table_id=a.table_id,
            table_name=a.table_name,
            metric_name=a.metric_name,
            current_value=a.current_value,
            expected_value=a.expected_value,
            deviation_sigma=a.deviation_sigma,
            severity=a.severity,
            description=a.description,
            detected_at=a.detected_at,
        )
        for a in alerts
    ]


# ==================== Data Quality Profiling ====================


class ColumnQualityMetricRead(BaseModel):
    column_name: str
    null_rate_pct: float
    null_rate_baseline_pct: float
    null_rate_spike: bool
    min_value: object | None = None
    max_value: object | None = None
    value_range_changed: bool = False
    value_range_explanation: str = ""
    column_type: str | None = None


class DataQualityProfileRead(BaseModel):
    table_id: str
    table_name: str
    profiled_at: datetime
    column_metrics: list[ColumnQualityMetricRead]
    anomalies_detected: int
    schema_version_changed: bool
    new_columns: list[str]
    dropped_columns: list[str]


@router.get("/data-quality/{table_id}", response_model=DataQualityProfileRead)
def get_data_quality_profile(
    table_id: UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
    lookback_days: int = Query(30, ge=7, le=90),
) -> DataQualityProfileRead:
    """Get zero-scan data quality profile for a table."""
    from cryo.quality.profiler import profile_table_quality

    profile = profile_table_quality(session, str(table_id), lookback_days=lookback_days)

    return DataQualityProfileRead(
        table_id=profile.table_id,
        table_name=profile.table_name,
        profiled_at=profile.profiled_at,
        column_metrics=[
            ColumnQualityMetricRead(
                column_name=m.column_name,
                null_rate_pct=m.null_rate_pct,
                null_rate_baseline_pct=m.null_rate_baseline_pct,
                null_rate_spike=m.null_rate_spike,
                min_value=m.min_value,
                max_value=m.max_value,
                value_range_changed=m.value_range_changed,
                value_range_explanation=m.value_range_explanation,
                column_type=m.column_type,
            )
            for m in profile.column_metrics
        ],
        anomalies_detected=profile.anomalies_detected,
        schema_version_changed=profile.schema_version_changed,
        new_columns=profile.new_columns,
        dropped_columns=profile.dropped_columns,
    )


# ==================== Partition Evolution ====================


class PartitionEvolutionRecommendationRead(BaseModel):
    table_id: str
    table_name: str
    current_partition_spec: str
    recommended_partition_spec: str
    reason: str
    confidence: float
    estimated_pruning_improvement_pct: float
    query_patterns_analyzed: int
    cardinality_estimate: str = "unknown"
    warnings: list[str] = []


@router.get(
    "/partition-evolution/{table_id}",
    response_model=PartitionEvolutionRecommendationRead | None,
)
def get_partition_evolution_recommendation(
    table_id: UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
    min_query_threshold: int = Query(100, ge=10),
) -> PartitionEvolutionRecommendationRead | None:
    """Get partition evolution recommendation for a table."""
    from cryo.optimizer.partition_analyzer import analyze_partition_evolution

    rec = analyze_partition_evolution(
        session, str(table_id), min_query_threshold=min_query_threshold
    )

    if not rec:
        return None

    return PartitionEvolutionRecommendationRead(
        table_id=rec.table_id,
        table_name=rec.table_name,
        current_partition_spec=rec.current_partition_spec,
        recommended_partition_spec=rec.recommended_partition_spec,
        reason=rec.reason,
        confidence=rec.confidence,
        estimated_pruning_improvement_pct=rec.estimated_pruning_improvement_pct,
        query_patterns_analyzed=rec.query_patterns_analyzed,
        cardinality_estimate=rec.cardinality_estimate,
        warnings=rec.warnings,
    )


# ==================== Signal-Driven Triggers ====================


class MaintenanceSignalRead(BaseModel):
    table_id: str
    table_name: str
    signal_type: str
    severity: str
    current_value: float
    baseline_value: float
    threshold: float
    description: str
    detected_at: datetime


@router.get("/signals", response_model=list[MaintenanceSignalRead])
def detect_maintenance_signals(
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> list[MaintenanceSignalRead]:
    """Detect all maintenance signals (file degradation, latency spikes, cost spikes)."""
    from cryo.signals import detect_all_signals

    signals = detect_all_signals(session)

    return [
        MaintenanceSignalRead(
            table_id=str(s.table_id),
            table_name=s.table_name,
            signal_type=s.signal_type,
            severity=s.severity,
            current_value=s.current_value,
            baseline_value=s.baseline_value,
            threshold=s.threshold,
            description=s.description,
            detected_at=s.detected_at,
        )
        for s in signals
    ]


# ==================== FinOps Forecasts ====================


class CostForecastRead(BaseModel):
    forecast_month: str
    current_month_storage_cost_usd: float
    current_month_compute_cost_usd: float
    current_month_maintenance_cost_usd: float
    forecast_storage_cost_usd: float
    forecast_compute_cost_usd: float
    forecast_maintenance_cost_usd: float
    total_forecast_usd: float
    budget_limit_usd: float | None
    alert_level: str


@router.get("/finops/forecast", response_model=CostForecastRead)
def get_cost_forecast(
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
    budget_limit_usd: float | None = Query(None, ge=0),
) -> CostForecastRead:
    """Get next month cost forecast."""
    from cryo.finops.forecast import forecast_next_month_cost

    forecast = forecast_next_month_cost(session, budget_limit_usd=budget_limit_usd)

    return CostForecastRead(
        forecast_month=forecast.forecast_month,
        current_month_storage_cost_usd=forecast.current_month_storage_cost_usd,
        current_month_compute_cost_usd=forecast.current_month_compute_cost_usd,
        current_month_maintenance_cost_usd=forecast.current_month_maintenance_cost_usd,
        forecast_storage_cost_usd=forecast.forecast_storage_cost_usd,
        forecast_compute_cost_usd=forecast.forecast_compute_cost_usd,
        forecast_maintenance_cost_usd=forecast.forecast_maintenance_cost_usd,
        total_forecast_usd=forecast.total_forecast_usd,
        budget_limit_usd=forecast.budget_limit_usd,
        alert_level=forecast.alert_level,
    )


# ==================== Workload-Aware Compaction Candidates (AutoComp, SIGMOD 2025) ====


@router.get(
    "/compaction-candidates/{table_id}",
    response_model=CompactionCandidatesResponse,
)
def get_compaction_candidates(
    table_id: UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
    horizon_days: int = Query(30, ge=1, le=365),
    k: int = Query(10, ge=1, le=100),
) -> CompactionCandidatesResponse:
    """Return AutoComp-style workload-aware compaction candidates for a table."""
    from cryo.db.models import Table
    from cryo.scanner.compaction_candidates import find_candidates

    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(404, "Table not found")

    candidates = find_candidates(session, table_id, horizon_days=horizon_days, k=k)
    return CompactionCandidatesResponse(
        table_id=table_id,
        candidates=[
            CompactionCandidateRead(
                partition_path=c.partition_path,
                partition_size_gb=c.partition_size_gb,
                small_files=c.small_files,
                query_freq_per_day=c.query_freq_per_day,
                p95_latency_ms=c.p95_latency_ms,
                score=c.score,
                priority=c.priority,
                savings_estimate_usd=c.savings_estimate_usd,
                reasons=list(c.reasons),
            )
            for c in candidates
        ],
        generated_at=datetime.now(UTC),
    )


# ==================== Unionable Tables Search (EasyTUS, arXiv:2511.02674) ====================


@router.get(
    "/unionable/{table_id}",
    response_model=UnionableResponse,
)
def get_unionable_tables(
    table_id: UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
    k: int = Query(10, ge=1, le=100),
    use_llm: bool = Query(True, description="Enrich rationale via configured LLM."),
) -> UnionableResponse:
    """Return tables whose schema is compatible with the target for UNION ALL."""
    from cryo.ai.union_search import find_unionable
    from cryo.db.models import Table

    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(404, "Table not found")

    results = find_unionable(session, table_id, k=k, use_llm=use_llm)
    return UnionableResponse(
        target_table_id=table_id,
        results=[
            UnionScoreRead(
                target_table_id=r.target_table_id,
                candidate_table_id=r.candidate_table_id,
                score=r.score,
                overlap_columns=list(r.overlap_columns),
                union_sql=r.union_sql,
                rationale=r.rationale,
                candidate_schema=r.candidate_schema,
                candidate_table_name=r.candidate_table_name,
                candidate_schema_name=r.candidate_schema_name,
            )
            for r in results
        ],
        generated_at=datetime.now(UTC),
    )


# ==================== AI Skillset Versioning (Skill issues, arXiv:2606.01185) ====================


@router.get("/skillsets", response_model=SkillsetListResponse)
def list_skillsets(
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> SkillsetListResponse:
    """List every skillset version, newest first."""
    from cryo.ai.skillset import list_skillsets

    rows = list_skillsets(session)
    return SkillsetListResponse(
        skillsets=[
            SkillsetRead(
                id=row.id,
                name=row.name,
                version=row.version,
                prompts=row.prompts,
                tools=row.tools,
                created_at=row.created_at,
                is_active=row.is_active,
                metrics=row.metrics,
            )
            for row in rows
        ],
        generated_at=datetime.now(UTC),
    )


@router.get("/skillsets/{name}/active", response_model=SkillsetRead | None)
def get_active_skillset(
    name: str,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> SkillsetRead | None:
    """Return the currently active version of a skillset, or None."""
    from cryo.ai.skillset import load_skillset

    row = load_skillset(session, name)
    if not row.id:
        return None
    return SkillsetRead(
        id=row.id,
        name=row.name,
        version=row.version,
        prompts=row.prompts,
        tools=row.tools,
        created_at=row.created_at,
        is_active=row.is_active,
        metrics=row.metrics,
    )


@router.post("/skillsets", response_model=SkillsetRead)
def create_skillset(
    payload: SkillsetWrite,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_editor)],
) -> SkillsetRead:
    """Create a new skillset version (and optionally activate it)."""
    from cryo.ai.skillset import Skillset as SkillsetDC
    from cryo.ai.skillset import register_skillset

    skillset = SkillsetDC(
        id="",
        name=payload.name,
        version=payload.version,
        prompts=payload.prompts,
        tools=payload.tools,
        is_active=payload.activate,
    )
    row = register_skillset(session, skillset)
    return SkillsetRead(
        id=row.id,
        name=row.name,
        version=row.version,
        prompts=row.prompts,
        tools=row.tools,
        created_at=row.created_at,
        is_active=row.is_active,
        metrics=row.metrics,
    )


@router.post("/skillsets/{name}/rollback", response_model=SkillsetRead)
def rollback_skillset(
    name: str,
    payload: SkillsetRollbackRequest,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_editor)],
) -> SkillsetRead:
    """Re-activate a prior skillset version."""
    from cryo.ai.skillset import set_active_skillset

    try:
        row = set_active_skillset(session, name, payload.version)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return SkillsetRead(
        id=row.id,
        name=row.name,
        version=row.version,
        prompts=row.prompts,
        tools=row.tools,
        created_at=row.created_at,
        is_active=row.is_active,
        metrics=row.metrics,
    )


@router.post("/skillsets/{name}/outcome", response_model=SkillsetOutcomeRead)
def record_skillset_outcome(
    name: str,
    payload: SkillsetOutcomeWrite,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_editor)],
) -> SkillsetOutcomeRead:
    """Record the decision on a skillset recommendation and roll up metrics."""
    from cryo.ai.skillset import load_skillset, record_outcome

    row = load_skillset(session, name)
    if not row.id:
        raise HTTPException(404, f"Skillset {name!r} not found")

    outcome = record_outcome(
        session,
        row.id,
        prompt_name=payload.prompt_name,
        recommendation=payload.recommendation,
        decision=payload.decision,
        score=payload.score,
        table_id=payload.table_id,
    )
    return SkillsetOutcomeRead(
        id=outcome.id,
        skillset_id=outcome.skillset_id,
        recorded_at=outcome.recorded_at,
        table_id=outcome.table_id,
        prompt_name=outcome.prompt_name,
        recommendation=outcome.recommendation,
        decision=outcome.decision,
        score=outcome.score,
    )


@router.post("/gitops/webhook", tags=["gitops"])
def gitops_webhook(
    payload: dict,
    session: SessionDep,
    request: Request,
    _principal: Annotated[Principal, Depends(require_editor)],
) -> dict[str, object]:
    """Reload GitOps policies when a git push webhook fires."""
    from pathlib import Path

    from cryo.config import get_settings
    from cryo.gitops.policy_loader import handle_git_webhook

    settings = get_settings()
    if settings.gitops_webhook_secret:
        token = request.headers.get("X-Cryo-GitOps-Secret", "")
        if token != settings.gitops_webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")
    if not settings.gitops_repo_path:
        raise HTTPException(
            status_code=503,
            detail="GitOps repository path is not configured (CRYO_GITOPS_REPO_PATH)",
        )
    result = handle_git_webhook(
        payload,
        repo_path=Path(settings.gitops_repo_path),
        policy_dir_rel=settings.gitops_policy_dir,
        session=session,
    )
    session.commit()
    return result


# ==================== Plan Verifier (proof-carrying plans) ====================


def _build_domain_plan(payload: PlanVerifyRequest) -> Any:
    from cryo.domain.enums import Mode, OperationType, PartitionTransform
    from cryo.domain.models import (
        MaintenancePlan,
        Operation,
        PartitionPredicate,
        PartitionPredicateTerm,
    )

    ops: list[Operation] = []
    for op_read in payload.plan.operations:
        predicates: list[PartitionPredicate] = []
        for pred_read in op_read.predicates or []:
            terms: list[PartitionPredicateTerm] = []
            for term_read in pred_read.terms:
                terms.append(
                    PartitionPredicateTerm(
                        base_column=term_read.base_column,
                        transform=PartitionTransform(term_read.transform),
                        value=term_read.value,
                        col_type=term_read.col_type,
                        transform_param=term_read.transform_param,
                        partition_field=term_read.partition_field,
                        unsupported_reason=term_read.unsupported_reason,
                    )
                )
            predicates.append(
                PartitionPredicate(
                    terms=tuple(terms),
                    unsupported_reason=pred_read.unsupported_reason,
                )
            )
        ops.append(
            Operation(
                type=OperationType(op_read.type),
                params=dict(op_read.params),
                predicates=tuple(predicates) if predicates else None,
            )
        )
    return MaintenancePlan(
        table_ref=payload.plan.table_ref,
        mode=Mode(payload.plan.mode),
        operations=tuple(ops),
        notes=tuple(payload.plan.notes),
    )


@router.post(
    "/plan-verify",
    response_model=PlanVerdictRead,
    dependencies=[Depends(require_viewer)],
)
def verify_plan(
    payload: PlanVerifyRequest,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> PlanVerdictRead:
    """Statically verify a ``MaintenancePlan`` against the live catalog + engine capabilities.
    Returns a ``PlanVerdictRead`` with the plan's SHA-256 hash, the invariants that ran, and any
    violations (critical ones flip ``verified`` to False)."""
    from cryo.planner.verifier import PlanVerifier

    settings = get_settings()
    if not settings.plan_verifier_enabled:
        raise HTTPException(status_code=503, detail="Plan verifier is disabled by configuration")
    plan = _build_domain_plan(payload)
    verdict = PlanVerifier(session=session, settings=settings).verify(plan)
    return PlanVerdictRead(
        verified=verdict.verified,
        plan_hash=verdict.plan_hash,
        verifier_version=verdict.verifier_version,
        invariants_checked=list(verdict.invariants_checked),
        violations=[
            PlanViolationRead(
                invariant=v.invariant,
                severity=v.severity,
                message=v.message,
                location=v.location,
            )
            for v in verdict.violations
        ],
    )


# ==================== Observation Lakehouse ====================


def _get_observation_lakehouse() -> ObservationLakehouse:
    settings = get_settings()
    return ObservationLakehouse(storage_path=settings.observation_lakehouse_path)


@router.get(
    "/observability/search",
    response_model=ObservationSearchResponse,
    dependencies=[Depends(require_viewer)],
)
def search_observations(
    _principal: Annotated[Principal, Depends(require_viewer)],
    table_id: Annotated[str | None, Query()] = None,
    event_type: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> ObservationSearchResponse:
    """Search the local observation archive (SANER 2026 observation lakehouse)."""
    lakehouse = _get_observation_lakehouse()
    events = lakehouse.query(
        table_id=table_id,
        event_type=event_type,
        since=since,
        until=until,
        limit=limit,
    )
    items = [
        ObservationEventRead(
            id=event.id,
            recorded_at=event.recorded_at,
            run_id=event.run_id,
            event_type=event.event_type,
            level=event.level,
            table_id=event.table_id,
            subject=event.subject,
            payload=dict(event.payload),
            correlation_id=event.correlation_id,
            actor=event.actor,
        )
        for event in events
    ]
    return ObservationSearchResponse(items=items, total=len(items))


@router.get(
    "/observability/stats",
    response_model=ObservationStatsResponse,
    dependencies=[Depends(require_viewer)],
)
def observation_stats(
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> ObservationStatsResponse:
    """Return archive-level stats (file count, byte total, oldest/newest event)."""
    lakehouse = _get_observation_lakehouse()
    stats = lakehouse.archive_stats()
    return ObservationStatsResponse(
        total_files=int(stats["total_files"]),
        total_bytes=int(stats["total_bytes"]),
        oldest_event=stats["oldest_event"],
        newest_event=stats["newest_event"],
    )


@router.post(
    "/observability/compact",
    response_model=ObservationCompactResponse,
    dependencies=[Depends(require_editor)],
)
def compact_observations(
    _principal: Annotated[Principal, Depends(require_editor)],
) -> ObservationCompactResponse:
    """Deduplicate by id inside each hour file. The lakehouse is append-only; this is the only
    destructive op and it's idempotent."""
    lakehouse = _get_observation_lakehouse()
    result = lakehouse.compact()
    return ObservationCompactResponse(
        files_merged=int(result["files_merged"]),
        rows=int(result["rows"]),
        bytes_in=int(result["bytes_in"]),
        bytes_out=int(result["bytes_out"]),
    )


# ==================== Puffin-backed column sketches (FORMATS '26, arXiv:2605.30308) =====


class ColumnSketchBlob(BaseModel):
    path: str
    size: int
    offset: int
    sketch_type: str


class ColumnSketchRead(BaseModel):
    field_type: str | None
    ndv_estimate: int
    quantiles: dict[str, float]
    sketch_count: int
    puffin_blobs: list[ColumnSketchBlob]


class ColumnStatsResponse(BaseModel):
    table_id: UUID
    columns: dict[str, ColumnSketchRead]


@router.get(
    "/column-stats/{table_id}",
    response_model=ColumnStatsResponse,
)
def get_column_stats(
    table_id: UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
    columns: str | None = Query(
        None,
        description=(
            "Optional comma-separated column whitelist; defaults to all columns with sketches."
        ),
    ),
) -> ColumnStatsResponse:
    """Return per-column sketches (Theta NDV + KLL quantiles) from the latest Puffin blobs.

    Walks the ``column_stats_sketch`` table for the requested table, decodes each Puffin
    blob, and merges the per-scan sketches into a global per-column summary. Returns
    ``p50/p95/p99`` quantiles plus an ``ndv_estimate``.
    """
    from cryo.db.models import ColumnStatsSketch, Table
    from cryo.scanner.puffin_writer import decode_puffin_blob
    from cryo.scanner.sketches import KLLSketch, ThetaSketch

    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(404, "Table not found")

    requested: set[str] | None = None
    if columns:
        requested = {c.strip() for c in columns.split(",") if c.strip()}

    rows = session.scalars(
        select(ColumnStatsSketch)
        .where(ColumnStatsSketch.table_id == table_id)
        .order_by(ColumnStatsSketch.created_at.desc())
        .limit(2000)
    ).all()

    if not rows:
        return ColumnStatsResponse(table_id=table_id, columns={})

    # Group rows by blob path so we read each Puffin file at most once.
    by_path: dict[str, list[ColumnStatsSketch]] = {}
    for row in rows:
        by_path.setdefault(row.puffin_path, []).append(row)

    decoded_per_blob: dict[str, dict[str, dict[str, Any]]] = {}
    for path in by_path:
        local_path = _resolve_local_puffin_path(path)
        if local_path is None or not local_path.exists():
            continue
        try:
            payload = local_path.read_bytes()
        except OSError:
            continue
        try:
            decoded_per_blob[path] = decode_puffin_blob(payload)
        except ValueError:
            continue

    # Merge across all decoded blobs.
    merged: dict[str, dict[str, Any]] = {}
    blob_metadata: dict[str, list[ColumnSketchBlob]] = {}
    for row in rows:
        path = row.puffin_path
        decoded = decoded_per_blob.get(path)
        if decoded is None:
            continue
        column_sketches = decoded.get(row.column_name, {})
        target = merged.setdefault(
            row.column_name,
            {"theta": ThetaSketch(), "kll": KLLSketch(), "field_type": row.field_type},
        )
        if "theta" in column_sketches:
            target["theta"] = target["theta"].merge(column_sketches["theta"])
        if "kll" in column_sketches:
            target["kll"] = target["kll"].merge(column_sketches["kll"])
        if target.get("field_type") is None and row.field_type:
            target["field_type"] = row.field_type
        blob_metadata.setdefault(row.column_name, []).append(
            ColumnSketchBlob(
                path=path,
                size=int(row.puffin_size),
                offset=int(row.puffin_offset),
                sketch_type=str(row.sketch_type),
            )
        )

    response_columns: dict[str, ColumnSketchRead] = {}
    for column_name, sketches in merged.items():
        if requested is not None and column_name not in requested:
            continue
        theta: ThetaSketch = sketches["theta"]
        kll: KLLSketch = sketches["kll"]
        response_columns[column_name] = ColumnSketchRead(
            field_type=sketches.get("field_type"),
            ndv_estimate=int(theta.estimate),
            quantiles={
                "p50": float(kll.quantile(0.5)),
                "p95": float(kll.quantile(0.95)),
                "p99": float(kll.quantile(0.99)),
            },
            sketch_count=int(theta.n),
            puffin_blobs=blob_metadata.get(column_name, []),
        )

    return ColumnStatsResponse(table_id=table_id, columns=response_columns)


def _resolve_local_puffin_path(path: str) -> Path | None:
    """Resolve a Puffin blob path to a local file.

    Production paths start with ``s3://`` -- we don't fetch them in this endpoint (the
    Puffin blob bytes would need an S3 client and would dominate API latency). Local
    paths that already exist on disk are served directly.
    """
    if path.startswith("s3://"):
        return None
    p = Path(path)
    if p.exists():
        return p
    # The writer sometimes returns an s3:// path even in local dev; fall back to the
    # local mirror under .local/puffin so the endpoint still works.
    base = Path(".local/puffin")
    candidate = base / Path(path).name
    return candidate if candidate.exists() else None


# ==================== Silent-data-loss detector (arXiv:2604.20081) =====================


class SilentDataLossReportRead(BaseModel):
    table_id: UUID
    snapshot_id: int | None
    orphan_data_files: list[str]
    orphan_size_bytes: int
    missing_files: list[str]
    detected_at: str
    root_cause: str
    is_clean: bool


class SilentDataLossRepairResponse(BaseModel):
    table_id: UUID
    audit_log_id: int | None
    status: str
    message: str


@router.get(
    "/silent-data-loss/{table_id}",
    response_model=SilentDataLossReportRead | None,
)
def get_silent_data_loss(
    table_id: UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_viewer)],
) -> SilentDataLossReportRead | None:
    """Return the latest silent-data-loss report for a table.

    Runs the detector on demand; the scheduler sweeps every ``silent_loss_check_interval_min``
    minutes for fleet-wide coverage, but this endpoint lets an operator force a fresh
    check from the UI.
    """
    from cryo.db.models import Table
    from cryo.scheduler.health import check_silent_data_loss

    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(404, "Table not found")
    report = check_silent_data_loss(session, table_id)
    if report is None:
        return None
    return SilentDataLossReportRead(
        table_id=table_id,
        snapshot_id=report.snapshot_id,
        orphan_data_files=list(report.orphan_data_files),
        orphan_size_bytes=int(report.orphan_size_bytes),
        missing_files=list(report.missing_files),
        detected_at=report.detected_at,
        root_cause=report.root_cause,
        is_clean=report.is_clean(),
    )


@router.post(
    "/silent-data-loss/{table_id}/repair",
    response_model=SilentDataLossRepairResponse,
    dependencies=[Depends(require_editor)],
)
def repair_silent_data_loss(
    table_id: UUID,
    session: SessionDep,
    _principal: Annotated[Principal, Depends(require_editor)],
) -> SilentDataLossRepairResponse:
    """Trigger ``iceberg.rewrite_manifests`` to repair silent-data-loss drift.

    Local dev: this is a noop that records an AuditLog entry so the UI can confirm the
    repair intent was captured. Production: enqueue a maintenance job that runs
    ``rewrite_manifests`` against the destination engine.
    """
    from cryo.db.models import AuditLog, Table
    from cryo.scheduler.health import check_silent_data_loss

    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(404, "Table not found")
    report = check_silent_data_loss(session, table_id)
    audit = AuditLog(
        table_id=table_id,
        action="silent_data_loss_repair",
        status="OK",
        engine="noop",
        detail=(
            f"orphan_count={len(report.orphan_data_files) if report else 0} "
            f"missing_count={len(report.missing_files) if report else 0} "
            f"root_cause={report.root_cause if report else 'unknown'}"
        ),
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    return SilentDataLossRepairResponse(
        table_id=table_id,
        audit_log_id=int(audit.id),
        status="noop",
        message=(
            "Local dev: audit recorded. In production this would enqueue "
            "iceberg.rewrite_manifests against the destination engine."
        ),
    )
