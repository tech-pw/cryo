from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, joinedload, sessionmaker

from cryo.api.deps import SessionDep, require_editor, require_operator, require_viewer
from cryo.api.schemas import MaintenanceRunResponse
from cryo.api.security import Principal
from cryo.config import get_settings
from cryo.db.models import (
    Destination,
    LayoutRecommendation,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableStats,
)
from cryo.db.repositories import presets
from cryo.db.repositories.exclusions import excluded_table_ids
from cryo.db.repositories.intents import IntentStore
from cryo.db.tenant_scope import get_scoped_resource, tenant_filter_id
from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.intents import AttemptStatus, EffectState, MaintenanceIntentRecord
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.engines.spark.k8s_engine import SparkK8sEngine
from cryo.governor.admission import AdmissionJob
from cryo.governor.native_admit import _fallback_engine_config
from cryo.governor.roi_gate import ROIGate
from cryo.intents.binding import bind_explicit_job_intent
from cryo.intents.validation import IntentValidationError, validate_effect_budget
from cryo.observability.correlation import correlation_summary
from cryo.planner.planner import full_table_optimize_allowed
from cryo.scheduler.forecaster import WorkloadForecaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/smart-maintenance", tags=["smart-maintenance"])
ViewerPrincipalDep = Annotated[Principal, Depends(require_viewer)]
OperatorPrincipalDep = Annotated[Principal, Depends(require_operator)]
EditorPrincipalDep = Annotated[Principal, Depends(require_editor)]


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
    truncated: bool = False
    limit: int = 500


class ApproveLayoutRecommendationRequest(BaseModel):
    note: str | None = None


class DismissLayoutRecommendationRequest(BaseModel):
    reason: str = "dismissed_by_operator"


class ExecuteLayoutRecommendationRequest(BaseModel):
    mode: Literal["light", "standard", "deep"] = "standard"


@router.get("/overview", response_model=SmartMaintenanceOverview)
def smart_maintenance_overview(
    session: SessionDep,
    _principal: ViewerPrincipalDep,
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
    _principal: ViewerPrincipalDep,
    limit: int = 200,
) -> DetectionTierResponse:
    tier_stmt = (
        select(Table)
        .options(joinedload(Table.stats))
        .join(TableStats, TableStats.table_id == Table.id)
    )
    tenant_id = tenant_filter_id(session)
    if tenant_id is not None:
        tier_stmt = tier_stmt.where(Table.tenant_id == tenant_id)
    rows = session.scalars(tier_stmt.limit(max(1, min(limit, 1000)))).all()
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
    _principal: ViewerPrincipalDep,
) -> ROIBreakdownRead:
    get_scoped_resource(session, Table, table_id, resource_type="Table")
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
            select(TablePartition).where(
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
            "write_amplification_cost_usd_month": (
                decision.breakdown.write_amplification_cost_usd_month
            ),
            "opportunity_cost_usd_month": decision.breakdown.opportunity_cost_usd_month,
        },
        effectiveness=effectiveness,
        top_moop_partitions=top_moop_partitions,
    )


@router.get("/tables/{table_id}/workload-forecast", response_model=WorkloadForecastRead)
def table_workload_forecast(
    table_id: uuid.UUID,
    session: SessionDep,
    _principal: ViewerPrincipalDep,
    horizon_hours: int = 24,
) -> WorkloadForecastRead:
    get_scoped_resource(session, Table, table_id, resource_type="Table")
    forecaster = WorkloadForecaster()
    forecast = forecaster.forecast(
        session, table_id, horizon_hours=max(1, min(horizon_hours, 7 * 24))
    )
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
    _principal: ViewerPrincipalDep,
    status: str | None = None,
) -> LayoutRecommendationListRead:
    stmt = select(LayoutRecommendation, Table).join(
        Table, Table.id == LayoutRecommendation.table_id
    )
    if status:
        stmt = stmt.where(LayoutRecommendation.status == status)
    tenant_id = tenant_filter_id(session)
    if tenant_id is not None:
        stmt = stmt.where(Table.tenant_id == tenant_id)
    rows = session.execute(stmt.order_by(LayoutRecommendation.updated_at.desc()).limit(501)).all()
    truncated = len(rows) > 500
    rows = rows[:500]
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
    return LayoutRecommendationListRead(items=items, truncated=truncated, limit=500)


@router.post(
    "/layout-recommendations/{recommendation_id}/approve", response_model=LayoutRecommendationRead
)
def approve_layout_recommendation(
    recommendation_id: uuid.UUID,
    payload: ApproveLayoutRecommendationRequest,
    session: SessionDep,
    principal: OperatorPrincipalDep,
) -> LayoutRecommendationRead:
    recommendation, table = _scoped_recommendation(session, recommendation_id)
    recommendation.status = "approved"
    recommendation.approved_at = datetime.now(UTC)
    recommendation.approved_by = principal.subject
    recommendation.approval_note = payload.note
    recommendation.dismissed_at = None
    recommendation.dismissed_by = None
    recommendation.dismissed_reason = None
    session.commit()
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
    "/layout-recommendations/{recommendation_id}/dismiss", response_model=LayoutRecommendationRead
)
def dismiss_layout_recommendation(
    recommendation_id: uuid.UUID,
    payload: DismissLayoutRecommendationRequest,
    session: SessionDep,
    principal: OperatorPrincipalDep,
) -> LayoutRecommendationRead:
    recommendation, table = _scoped_recommendation(session, recommendation_id)
    recommendation.status = "rejected"
    recommendation.dismissed_at = datetime.now(UTC)
    recommendation.dismissed_by = principal.subject
    recommendation.dismissed_reason = payload.reason
    session.commit()
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
    principal: EditorPrincipalDep,
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
    recommendation, table = _scoped_recommendation(session, recommendation_id)
    if recommendation.status != "approved":
        raise HTTPException(
            status_code=400,
            detail=(
                "Recommendation must be approved before executing "
                f"(status={recommendation.status!r})"
            ),
        )
    if recommendation.recommended_strategy not in ("sort", "zorder"):
        raise HTTPException(
            status_code=400,
            detail=f"No actionable rewrite for strategy {recommendation.recommended_strategy!r}",
        )
    columns = _normalize_recommended_columns(recommendation.recommended_columns)
    if not columns:
        raise HTTPException(status_code=400, detail="Recommendation has no columns to apply")

    # Regression, 2026-08-11: recommended_columns comes straight from QueryPattern.column_name
    # telemetry (get_recommended_sort_columns in cryo/telemetry/patterns.py), which is never
    # validated against the table's real Iceberg schema. Confirmed live on
    # lsq_pw.silver_prospect_activity_base: the recommendation included 'mx_customobject_1'
    # (not a real column -- almost certainly a query alias picked up from log parsing) and
    # 'activityevent' (real column is 'ActivityEvent', case mismatch) -- both dispatched a real
    # Spark job that failed deep in a Java stack trace with
    # "Cannot find field 'mx_customobject_1' in struct: ..." only after the rewrite had already
    # started. Validate against TableStats.raw.column_quality (the Go scanner's own column
    # list, already on hand, no extra catalog call) before ever dispatching, and resolve
    # case-only mismatches to the schema's real casing rather than failing on them --
    # NamedReference.bind is case-sensitive but query telemetry routinely lowercases columns.
    stats = session.scalar(select(TableStats).where(TableStats.table_id == table.id))
    column_quality = (stats.raw or {}).get("column_quality") or [] if stats is not None else []
    schema_columns = [str(c.get("column_name")) for c in column_quality if c.get("column_name")]
    if schema_columns:
        schema_by_lower = {c.lower(): c for c in schema_columns}
        resolved_columns: list[str] = []
        unknown_columns: list[str] = []
        for col in columns:
            real = schema_by_lower.get(col.lower())
            if real is None:
                unknown_columns.append(col)
            else:
                resolved_columns.append(real)
        if unknown_columns:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Recommendation {recommendation.id} references column(s) not present on "
                    f"{table.schema_name}.{table.table_name}'s current schema: "
                    f"{unknown_columns} -- dismiss and regenerate this recommendation rather "
                    "than dispatching a rewrite that will fail mid-job."
                ),
            )
        columns = resolved_columns

    effective = presets.resolve_effective_preset(session, table)
    if effective is None:
        raise HTTPException(status_code=400, detail="Table has no effective preset/destination")

    destination = session.get(Destination, uuid.UUID(effective.preset.destination_id))
    if destination is None:
        raise HTTPException(status_code=422, detail="Assigned destination not found")
    if destination.engine == EngineType.SPARK_EMR:
        # Table is already routed straight to a Spark-on-K8s destination (e.g. spark-medium) --
        # that IS the engine sort/z-order rewrites need, so use it directly instead of requiring
        # a separate sql_fallback_destination_id. Regression, 2026-08-11:
        # silver_prospect_activity_base's effective preset (spark-medium) routes straight to
        # the spark-k8s-fallback destination
        # with no fallback config of its own (Spark is not a fallback here, it's the primary
        # engine), so the old fallback-id-only check rejected every Spark-primary table with a
        # 422 even though nothing else was actually missing.
        fallback_destination = destination
    else:
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
        maybe_fallback_destination = session.get(Destination, uuid.UUID(str(fallback_id)))
        if maybe_fallback_destination is None:
            raise HTTPException(
                status_code=422, detail=f"Configured fallback destination {fallback_id} not found"
            )
        fallback_destination = maybe_fallback_destination
    source = session.get(Source, table.source_id)
    if source is None:
        raise HTTPException(status_code=422, detail="Table's source not found")
    catalog_name = (source.config or {}).get("catalog_name")
    if not isinstance(catalog_name, str) or not catalog_name:
        raise HTTPException(status_code=422, detail="Table's source has no catalog_name configured")
    table_ref = f"{catalog_name}.{table.schema_name}.{table.table_name}"
    if stats is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "Layout rewrite requires current scan size evidence before it can be dispatched."
            ),
        )
    scan_stats = {**dict(stats.raw or {}), "total_size_gb": stats.total_size_gb}
    op_params: dict[str, Any] = {
        "strategy": recommendation.recommended_strategy,
        "rewrite_all": True,
        "persist_sort_order": recommendation.recommended_strategy == "sort",
    }
    for key in ("chunk_size_gb", "streaming_aware_cadence"):
        value = effective.preset.params.get(key)
        if value is not None:
            op_params[key] = value
    if recommendation.recommended_strategy == "zorder":
        op_params["zorder_columns"] = columns
    else:
        op_params["sort_columns"] = columns
    if not full_table_optimize_allowed(scan_stats, op_params):
        raise HTTPException(
            status_code=422,
            detail="Layout rewrite is outside the effective chunk size budget for this table.",
        )

    if excluded_table_ids(session, [table.id]):
        raise HTTPException(
            status_code=409,
            detail="This table is excluded from maintenance. Include it on the Runs page first.",
        )

    run = MaintenanceRun(
        kind="adhoc",
        preset_id=None,
        trigger="layout-recommendation-apply",
        status="running",
        summary=correlation_summary(
            {
                "actor": principal.subject,
                "layout_recommendation_id": str(recommendation.id),
            }
        ),
    )
    session.add(run)
    session.flush()
    job = MaintenanceJob(
        run_id=run.id,
        table_id=table.id,
        destination_id=fallback_destination.id,
        mode=payload.mode,
        status="running",
        started_at=datetime.now(UTC),
    )
    session.add(job)
    session.flush()

    plan = MaintenancePlan(
        table_ref=table_ref,
        mode=Mode(payload.mode),
        operations=(Operation(OperationType.OPTIMIZE, op_params),),
    )
    settings = get_settings()
    bound_intent = None
    if settings.durable_intents_enabled:
        bound = bind_explicit_job_intent(
            session,
            job_id=str(job.id),
            plan=plan,
            settings=settings,
            actor_type="layout-recommendation",
            actor_id=principal.subject,
            policy_id=str(recommendation.id),
        )
        bound_intent = bound.intent
        session.commit()

    merged_config = _fallback_engine_config(fallback_destination.config, source.config)
    engine = SparkK8sEngine(config=merged_config).with_table_stats(scan_stats)
    intent_store = (
        IntentStore(
            sessionmaker(
                bind=session.get_bind(),
                autoflush=False,
                expire_on_commit=False,
            )
        )
        if bound_intent is not None
        else None
    )
    intent_record = (
        intent_store.get(str(bound_intent.id))
        if intent_store is not None and bound_intent is not None
        else None
    )
    if intent_store is not None and intent_record is None:
        raise RuntimeError("bound layout maintenance intent could not be reloaded")
    attempt = (
        intent_store.begin_attempt(
            str(intent_record.id),
            worker_id=f"api:{principal.subject}",
        )
        if intent_store is not None and intent_record is not None
        else None
    )
    receipt = None
    if intent_store is not None and attempt is not None and intent_record is not None:
        requirement = intent_record.capability_requirements[0]
        receipt = intent_store.prepare_effect(
            intent_id=str(intent_record.id),
            attempt_id=str(attempt.id),
            step_index=0,
            operation=OperationType.OPTIMIZE,
            strategy=requirement.effect_strategy,
            destination_id=str(fallback_destination.id),
        )
        intent_store.mark_effect_submitted(
            str(receipt.id),
            submission={
                "operation": OperationType.OPTIMIZE.value,
                "execution": "layout_recommendation",
                "recommendation_id": str(recommendation.id),
                "continuation": "manual_review",
            },
        )

    def _track_effect_reference(reference: str) -> None:
        job.engine_ref = reference
        if intent_store is not None and receipt is not None:
            intent_store.set_effect_external_reference(str(receipt.id), reference)

    try:
        results = engine.execute_plan(
            plan,
            ExecCtx(
                job_id=job.id,
                table_id=table.id,
                run_id=str(run.id),
                on_engine_ref=_track_effect_reference,
            ),
        )
    except Exception as exc:
        if intent_store is not None and attempt is not None and receipt is not None:
            intent_store.resolve_effect(
                str(receipt.id),
                EffectState.UNKNOWN,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            intent_store.finish_attempt(
                str(attempt.id),
                AttemptStatus.RECONCILE_REQUIRED,
                error={"message": str(exc)},
            )
            job.status = "partial"
            job.error = f"effect_unknown:{exc}"
            job.finished_at = datetime.now(UTC)
            run.status = "partial"
            run.finished_at = datetime.now(UTC)
            session.commit()
        raise HTTPException(
            status_code=502,
            detail=f"Layout rewrite outcome is unknown and requires reconciliation (run {run.id})",
        ) from exc
    rewrite_result = results[0] if results else None
    if (
        intent_store is not None
        and receipt is not None
        and rewrite_result is not None
        and rewrite_result.engine_ref
    ):
        intent_store.set_effect_external_reference(
            str(receipt.id),
            rewrite_result.engine_ref,
        )

    if rewrite_result is None or not rewrite_result.ok:
        error = rewrite_result.error if rewrite_result else "No result from Spark dispatch"
        outcome_unknown = rewrite_result is None or not rewrite_result.outcome_known
        if intent_store is not None and attempt is not None and receipt is not None:
            intent_store.resolve_effect(
                str(receipt.id),
                EffectState.UNKNOWN if outcome_unknown else EffectState.FAILED,
                error={"message": error},
            )
            intent_store.finish_attempt(
                str(attempt.id),
                (AttemptStatus.RECONCILE_REQUIRED if outcome_unknown else AttemptStatus.FAILED),
                error={"message": error},
            )
        job.status = "partial" if outcome_unknown else "failed"
        job.error = error
        job.engine_ref = rewrite_result.engine_ref if rewrite_result else None
        job.finished_at = datetime.now(UTC)
        run.status = "partial" if outcome_unknown else "failed"
        run.finished_at = datetime.now(UTC)
        session.commit()
        raise HTTPException(
            status_code=502,
            detail=f"{recommendation.recommended_strategy} rewrite failed: {error} (run {run.id})",
        )

    if intent_store is not None and receipt is not None:
        intent_store.resolve_effect(
            str(receipt.id),
            EffectState.SUCCEEDED,
            result=_layout_effect_result(rewrite_result),
        )
        assert intent_record is not None
        budget_error = _layout_budget_error(intent_store, intent_record)
        if budget_error is not None:
            assert attempt is not None
            intent_store.finish_attempt(
                str(attempt.id),
                AttemptStatus.FAILED,
                error={"message": budget_error},
            )
            job.status = "partial"
            job.error = budget_error
            job.engine_ref = rewrite_result.engine_ref
            job.finished_at = datetime.now(UTC)
            run.status = "partial"
            run.finished_at = datetime.now(UTC)
            session.commit()
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Layout rewrite completed but exceeded its bound effect budget: "
                    f"{budget_error} (run {run.id})"
                ),
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
        persist_receipt = None
        if intent_store is not None and attempt is not None and intent_record is not None:
            requirement = next(
                item
                for item in intent_record.capability_requirements
                if item.executes_effect
                and item.operation is OperationType.OPTIMIZE
                and item.destination_id == fallback_destination.id
            )
            persist_receipt = intent_store.prepare_effect(
                intent_id=str(intent_record.id),
                attempt_id=str(attempt.id),
                step_index=1,
                operation=OperationType.OPTIMIZE,
                strategy=requirement.effect_strategy,
                destination_id=str(fallback_destination.id),
            )
            intent_store.mark_effect_submitted(
                str(persist_receipt.id),
                submission={
                    "operation": OperationType.OPTIMIZE.value,
                    "execution": "persist_sort_order",
                    "recommendation_id": str(recommendation.id),
                    "continuation": "manual_review",
                },
            )

        def _track_persist_reference(reference: str) -> None:
            if intent_store is not None and persist_receipt is not None:
                intent_store.set_effect_external_reference(
                    str(persist_receipt.id),
                    reference,
                )

        try:
            alter_result = engine.execute(
                alter_stmt,
                ExecCtx(
                    job_id=job.id,
                    table_id=table.id,
                    run_id=str(run.id),
                    statement_index=1,
                    on_engine_ref=_track_persist_reference,
                ),
            )
        except Exception as exc:
            if intent_store is not None and persist_receipt is not None:
                intent_store.resolve_effect(
                    str(persist_receipt.id),
                    EffectState.UNKNOWN,
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
                assert attempt is not None
                intent_store.finish_attempt(
                    str(attempt.id),
                    AttemptStatus.RECONCILE_REQUIRED,
                    error={"message": str(exc)},
                )
            job.status = "partial"
            job.error = f"effect_unknown:{exc}"
            job.finished_at = datetime.now(UTC)
            run.status = "partial"
            run.finished_at = datetime.now(UTC)
            session.commit()
            raise HTTPException(
                status_code=502,
                detail=(
                    "Sort-order persistence outcome is unknown and requires "
                    f"reconciliation (run {run.id})"
                ),
            ) from exc

        if intent_store is not None and persist_receipt is not None and alter_result.engine_ref:
            intent_store.set_effect_external_reference(
                str(persist_receipt.id),
                alter_result.engine_ref,
            )
        if (
            not alter_result.ok
            and not alter_result.outcome_known
            and intent_store is not None
            and persist_receipt is not None
        ):
            intent_store.resolve_effect(
                str(persist_receipt.id),
                EffectState.UNKNOWN,
                error={"message": alter_result.error or "sort-order outcome unknown"},
            )
            assert attempt is not None
            intent_store.finish_attempt(
                str(attempt.id),
                AttemptStatus.RECONCILE_REQUIRED,
                error={"message": alter_result.error or "sort-order outcome unknown"},
            )
            job.status = "partial"
            job.error = f"effect_unknown:{alter_result.error or 'sort-order outcome unknown'}"
            job.engine_ref = alter_result.engine_ref or rewrite_result.engine_ref
            job.finished_at = datetime.now(UTC)
            run.status = "partial"
            run.finished_at = datetime.now(UTC)
            session.commit()
            raise HTTPException(
                status_code=502,
                detail=(
                    "Sort-order persistence outcome is unknown and requires "
                    f"reconciliation (run {run.id})"
                ),
            )
        persisted_sort_order = alter_result.ok
        if intent_store is not None and persist_receipt is not None:
            intent_store.resolve_effect(
                str(persist_receipt.id),
                (EffectState.SUCCEEDED if persisted_sort_order else EffectState.FAILED),
                result=(_layout_effect_result(alter_result) if persisted_sort_order else None),
                error=(
                    None
                    if persisted_sort_order
                    else {"message": alter_result.error or "sort-order persistence failed"}
                ),
            )
        if not alter_result.ok:
            persist_error = alter_result.error
            logger.warning(
                "layout recommendation %s: rewrite succeeded but persisting write.sort-order "
                "failed: %s",
                recommendation.id,
                persist_error,
            )
        elif intent_store is not None and intent_record is not None:
            budget_error = _layout_budget_error(intent_store, intent_record)
            if budget_error is not None:
                assert attempt is not None
                intent_store.finish_attempt(
                    str(attempt.id),
                    AttemptStatus.FAILED,
                    error={"message": budget_error},
                )
                job.status = "partial"
                job.error = budget_error
                job.engine_ref = alter_result.engine_ref or rewrite_result.engine_ref
                job.finished_at = datetime.now(UTC)
                run.status = "partial"
                run.finished_at = datetime.now(UTC)
                session.commit()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Sort-order persistence completed but exceeded its bound effect "
                        f"budget: {budget_error} (run {run.id})"
                    ),
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
    if bound_intent is None:
        job.plan = {
            "table_ref": table_ref,
            "mode": payload.mode,
            "operations": [{"type": "optimize", "params": op_params}],
            "notes": notes,
        }
    else:
        run.summary = {**(run.summary or {}), "layout_notes": notes}
    run.status = "succeeded"
    run.finished_at = datetime.now(UTC)

    recommendation.status = "applied"
    recommendation.applied_at = datetime.now(UTC)
    recommendation.applied_run_id = run.id
    # Freeze the real "before" baseline now, at the moment the rewrite is actually dispatched --
    # current_prune_efficiency itself is not safe to read again later: it gets overwritten in
    # place by the next generate_layout_recommendation regeneration tick (no status check on
    # that update path), which would otherwise silently clobber the true pre-apply value before
    # anything gets a chance to measure the real before/after improvement. See
    # measure_layout_recommendation_effectiveness (layout_analyzer.py) for the consumer of this
    # field and  for why this replaced the previous baseline_scan_bytes write here (that
    # field stored a dollar figure -- avg TableCost.query_request_savings_usd_month -- multiplied
    # by 1024*1024 into a column named "_bytes"; a real unit-confusion bug, not a
    # pending-but-correct placeholder, and nothing ever wrote its post_apply_scan_bytes
    # counterpart anyway).
    recommendation.applied_prune_efficiency_baseline = recommendation.current_prune_efficiency
    if intent_store is not None and attempt is not None:
        intent_store.finish_attempt(str(attempt.id), AttemptStatus.SUCCEEDED)
    session.commit()

    return MaintenanceRunResponse(run_id=run.id, job_ids=[job.id], status=run.status)


def _scoped_recommendation(
    session: Session, recommendation_id: uuid.UUID
) -> tuple[LayoutRecommendation, Table]:
    """Load a recommendation and its table, 404 when either is missing or the table belongs
    to another tenant (so another tenant's recommendation IDs are indistinguishable from
    unknown ones)."""
    recommendation = session.get(LayoutRecommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    table = get_scoped_resource(
        session, Table, recommendation.table_id, resource_type="Recommendation"
    )
    return recommendation, table


def _layout_effect_result(result: ExecResult) -> dict[str, Any]:
    return {
        "engine_ref": result.engine_ref,
        "detail": result.detail,
        "duration_ms": result.duration_ms,
        "output_files_count": getattr(result, "output_files_count", None),
        "actual_bytes_written": getattr(result, "actual_bytes_written", None),
        "objects_deleted": getattr(result, "objects_deleted", None),
    }


def _layout_budget_error(
    intent_store: IntentStore,
    intent: MaintenanceIntentRecord,
) -> str | None:
    try:
        validate_effect_budget(
            intent,
            intent_store.budget_usage(str(intent.id)),
        )
    except IntentValidationError as exc:
        return f"intent_budget_exceeded:{exc.code}:{exc.detail}"
    return None


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
