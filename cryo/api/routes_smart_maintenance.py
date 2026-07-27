from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import joinedload

from cryo.api.deps import SessionDep, require_editor, require_operator, require_viewer
from cryo.api.schemas import MaintenanceRunResponse
from cryo.api.security import Principal
from cryo.db.models import (
    Destination,
    LayoutRecommendation,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableCost,
    TableStats,
)
from cryo.db.repositories import presets
from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.base import RenderedStatement
from cryo.engines.spark.k8s_engine import SparkK8sEngine
from cryo.governor.admission import AdmissionJob
from cryo.governor.native_admit import _fallback_engine_config
from cryo.governor.roi_gate import ROIGate
from cryo.scheduler.forecaster import WorkloadForecaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/smart-maintenance", tags=["smart-maintenance"])


class TierSummary(BaseModel):
    hot: int = 0
    warm: int = 0
    cold: int = 0
    dormant: int = 0
    unknown: int = 0


class SmartMaintenanceOverview(BaseModel):
    generated_at: datetime
    tables_with_stats: int
    tiers: TierSummary
    roi_shadow_decisions_30d: int
    roi_enforced: bool
    pending_layout_recommendations: int


class ROIDecisionRead(BaseModel):
    action: str
    reason: str
    bcr: float
    threshold: float
    shadow: bool
    benefit_usd_month: float
    cost_usd_month: float
    ts: datetime | None = None


class ROIBreakdownRead(BaseModel):
    table_id: uuid.UUID
    decision: ROIDecisionRead
    monthly_benefit_usd: float
    monthly_cost_usd: float
    details: dict[str, float]
    effectiveness: dict[str, Any] | None = None
    top_moop_partitions: list[dict[str, Any]] = Field(default_factory=list)


class DetectionTierRow(BaseModel):
    table_id: uuid.UUID
    table_name: str
    schema_name: str
    tier: str
    score: float | None = None
    commits_24h: int = 0
    commits_7d: int = 0
    delete_file_pct: float | None = None
    query_count_30d: int = 0


class DetectionTierResponse(BaseModel):
    items: list[DetectionTierRow]


class ForecastPointRead(BaseModel):
    hour_start: datetime
    expected_queries: float


class WorkloadForecastRead(BaseModel):
    table_id: uuid.UUID
    generated_at: datetime
    horizon_hours: int
    expected_total_queries: float
    points: list[ForecastPointRead]


class LayoutRecommendationRead(BaseModel):
    id: uuid.UUID
    table_id: uuid.UUID
    table_ref: str
    recommended_strategy: str
    recommended_columns: list[str] = Field(default_factory=list)
    status: str
    confidence: float
    estimated_prune_improvement: float | None = None
    observed_prune_improvement: float | None = None
    approved_at: datetime | None = None
    dismissed_at: datetime | None = None
    applied_at: datetime | None = None
    applied_run_id: uuid.UUID | None = None


class LayoutRecommendationListRead(BaseModel):
    items: list[LayoutRecommendationRead]


class ApproveLayoutRecommendationRequest(BaseModel):
    note: str | None = None


class DismissLayoutRecommendationRequest(BaseModel):
    reason: str = "dismissed_by_operator"


class ExecuteLayoutRecommendationRequest(BaseModel):
    mode: Literal["light", "standard", "deep"] = "standard"


@router.get("/overview", response_model=SmartMaintenanceOverview)
def smart_maintenance_overview(
    session: SessionDep,
    _principal: Principal = Depends(require_viewer),
) -> SmartMaintenanceOverview:
    # Aggregated in SQL rather than `select(TableStats)).all()` -- materializing every table's
    # full row (including the `raw` JSONB blob) into Python OOM'd the api pod once the catalog
    # grew past a few thousand tables (see the memory comment on api.resources in values.yaml).
    tier_col = func.lower(func.coalesce(TableStats.detection_tier, "unknown"))
    tier_counts = session.execute(select(tier_col, func.count()).group_by(tier_col)).all()
    tiers = TierSummary()
    tables_with_stats = 0
    for tier, count in tier_counts:
        tables_with_stats += count
        if tier == "hot":
            tiers.hot = count
        elif tier == "warm":
            tiers.warm = count
        elif tier == "cold":
            tiers.cold = count
        elif tier == "dormant":
            tiers.dormant = count
        else:
            tiers.unknown += count

    roi_shadow = (
        session.scalar(
            text(
                """
                select count(*)
                from cryo.table_stats ts,
                     jsonb_array_elements(
                         coalesce(ts.raw #> '{smart_maintenance,roi_decisions}', '[]'::jsonb)
                     ) as decision
                where (decision ->> 'shadow')::boolean is true
                """
            )
        )
        or 0
    )

    pending_layout = session.scalar(
        select(func.count(LayoutRecommendation.id)).where(
            LayoutRecommendation.status.in_(["pending", "approved"])
        )
    )
    if pending_layout is None:
        pending_layout = 0

    from cryo.config import get_settings

    settings = get_settings()
    return SmartMaintenanceOverview(
        generated_at=datetime.now(UTC),
        tables_with_stats=tables_with_stats,
        tiers=tiers,
        roi_shadow_decisions_30d=int(roi_shadow),
        roi_enforced=settings.roi_gate_enabled and not settings.roi_gate_shadow_mode,
        pending_layout_recommendations=int(pending_layout),
    )


@router.get("/detection-tiers", response_model=DetectionTierResponse)
def list_detection_tiers(
    session: SessionDep,
    limit: int = 200,
    _principal: Principal = Depends(require_viewer),
) -> DetectionTierResponse:
    rows = session.scalars(
        select(Table)
        .options(joinedload(Table.stats))
        .join(TableStats, TableStats.table_id == Table.id)
        .limit(max(1, min(limit, 1000)))
    ).all()
    items: list[DetectionTierRow] = []
    for table in rows:
        stats = table.stats
        if stats is None:
            continue
        smart = dict((stats.raw or {}).get("smart_maintenance") or {})
        items.append(
            DetectionTierRow(
                table_id=table.id,
                table_name=table.table_name,
                schema_name=table.schema_name,
                tier=stats.detection_tier or "unknown",
                score=smart.get("detection_tier_score"),
                commits_24h=int(smart.get("commits_24h") or 0),
                commits_7d=int(smart.get("commits_7d") or 0),
                delete_file_pct=stats.delete_file_pct,
                query_count_30d=int(stats.query_count_30d or 0),
            )
        )
    return DetectionTierResponse(items=items)


@router.get("/tables/{table_id}/roi", response_model=ROIBreakdownRead)
def get_table_roi(
    table_id: uuid.UUID,
    session: SessionDep,
    _principal: Principal = Depends(require_viewer),
) -> ROIBreakdownRead:
    stats = session.get(TableStats, table_id)
    if stats is None:
        raise HTTPException(status_code=404, detail="Table stats not found")
    decision = ROIGate().decide(
        session,
        job=AdmissionJob(
            job_id="api",
            table_id=str(table_id),
            destination_id="api",
            mode="standard",
        ),
        record=False,
    )

    # AutoComp §A.5: surface the same effectiveness signal update_table_effectiveness persists
    # and the table's top-5 MOOP candidates, so the ROI drawer doesn't need a second round trip.
    effectiveness = (stats.raw or {}).get("compaction_effectiveness") if stats.raw else None
    from sqlalchemy import func, select

    from cryo.db.models import TablePartition

    latest_scan = session.scalar(
        select(func.max(TablePartition.scan_at)).where(TablePartition.table_id == table_id)
    )
    top_moop_partitions: list[dict[str, Any]] = []
    if latest_scan is not None:
        top_rows = session.scalars(
            select(TablePartition)
            .where(
                TablePartition.table_id == table_id,
                TablePartition.scan_at == latest_scan,
                TablePartition.autocomp_traits.isnot(None),
            )
        ).all()
        ranked = sorted(
            top_rows,
            key=lambda p: (p.autocomp_traits or {}).get("s_c") or float("-inf"),
            reverse=True,
        )[:5]
        top_moop_partitions = [
            {
                "partition_value": p.partition_value,
                "moop_score": (p.autocomp_traits or {}).get("s_c"),
                "small_files": p.small_files_in_partition_count,
                "size_gb": p.partition_size_gb,
            }
            for p in ranked
        ]

    return ROIBreakdownRead(
        table_id=table_id,
        decision=ROIDecisionRead(
            action=decision.action.value,
            reason=decision.reason,
            bcr=decision.bcr,
            threshold=decision.threshold,
            shadow=decision.shadow,
            benefit_usd_month=decision.breakdown.total_benefit_usd_month,
            cost_usd_month=decision.breakdown.total_cost_usd_month,
            ts=datetime.now(UTC),
        ),
        monthly_benefit_usd=decision.breakdown.total_benefit_usd_month,
        monthly_cost_usd=decision.breakdown.total_cost_usd_month,
        details={
            "latency_benefit_usd_month": decision.breakdown.latency_benefit_usd_month,
            "storage_benefit_usd_month": decision.breakdown.storage_benefit_usd_month,
            "request_benefit_usd_month": decision.breakdown.request_benefit_usd_month,
            "opportunity_benefit_usd_month": decision.breakdown.opportunity_benefit_usd_month,
            "rewrite_compute_cost_usd_month": decision.breakdown.rewrite_compute_cost_usd_month,
            "write_amplification_cost_usd_month": decision.breakdown.write_amplification_cost_usd_month,
            "opportunity_cost_usd_month": decision.breakdown.opportunity_cost_usd_month,
        },
        effectiveness=effectiveness,
        top_moop_partitions=top_moop_partitions,
    )


@router.get("/tables/{table_id}/workload-forecast", response_model=WorkloadForecastRead)
def table_workload_forecast(
    table_id: uuid.UUID,
    session: SessionDep,
    horizon_hours: int = 24,
    _principal: Principal = Depends(require_viewer),
) -> WorkloadForecastRead:
    forecaster = WorkloadForecaster()
    forecast = forecaster.forecast(session, table_id, horizon_hours=max(1, min(horizon_hours, 7 * 24)))
    return WorkloadForecastRead(
        table_id=forecast.table_id,
        generated_at=forecast.generated_at,
        horizon_hours=forecast.horizon_hours,
        expected_total_queries=forecast.expected_total_queries,
        points=[
            ForecastPointRead(hour_start=point.hour_start, expected_queries=point.expected_queries)
            for point in forecast.points
        ],
    )


@router.get("/layout-recommendations", response_model=LayoutRecommendationListRead)
def list_layout_recommendations(
    session: SessionDep,
    status: str | None = None,
    _principal: Principal = Depends(require_viewer),
) -> LayoutRecommendationListRead:
    stmt = select(LayoutRecommendation, Table).join(Table, Table.id == LayoutRecommendation.table_id)
    if status:
        stmt = stmt.where(LayoutRecommendation.status == status)
    rows = session.execute(stmt.order_by(LayoutRecommendation.updated_at.desc()).limit(500)).all()
    items = [
        LayoutRecommendationRead(
            id=rec.id,
            table_id=rec.table_id,
            table_ref=f"{table.schema_name}.{table.table_name}",
            recommended_strategy=rec.recommended_strategy,
            recommended_columns=_normalize_recommended_columns(rec.recommended_columns),
            status=rec.status,
            confidence=rec.confidence,
            estimated_prune_improvement=rec.estimated_prune_improvement,
            observed_prune_improvement=rec.observed_prune_improvement,
            approved_at=rec.approved_at,
            dismissed_at=rec.dismissed_at,
            applied_at=rec.applied_at,
            applied_run_id=rec.applied_run_id,
        )
        for rec, table in rows
    ]
    return LayoutRecommendationListRead(items=items)


@router.post("/layout-recommendations/{recommendation_id}/approve", response_model=LayoutRecommendationRead)
def approve_layout_recommendation(
    recommendation_id: uuid.UUID,
    payload: ApproveLayoutRecommendationRequest,
    session: SessionDep,
    principal: Principal = Depends(require_operator),
) -> LayoutRecommendationRead:
    recommendation = session.get(LayoutRecommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    recommendation.status = "approved"
    recommendation.approved_at = datetime.now(UTC)
    recommendation.approved_by = principal.subject
    recommendation.approval_note = payload.note
    recommendation.dismissed_at = None
    recommendation.dismissed_by = None
    recommendation.dismissed_reason = None
    session.commit()
    table = session.get(Table, recommendation.table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    return LayoutRecommendationRead(
        id=recommendation.id,
        table_id=recommendation.table_id,
        table_ref=f"{table.schema_name}.{table.table_name}",
        recommended_strategy=recommendation.recommended_strategy,
        recommended_columns=_normalize_recommended_columns(recommendation.recommended_columns),
        status=recommendation.status,
        confidence=recommendation.confidence,
        estimated_prune_improvement=recommendation.estimated_prune_improvement,
        observed_prune_improvement=recommendation.observed_prune_improvement,
        approved_at=recommendation.approved_at,
        dismissed_at=recommendation.dismissed_at,
        applied_at=recommendation.applied_at,
        applied_run_id=recommendation.applied_run_id,
    )


@router.post("/layout-recommendations/{recommendation_id}/dismiss", response_model=LayoutRecommendationRead)
def dismiss_layout_recommendation(
    recommendation_id: uuid.UUID,
    payload: DismissLayoutRecommendationRequest,
    session: SessionDep,
    principal: Principal = Depends(require_operator),
) -> LayoutRecommendationRead:
    recommendation = session.get(LayoutRecommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    recommendation.status = "rejected"
    recommendation.dismissed_at = datetime.now(UTC)
    recommendation.dismissed_by = principal.subject
    recommendation.dismissed_reason = payload.reason
    session.commit()
    table = session.get(Table, recommendation.table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    return LayoutRecommendationRead(
        id=recommendation.id,
        table_id=recommendation.table_id,
        table_ref=f"{table.schema_name}.{table.table_name}",
        recommended_strategy=recommendation.recommended_strategy,
        recommended_columns=_normalize_recommended_columns(recommendation.recommended_columns),
        status=recommendation.status,
        confidence=recommendation.confidence,
        estimated_prune_improvement=recommendation.estimated_prune_improvement,
        observed_prune_improvement=recommendation.observed_prune_improvement,
        approved_at=recommendation.approved_at,
        dismissed_at=recommendation.dismissed_at,
        applied_at=recommendation.applied_at,
        applied_run_id=recommendation.applied_run_id,
    )


@router.post(
    "/layout-recommendations/{recommendation_id}/execute",
    response_model=MaintenanceRunResponse,
)
def execute_layout_recommendation(
    recommendation_id: uuid.UUID,
    payload: ExecuteLayoutRecommendationRequest,
    session: SessionDep,
    principal: Principal = Depends(require_editor),
) -> MaintenanceRunResponse:
    """Apply a layout recommendation's specific sort/z-order strategy.

    Regression, 2026-07-24: this used to dispatch the table's generic assigned preset
    (whatever operations that preset happens to run -- typically plain binpack compaction)
    and unconditionally mark the recommendation "applied" regardless of what the dispatched
    run actually did. It never read recommended_strategy/recommended_columns at all, so
    approving and "executing" a sort/z-order recommendation silently did neither -- no
    physical resort, no persisted write.sort-order. This dispatches the actual recommended
    rewrite via Spark (native Go doesn't support custom sort-order/z-order rewrites),
    persists write.sort-order for future writers on success, and only marks the
    recommendation applied if the rewrite genuinely succeeded.
    """
    recommendation = session.get(LayoutRecommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if recommendation.status != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"Recommendation must be approved before executing (status={recommendation.status!r})",
        )
    if recommendation.recommended_strategy not in ("sort", "zorder"):
        raise HTTPException(
            status_code=400,
            detail=f"No actionable rewrite for strategy {recommendation.recommended_strategy!r}",
        )
    columns = _normalize_recommended_columns(recommendation.recommended_columns)
    if not columns:
        raise HTTPException(status_code=400, detail="Recommendation has no columns to apply")

    table = session.get(Table, recommendation.table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    effective = presets.resolve_effective_preset(session, table)
    if effective is None:
        raise HTTPException(status_code=400, detail="Table has no effective preset/destination")

    destination = session.get(Destination, uuid.UUID(effective.preset.destination_id))
    if destination is None:
        raise HTTPException(status_code=422, detail="Assigned destination not found")
    fallback_id = (destination.config or {}).get("sql_fallback_destination_id") or (
        destination.config or {}
    ).get("fallback_destination_id")
    if not fallback_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "This table's destination has no sql_fallback_destination_id configured -- "
                "sort/z-order rewrites run via the Spark-on-K8s engine, same as other SQL "
                "fallback maintenance operations."
            ),
        )
    fallback_destination = session.get(Destination, uuid.UUID(str(fallback_id)))
    if fallback_destination is None:
        raise HTTPException(
            status_code=422, detail=f"Configured fallback destination {fallback_id} not found"
        )
    source = session.get(Source, table.source_id)
    if source is None:
        raise HTTPException(status_code=422, detail="Table's source not found")
    catalog_name = (source.config or {}).get("catalog_name")
    if not isinstance(catalog_name, str) or not catalog_name:
        raise HTTPException(
            status_code=422, detail="Table's source has no catalog_name configured"
        )
    table_ref = f"{catalog_name}.{table.schema_name}.{table.table_name}"

    run = MaintenanceRun(
        kind="adhoc",
        preset_id=None,
        trigger="layout-recommendation-apply",
        status="running",
        summary={"actor": principal.subject, "layout_recommendation_id": str(recommendation.id)},
    )
    session.add(run)
    session.flush()
    job = MaintenanceJob(
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode=payload.mode,
        status="running",
        started_at=datetime.now(UTC),
    )
    session.add(job)
    session.flush()

    op_params: dict[str, Any] = {"strategy": recommendation.recommended_strategy, "rewrite_all": True}
    if recommendation.recommended_strategy == "zorder":
        op_params["zorder_columns"] = columns
    else:
        op_params["sort_columns"] = columns
    plan = MaintenancePlan(
        table_ref=table_ref,
        mode=Mode(payload.mode),
        operations=(Operation(OperationType.OPTIMIZE, op_params),),
    )

    merged_config = _fallback_engine_config(fallback_destination.config, source.config)
    engine = SparkK8sEngine(config=merged_config)
    results = engine.execute_plan(plan)
    rewrite_result = results[0] if results else None

    if rewrite_result is None or not rewrite_result.ok:
        error = rewrite_result.error if rewrite_result else "No result from Spark dispatch"
        job.status = "failed"
        job.error = error
        job.engine_ref = rewrite_result.engine_ref if rewrite_result else None
        job.finished_at = datetime.now(UTC)
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        session.commit()
        raise HTTPException(
            status_code=502,
            detail=f"{recommendation.recommended_strategy} rewrite failed: {error} (run {run.id})",
        )

    persisted_sort_order = False
    persist_error: str | None = None
    if recommendation.recommended_strategy == "sort":
        order_expr = ", ".join(f"`{c}` ASC" for c in columns)
        alter_stmt = RenderedStatement(
            action=OperationType.OPTIMIZE,
            sql=f"ALTER TABLE {table_ref} WRITE ORDERED BY {order_expr}",
            engine=EngineType.SPARK_EMR,
            metadata={"catalog": catalog_name},
        )
        alter_result = engine.execute(alter_stmt)
        persisted_sort_order = alter_result.ok
        if not alter_result.ok:
            persist_error = alter_result.error
            logger.warning(
                "layout recommendation %s: rewrite succeeded but persisting write.sort-order "
                "failed: %s",
                recommendation.id,
                persist_error,
            )

    notes = []
    if recommendation.recommended_strategy == "sort":
        notes.append(
            "write.sort-order persisted for future writers"
            if persisted_sort_order
            else f"write.sort-order NOT persisted: {persist_error}"
        )

    job.status = "succeeded"
    job.finished_at = datetime.now(UTC)
    job.engine_ref = rewrite_result.engine_ref
    job.plan = {
        "table_ref": table_ref,
        "mode": payload.mode,
        "operations": [{"type": "optimize", "params": op_params}],
        "notes": notes,
    }
    run.status = "succeeded"
    run.finished_at = datetime.now(UTC)

    recommendation.status = "applied"
    recommendation.applied_at = datetime.now(UTC)
    recommendation.applied_run_id = run.id

    # Best-effort before/after effectiveness baseline from recent query telemetry.
    baseline = session.scalar(
        select(func.avg(TableCost.query_request_savings_usd_month)).where(
            TableCost.table_id == recommendation.table_id
        )
    )
    recommendation.baseline_scan_bytes = int((baseline or 0.0) * 1024 * 1024)
    session.commit()

    return MaintenanceRunResponse(run_id=run.id, job_ids=[job.id], status=run.status)


def _normalize_recommended_columns(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item:
            out.append(item)
            continue
        if isinstance(item, dict):
            column = item.get("column")
            if isinstance(column, str) and column:
                out.append(column)
    return out
