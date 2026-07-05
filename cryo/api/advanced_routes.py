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

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from cryo.api.deps import require_editor, require_viewer
from cryo.db.session import get_session

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
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
):
    """List all pending approval requests."""
    from cryo.workflows.approvals import get_pending_approvals
    
    requests = get_pending_approvals(session)
    
    # Enrich with table names
    from cryo.db.models import Table
    table_map = {
        t.id: t.name 
        for t in session.query(Table).filter(
            Table.id.in_([r.table_id for r in requests])
        ).all()
    }
    
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
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_editor)],
):
    """Create a new approval request for a destructive operation."""
    from cryo.workflows.approvals import ApprovalRequestData, create_approval_request as create_req
    from cryo.db.models import Table
    
    table = session.query(Table).filter_by(id=request.table_id).first()
    if not table:
        raise HTTPException(404, "Table not found")
    
    req_data = ApprovalRequestData(
        table_id=request.table_id,
        operation_type=request.operation_type,
        operation_params=request.operation_params,
        requested_by=request.requested_by,
        reason=request.reason,
    )
    
    approval_req = create_req(session, req_data)
    
    return ApprovalRequestRead(
        id=approval_req.id,
        table_id=approval_req.table_id,
        table_name=table.name,
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
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_editor)],
):
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
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
    depth: int = Query(3, ge=1, le=10),
):
    """Get lineage graph for a table."""
    from cryo.lineage.tracker import get_lineage_graph
    from cryo.db.models import Table
    
    table = session.query(Table).filter_by(id=table_id).first()
    if not table:
        raise HTTPException(404, "Table not found")
    
    graph = get_lineage_graph(session, table_id, max_depth=depth)
    
    return LineageGraphRead(
        root_table_id=graph.root_table_id,
        root_table_name=graph.root_table_name,
        upstream=[
            LineageNodeRead(
                table_id=n.table_id,
                table_name=n.table_name,
                depth=n.depth,
                node_type=n.node_type,
            )
            for n in graph.upstream
        ],
        downstream=[
            LineageNodeRead(
                table_id=n.table_id,
                table_name=n.table_name,
                depth=n.depth,
                node_type=n.node_type,
            )
            for n in graph.downstream
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
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
    lookback_days: int = Query(30, ge=7, le=90),
    threshold_sigma: float = Query(3.0, ge=2.0, le=5.0),
):
    """Detect anomalies across all tables."""
    from cryo.ml.anomaly_detector import detect_all_anomalies
    
    alerts = detect_all_anomalies(
        session, 
        lookback_days=lookback_days, 
        threshold_sigma=threshold_sigma
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
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
    lookback_days: int = Query(30, ge=7, le=90),
    threshold_sigma: float = Query(3.0, ge=2.0, le=5.0),
):
    """Detect anomalies for a specific table."""
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
    null_spike_detected: bool
    value_bounds: dict | None
    value_bound_violations: int


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
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
    lookback_days: int = Query(30, ge=7, le=90),
):
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
                null_spike_detected=m.null_spike_detected,
                value_bounds=m.value_bounds,
                value_bound_violations=m.value_bound_violations,
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


@router.get("/partition-evolution/{table_id}", response_model=PartitionEvolutionRecommendationRead | None)
def get_partition_evolution_recommendation(
    table_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
    min_query_threshold: int = Query(100, ge=10),
):
    """Get partition evolution recommendation for a table."""
    from cryo.optimizer.partition_analyzer import analyze_partition_evolution
    
    rec = analyze_partition_evolution(
        session, 
        str(table_id), 
        min_query_threshold=min_query_threshold
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
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
):
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
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
    budget_limit_usd: float | None = Query(None, ge=0),
):
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
