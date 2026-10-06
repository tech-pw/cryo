"""API routes for dashboard features: query performance, health trends, SLA, cost attribution."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session, contains_eager, selectinload

from cryo.api.deps import SessionDep, require_viewer
from cryo.api.sla import sla_from_guardrails
from cryo.config import get_settings
from cryo.db.model_utils import qualified_table_name
from cryo.db.models import (
    AuditLog,
    JobStep,
    MaintenanceJob,
    QueryLog,
    Source,
    Table,
    TableCostHistory,
    TableStats,
    TableStatsHistory,
)
from cryo.db.repositories import presets as preset_repo
from cryo.db.tenant_scope import get_scoped_resource, require_tenant, scope_by_tenant, scoped_select
from cryo.reporting.maintenance_coverage import coverage_to_payload, measure_cohort_coverage
from cryo.reporting.vitals import compute_cryo_vitals, cryo_vitals_to_payload
from cryo.scheduler.rules import effective_preset
from cryo.tiering.s3_analyzer import analyze_table_for_tiering, get_all_tiering_recommendations

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


def _raw_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _raw_int(value: object, *keys: str) -> int | None:
    raw = _raw_dict(value)
    for key in keys:
        raw_value = raw.get(key)
        if isinstance(raw_value, int):
            return raw_value
        if isinstance(raw_value, float):
            return int(raw_value)
    return None


def _raw_float(value: object, *keys: str) -> float | None:
    raw = _raw_dict(value)
    for key in keys:
        raw_value = raw.get(key)
        if isinstance(raw_value, int | float):
            return float(raw_value)
    return None


def _raw_datetime(value: object, *keys: str) -> datetime | None:
    raw = _raw_dict(value)
    for key in keys:
        raw_value = raw.get(key)
        if isinstance(raw_value, datetime):
            return raw_value if raw_value.tzinfo else raw_value.replace(tzinfo=UTC)
        if isinstance(raw_value, str):
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            except ValueError:
                continue
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _query_status(query: QueryLog) -> str:
    raw = _raw_dict(query.raw)
    status = raw.get("status") or raw.get("state")
    return str(status) if status else "unknown"


# ===================== Multi-Catalog Federation =====================


class CatalogStatsRead(BaseModel):
    """Statistics for a single catalog."""

    catalog_id: UUID
    catalog_name: str
    catalog_type: str
    table_count: int
    total_size_gb: float
    small_files_count: int
    needs_compaction_count: int
    monthly_cost_usd: float
    projected_savings_usd_month: float | None
    avg_health_score: float
    enabled: bool


class MultiCatalogResponse(BaseModel):
    """Response for multi-catalog federation dashboard."""

    catalogs: list[CatalogStatsRead]
    total_catalogs: int
    total_tables: int
    total_cost_usd: float


@router.get("/multi-catalog", response_model=MultiCatalogResponse, tags=["dashboards"])
def get_multi_catalog_stats(
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
) -> MultiCatalogResponse:
    """Get aggregated statistics across all catalogs for federation view."""
    tenant_id = require_tenant(session)
    sources_stmt = scoped_select(session, Source).where(Source.enabled == True)  # noqa: E712
    sources = session.execute(sources_stmt).scalars().all()

    catalogs: list[CatalogStatsRead] = []
    total_tables = 0
    total_cost_usd = 0.0

    for source in sources:
        # Aggregate table stats for this catalog
        stats_stmt = (
            select(
                func.count(Table.id).label("table_count"),
                func.sum(TableStats.total_size_gb).label("total_size_gb"),
                func.sum(TableStats.small_files_count).label("small_files_count"),
                func.sum(
                    case(
                        (TableStats.needs_compaction.is_(True), 1),
                        else_=0,
                    )
                ).label("needs_compaction"),
                func.sum(TableStats.estimated_monthly_s3_cost_usd).label("monthly_cost_usd"),
                func.sum(TableStats.projected_savings_usd_month).label("projected_savings"),
                func.avg(TableStats.overall_score).label("avg_score"),
            )
            .join(TableStats, Table.id == TableStats.table_id)
            .where(Table.source_id == source.id)
        )
        stats_stmt = scope_by_tenant(stats_stmt, Table, tenant_id)

        agg = session.execute(stats_stmt).one_or_none()

        if agg and agg.table_count:
            table_count = int(agg.table_count or 0)
            total_size_gb = float(agg.total_size_gb or 0.0)
            small_files = int(agg.small_files_count or 0)
            needs_compaction = int(agg.needs_compaction or 0)
            monthly_cost = float(agg.monthly_cost_usd or 0.0)
            projected_savings = (
                float(agg.projected_savings or 0.0) if agg.projected_savings else None
            )
            avg_score = float(agg.avg_score or 0.0)

            catalogs.append(
                CatalogStatsRead(
                    catalog_id=source.id,
                    catalog_name=source.name,
                    catalog_type=source.type,
                    table_count=table_count,
                    total_size_gb=total_size_gb,
                    small_files_count=small_files,
                    needs_compaction_count=needs_compaction,
                    monthly_cost_usd=monthly_cost,
                    projected_savings_usd_month=projected_savings,
                    avg_health_score=avg_score,
                    enabled=source.enabled,
                )
            )

            total_tables += table_count
            total_cost_usd += monthly_cost

    return MultiCatalogResponse(
        catalogs=catalogs,
        total_catalogs=len(catalogs),
        total_tables=total_tables,
        total_cost_usd=total_cost_usd,
    )


# ===================== Schemas =====================


class QueryPerformanceRead(BaseModel):
    """Query performance metrics."""

    query_id: str
    query_text: str | None
    engine: str
    user: str | None
    duration_ms: int
    bytes_scanned: int | None
    rows_produced: int | None
    status: str
    started_at: datetime
    table_name: str | None
    table_id: UUID | None = None


class QueryPerformanceResponse(BaseModel):
    """Response for query performance dashboard."""

    queries: list[QueryPerformanceRead]
    total: int
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    p99_latency_ms: float | None
    avg_bytes_scanned: float | None
    failed_count: int


class TableHealthTrendPoint(BaseModel):
    """Single data point in table health trend."""

    timestamp: datetime
    grade: str
    score: float
    small_files: int
    total_size_gb: float
    snapshot_count: int | None
    projected_savings_usd_month: float | None


class TableHealthTrendsResponse(BaseModel):
    """Response for table health trends."""

    table_id: UUID
    table_name: str
    current_grade: str
    current_score: float
    trend_points: list[TableHealthTrendPoint]
    degradation_rate: float | None  # Score change per day
    days_to_critical: int | None  # Days until grade F if trend continues


class JobDetailRead(BaseModel):
    """Detailed maintenance job information."""

    job_id: UUID
    run_id: UUID
    table_id: UUID
    table_name: str
    operation_type: str
    status: str
    engine: str
    replica: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None
    input_files_count: int | None
    output_files_count: int | None
    effectiveness_ratio: float | None
    error_message: str | None
    sql_statements: list[str] | None
    partition_predicates: list[str] | None
    force_admit: bool = False


class SLAStatusRead(BaseModel):
    """SLA status for a table."""

    table_id: UUID
    table_name: str
    schema_name: str
    freshness_hours: float | None  # Hours since last Iceberg snapshot commit
    freshness_sla_hours: int | None
    freshness_breached: bool
    scan_staleness_hours: float | None  # Hours since Cryo last scanned (observability only)
    maintenance_days: float | None  # Days since last maintenance
    maintenance_sla_days: int | None
    maintenance_breached: bool
    p95_latency_ms: float | None
    p95_latency_sla_ms: int | None
    p95_latency_breached: bool
    # False when no preset assignment/rule matches this table (see effective_preset()) --
    # its SLA guardrails are only ever the hardcoded defaults, and its maintenance SLA can
    # never actually be met since nothing will ever run against it automatically.
    has_preset: bool


class SLAResponse(BaseModel):
    """Response for SLA monitoring."""

    slas: list[SLAStatusRead]
    total_breached: int
    total_monitored: int


class CostAttributionRead(BaseModel):
    """Cost attribution by team/department."""

    cost_center: str | None
    department: str | None
    storage_cost_usd: float
    compute_cost_usd: float  # Estimated from query logs
    maintenance_cost_usd: float  # Estimated from maintenance job duration
    total_cost_usd: float
    table_count: int
    month: str


class CostAttributionResponse(BaseModel):
    """Response for cost attribution dashboard."""

    attributions: list[CostAttributionRead]
    total_cost_usd: float
    unattributed_cost_usd: float
    month: str


class FleetHealthTrendPoint(BaseModel):
    """Fleet-wide daily health aggregate."""

    day: date
    avg_health_score: float
    tables_at_grade_df: int
    is_partial: bool
    tables_counted: int


class FleetCostTrendPoint(BaseModel):
    """Fleet-wide daily cost aggregate."""

    day: date
    total_monthly_cost_usd: float
    projected_savings_usd_month: float
    is_partial: bool
    tables_counted: int


class FleetTrendsResponse(BaseModel):
    """Response for fleet health and cost trend charts."""

    days: int
    health_trend: list[FleetHealthTrendPoint]
    cost_trend: list[FleetCostTrendPoint]


def _scan_day_bucket(session: Session, column: Any) -> Any:
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return func.date_trunc("day", column)
    return func.date(column)


def _as_trend_day(day_value: object) -> date:
    if isinstance(day_value, datetime):
        return day_value.date()
    if isinstance(day_value, date):
        return day_value
    return date.fromisoformat(str(day_value)[:10])


def _latest_rows_per_table_day(
    session: Session,
    history: Any,
    value_columns: tuple[Any, ...],
    *,
    since: datetime,
    tenant_id: UUID,
) -> Any:
    scan_at = history.scan_at
    selected = [
        _scan_day_bucket(session, scan_at).label("day"),
        history.table_id.label("table_id"),
        *[column.label(column.key) for column in value_columns],
        func.row_number()
        .over(
            partition_by=(history.table_id, _scan_day_bucket(session, scan_at)),
            order_by=(scan_at.desc(), history.id.desc()),
        )
        .label("rn"),
    ]
    ranked = select(*selected).join(Table, history.table_id == Table.id).where(scan_at >= since)
    return scope_by_tenant(ranked, Table, tenant_id).subquery()


def aggregate_fleet_health_trend(
    session: Session,
    *,
    since: datetime,
    tenant_id: UUID,
) -> list[FleetHealthTrendPoint]:
    latest = _latest_rows_per_table_day(
        session,
        TableStatsHistory,
        (TableStatsHistory.overall_score, TableStatsHistory.grade),
        since=since,
        tenant_id=tenant_id,
    )
    stmt = (
        select(
            latest.c.day,
            func.avg(latest.c.overall_score).label("avg_score"),
            func.sum(
                case(
                    (latest.c.grade.in_(("D", "F")), 1),
                    else_=0,
                )
            ).label("df_count"),
            func.count(latest.c.table_id).label("tables_counted"),
        )
        .where(latest.c.rn == 1)
        .group_by(latest.c.day)
        .order_by(latest.c.day)
    )
    rows = session.execute(stmt).all()
    today = datetime.now(UTC).date()
    points: list[FleetHealthTrendPoint] = []
    for day_value, avg_score, df_count, tables_counted in rows:
        if day_value is None:
            continue
        day = _as_trend_day(day_value)
        points.append(
            FleetHealthTrendPoint(
                day=day,
                avg_health_score=round(float(avg_score or 0.0), 2),
                tables_at_grade_df=int(df_count or 0),
                is_partial=day == today,
                tables_counted=int(tables_counted or 0),
            )
        )
    return points


def aggregate_fleet_cost_trend(
    session: Session,
    *,
    since: datetime,
    tenant_id: UUID,
) -> list[FleetCostTrendPoint]:
    latest = _latest_rows_per_table_day(
        session,
        TableCostHistory,
        (
            TableCostHistory.current_monthly_cost_usd,
            TableCostHistory.projected_savings_usd_month,
        ),
        since=since,
        tenant_id=tenant_id,
    )
    stmt = (
        select(
            latest.c.day,
            func.coalesce(func.sum(latest.c.current_monthly_cost_usd), 0.0).label("total_cost"),
            func.coalesce(func.sum(latest.c.projected_savings_usd_month), 0.0).label(
                "total_savings"
            ),
            func.count(latest.c.table_id).label("tables_counted"),
        )
        .where(latest.c.rn == 1)
        .group_by(latest.c.day)
        .order_by(latest.c.day)
    )
    rows = session.execute(stmt).all()
    today = datetime.now(UTC).date()
    points: list[FleetCostTrendPoint] = []
    for day_value, total_cost, total_savings, tables_counted in rows:
        if day_value is None:
            continue
        day = _as_trend_day(day_value)
        points.append(
            FleetCostTrendPoint(
                day=day,
                total_monthly_cost_usd=round(float(total_cost or 0.0), 4),
                projected_savings_usd_month=round(float(total_savings or 0.0), 4),
                is_partial=day == today,
                tables_counted=int(tables_counted or 0),
            )
        )
    return points


# ===================== Endpoints =====================


@router.get("/dashboard/fleet-trends", response_model=FleetTrendsResponse, tags=["dashboards"])
def get_fleet_trends(
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
    days: Annotated[int, Query(description="Days of history to fetch", ge=1, le=90)] = 30,
) -> FleetTrendsResponse:
    """Aggregate fleet-wide health and cost trends by day."""
    tenant_id = require_tenant(session)
    since = datetime.now(UTC) - timedelta(days=days)
    return FleetTrendsResponse(
        days=days,
        health_trend=aggregate_fleet_health_trend(session, since=since, tenant_id=tenant_id),
        cost_trend=aggregate_fleet_cost_trend(session, since=since, tenant_id=tenant_id),
    )


@router.get("/query-performance", response_model=QueryPerformanceResponse, tags=["dashboards"])
def get_query_performance(
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
    table_id: Annotated[UUID | None, Query(description="Filter by table ID")] = None,
    engine: Annotated[
        str | None, Query(description="Filter by engine (trino, spark, etc.)")
    ] = None,
    hours: Annotated[int, Query(description="Hours of history to fetch", ge=1, le=168)] = 24,
    limit: Annotated[int, Query(description="Max queries to return", ge=1, le=1000)] = 100,
) -> QueryPerformanceResponse:
    """Get query performance metrics for the dashboard."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    tenant_id = require_tenant(session)

    stmt = (
        select(QueryLog)
        .join(Table, QueryLog.table_id == Table.id)
        .where(QueryLog.executed_at >= since)
    )
    stmt = scope_by_tenant(stmt, Table, tenant_id)

    if table_id:
        stmt = stmt.where(QueryLog.table_id == table_id)

    if engine:
        stmt = stmt.where(QueryLog.engine == engine)

    # Order by duration descending (slowest first)
    stmt = stmt.order_by(desc(QueryLog.duration_ms)).limit(limit)

    queries = session.execute(stmt).scalars().all()

    # Compute percentiles
    percentile_stmt = (
        select(
            func.percentile_cont(0.5).within_group(QueryLog.duration_ms).label("p50"),
            func.percentile_cont(0.95).within_group(QueryLog.duration_ms).label("p95"),
            func.percentile_cont(0.99).within_group(QueryLog.duration_ms).label("p99"),
            func.avg(QueryLog.bytes_read).label("avg_bytes"),
            func.count().label("total"),
        )
        .select_from(QueryLog)
        .join(Table, QueryLog.table_id == Table.id)
        .where(QueryLog.executed_at >= since)
    )
    percentile_stmt = scope_by_tenant(percentile_stmt, Table, tenant_id)

    if table_id:
        percentile_stmt = percentile_stmt.where(QueryLog.table_id == table_id)
    if engine:
        percentile_stmt = percentile_stmt.where(QueryLog.engine == engine)

    agg_result = session.execute(percentile_stmt).one()

    return QueryPerformanceResponse(
        queries=[
            QueryPerformanceRead(
                query_id=q.query_id,
                query_text=q.query_text[:500] if q.query_text else None,  # Truncate
                engine=q.engine,
                user=q.user_name,
                duration_ms=q.duration_ms or 0,
                bytes_scanned=q.bytes_read,
                rows_produced=q.rows_read,
                status=_query_status(q),
                started_at=q.executed_at,
                table_name=qualified_table_name(q.table) if q.table else None,
                table_id=q.table_id,
            )
            for q in queries
        ],
        total=int(agg_result.total or 0),
        p50_latency_ms=float(agg_result.p50) if agg_result.p50 is not None else None,
        p95_latency_ms=float(agg_result.p95) if agg_result.p95 is not None else None,
        p99_latency_ms=float(agg_result.p99) if agg_result.p99 is not None else None,
        avg_bytes_scanned=float(agg_result.avg_bytes) if agg_result.avg_bytes is not None else None,
        failed_count=sum(1 for q in queries if _query_status(q).lower() == "failed"),
    )


@router.get(
    "/tables/{table_id}/health-trends",
    response_model=TableHealthTrendsResponse,
    tags=["dashboards"],
)
def get_table_health_trends(
    table_id: UUID,
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
    days: Annotated[int, Query(description="Days of history to fetch", ge=1, le=90)] = 30,
) -> TableHealthTrendsResponse:
    """Get health trends for a table over time."""
    table = get_scoped_resource(session, Table, table_id, resource_type="Table")

    since = datetime.now(UTC) - timedelta(days=days)

    # Fetch historical stats
    stmt = (
        select(TableStatsHistory)
        .where(TableStatsHistory.table_id == table_id)
        .where(TableStatsHistory.scan_at >= since)
        .order_by(TableStatsHistory.scan_at)
    )

    history = session.execute(stmt).scalars().all()

    trend_points = [
        TableHealthTrendPoint(
            timestamp=h.scan_at,
            grade=h.grade or "F",
            score=float(h.overall_score or 0),
            small_files=int(h.small_files_count or 0),
            total_size_gb=float(h.total_size_gb or 0.0),
            snapshot_count=_raw_int(h.raw, "total_snapshots", "snapshot_count"),
            projected_savings_usd_month=_raw_float(h.raw, "projected_savings_usd_month"),
        )
        for h in history
    ]

    # Calculate degradation rate (score change per day)
    degradation_rate = None
    days_to_critical = None
    if len(trend_points) >= 2:
        first = trend_points[0]
        last = trend_points[-1]
        time_delta_days = (last.timestamp - first.timestamp).total_seconds() / 86400
        if time_delta_days > 0:
            score_delta = last.score - first.score
            degradation_rate = score_delta / time_delta_days

            # Estimate days to critical (score 0)
            if degradation_rate < 0 and last.score > 0:
                days_to_critical = int(last.score / abs(degradation_rate))

    # Get current stats
    current_stats = session.get(TableStats, table_id)
    current_grade = current_stats.grade if current_stats else "F"
    current_score = float(current_stats.overall_score) if current_stats else 0.0

    return TableHealthTrendsResponse(
        table_id=table_id,
        table_name=qualified_table_name(table),
        current_grade=current_grade,
        current_score=current_score,
        trend_points=trend_points,
        degradation_rate=degradation_rate,
        days_to_critical=days_to_critical,
    )


_NON_OPERATION_AUDIT_ACTIONS = frozenset({"force_admit"})
_STATEMENT_PREFIXES = ("select ", "alter ", "execute ", "call ", "optimize ", "with ", "native:")


def statements_from_detail(detail: str | None) -> list[str]:
    """Pull rendered SQL out of an audit or step detail.

    Workers store the statement as a plain string. Some writers nest a
    ``sql_statements`` list in JSON. Error text is not a statement.
    """
    if not detail or not detail.strip():
        return []
    text = detail.strip()
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        raw = parsed.get("sql_statements")
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        return []
    lowered = text.lower()
    if lowered.startswith(_STATEMENT_PREFIXES):
        return [text]
    return []


def collect_job_statements(
    audits: list[AuditLog],
    steps: list[JobStep],
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for step in sorted(steps, key=lambda item: item.step_index):
        for statement in statements_from_detail(step.detail):
            if statement not in seen:
                seen.add(statement)
                ordered.append(statement)
    for audit in sorted(audits, key=lambda item: item.ts or datetime.min.replace(tzinfo=UTC)):
        if audit.action in _NON_OPERATION_AUDIT_ACTIONS:
            continue
        for statement in statements_from_detail(audit.detail):
            if statement not in seen:
                seen.add(statement)
                ordered.append(statement)
    return ordered


def aggregate_job_file_counts(
    audits: list[AuditLog],
) -> tuple[int | None, int | None, float | None]:
    inputs = [
        audit.input_files_count
        for audit in audits
        if audit.action not in _NON_OPERATION_AUDIT_ACTIONS and audit.input_files_count is not None
    ]
    outputs = [
        audit.output_files_count
        for audit in audits
        if audit.action not in _NON_OPERATION_AUDIT_ACTIONS and audit.output_files_count is not None
    ]
    input_count = sum(inputs) if inputs else None
    output_count = sum(outputs) if outputs else None
    ratio = None
    if input_count is not None and output_count:
        ratio = float(input_count) / output_count
    return input_count, output_count, ratio


def job_duration_seconds(started_at: datetime | None, finished_at: datetime | None) -> float | None:
    if started_at is None or finished_at is None:
        return None
    return (finished_at - started_at).total_seconds()


def format_partition_predicate(predicate: object) -> str:
    if isinstance(predicate, str):
        return predicate
    if not isinstance(predicate, dict):
        return str(predicate)
    reason = predicate.get("unsupported_reason")
    if isinstance(reason, str) and reason:
        return reason
    terms = predicate.get("terms")
    if not isinstance(terms, list) or not terms:
        return "full table"
    parts: list[str] = []
    for term in terms:
        if not isinstance(term, dict):
            parts.append(str(term))
            continue
        column = term.get("partition_field") or term.get("base_column") or "partition"
        transform = term.get("transform")
        label = f"{transform}({column})" if transform and transform != "identity" else str(column)
        parts.append(f"{label} = {term.get('value')}")
    return " AND ".join(parts)


def partition_predicates_from_plan(plan: object) -> list[str] | None:
    if not isinstance(plan, dict):
        return None
    operations = plan.get("operations")
    if not isinstance(operations, list):
        return None
    predicates: list[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        raw = operation.get("predicates")
        if isinstance(raw, list):
            predicates.extend(format_partition_predicate(item) for item in raw)
    return predicates or None


def operation_label_from_plan(plan: object) -> str:
    if not isinstance(plan, dict):
        return "unknown"
    operations = plan.get("operations")
    if not isinstance(operations, list):
        return "unknown"
    types = [
        str(operation.get("type"))
        for operation in operations
        if isinstance(operation, dict) and operation.get("type")
    ]
    return ", ".join(types) if types else "unknown"


@router.get("/jobs/{job_id}/detail", response_model=JobDetailRead, tags=["runs"])
def get_job_detail(
    job_id: UUID,
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
) -> JobDetailRead:
    """Get detailed information about a maintenance job."""
    job = session.get(MaintenanceJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    get_scoped_resource(session, Table, job.table_id, resource_type="Table")

    audits = list(
        session.scalars(
            select(AuditLog).where(AuditLog.job_id == job.id).order_by(AuditLog.ts).limit(500)
        )
    )
    steps = list(
        session.scalars(
            select(JobStep).where(JobStep.job_id == job.id).order_by(JobStep.step_index).limit(500)
        )
    )

    table = session.get(Table, job.table_id)
    table_name = qualified_table_name(table) if table else str(job.table_id)
    input_files, output_files, effectiveness_ratio = aggregate_job_file_counts(audits)
    sql_statements = collect_job_statements(audits, steps) or None

    return JobDetailRead(
        job_id=job_id,
        run_id=job.run_id,
        table_id=job.table_id,
        table_name=table_name,
        operation_type=operation_label_from_plan(job.plan),
        status=job.status,
        engine=job.destination.engine if job.destination else "unknown",
        replica=job.replica,
        started_at=job.started_at,
        completed_at=job.finished_at,
        duration_seconds=job_duration_seconds(job.started_at, job.finished_at),
        input_files_count=input_files,
        output_files_count=output_files,
        effectiveness_ratio=effectiveness_ratio,
        # Previously suppressed unless status == FAILED, so a queued/deferred job's
        # "table_locked"/"cooldown"/etc reason (job.error is set for those statuses too)
        # never reached the UI -- the operator had no way to see why a run wasn't
        # progressing short of asking someone to check server-side state by hand.
        error_message=job.error,
        sql_statements=sql_statements,
        partition_predicates=partition_predicates_from_plan(job.plan),
        force_admit=job.force_admit,
    )


@router.get("/vitals", tags=["dashboards"])
def get_cryo_vitals(
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
) -> dict[str, Any]:
    """Cryo's own vitals: one 0-100 score + HEALTHY/DEGRADED/CRITICAL status answering "is
    everything in Cryo working as expected right now" -- the operational-health counterpart to
    /dashboard/fleet-trends' "how much good are we doing" north-star metric. Fleet-wide, not
    tenant-scoped, same as the health_check.py checklist it's built on (see
    cryo/reporting/vitals.py for the composition and scoring rationale)."""
    vitals = compute_cryo_vitals(session)
    return cryo_vitals_to_payload(vitals)


@router.get("/maintenance-coverage", tags=["dashboards"])
def get_maintenance_coverage(
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
    preset_name: Annotated[str | None, Query()] = None,
    window_hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> dict[str, Any]:
    """Per-operation maintenance coverage of a preset's pinned cohort.

    Answers "did the tables we promised to maintain actually get each of the four
    operations", which neither /vitals (is the machine healthy) nor
    /dashboard/fleet-trends (how much good are we doing fleet-wide) can: a job-level success
    rate cannot tell a table that got optimize but not expire_snapshots from one that got
    nothing. Defaults to the Trino-hot preset -- see cryo/reporting/maintenance_coverage.py.
    """
    resolved = preset_name or get_settings().trino_hot_preset_name
    coverage = measure_cohort_coverage(
        session,
        preset_name=resolved,
        window_hours=window_hours,
    )
    return coverage_to_payload(coverage)


@router.get("/sla-status", response_model=SLAResponse, tags=["dashboards"])
def get_sla_status(
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
    breached_only: Annotated[bool, Query(description="Show only breached SLAs")] = False,
    table_id: Annotated[UUID | None, Query(description="Filter to a single table")] = None,
) -> SLAResponse:
    """Get SLA status for all monitored tables, or a single table when table_id is given."""
    now = datetime.now(UTC)
    tenant_id = require_tenant(session)

    # contains_eager(Table.stats) reuses the TableStats row this query already joins and
    # fetches instead of lazy-loading table.stats again per table; selectinload(Table.source)
    # and selectinload(Table.cost) batch those lookups (needed by qualified_table_name() and
    # table_to_candidate() respectively) into one query each instead of one per table. Without
    # these, the loop below re-triggers exactly the per-table query pattern this function is
    # meant to fix.
    base_stmt = (
        select(Table)
        .join(TableStats)
        .where(Table.first_seen_at.isnot(None))
        .options(
            contains_eager(Table.stats),
            selectinload(Table.source),
            selectinload(Table.cost),
        )
    )
    if table_id is not None:
        base_stmt = base_stmt.where(Table.id == table_id)
    stmt: Any = (
        scope_by_tenant(cast(Any, base_stmt), Table, tenant_id)
        if tenant_id is not None
        else base_stmt
    )

    results = session.execute(stmt).all()

    # Aggregate in SQL, once, instead of once per table -- the per-table version of this
    # loop (resolve_effective_preset() re-querying all presets/rules/assignments, plus a
    # MAX(AuditLog.ts) and a percentile_cont(QueryLog) each) issued ~5 queries per table,
    # ~65k queries for the full fleet on every page load (and this page polls every 60s).
    # Same class of bug as dashboard_datalake's fleet-wide cost totals.
    presets, rules, assignments = preset_repo.load_resolution_inputs(session)

    last_maintenance_by_table: dict[UUID, datetime] = {
        table_id: ts
        for table_id, ts in session.execute(
            select(AuditLog.table_id, func.max(AuditLog.ts))
            .where(AuditLog.status == "succeeded")
            .group_by(AuditLog.table_id)
        )
        if table_id is not None and ts is not None
    }
    p95_by_table: dict[UUID, float] = {
        table_id: p95
        for table_id, p95 in session.execute(
            select(
                QueryLog.table_id,
                func.percentile_cont(0.95).within_group(QueryLog.duration_ms),
            )
            .where(QueryLog.executed_at >= now - timedelta(days=1))
            .group_by(QueryLog.table_id)
        )
        if table_id is not None and p95 is not None
    }

    slas: list[SLAStatusRead] = []

    for (table,) in results:
        stats = table.stats
        assert stats is not None  # guaranteed by the inner join in base_stmt
        effective = effective_preset(
            preset_repo.table_to_candidate(table), presets, rules, assignments
        )
        guardrails = dict(effective.preset.guardrails) if effective and effective.preset else {}
        sla = sla_from_guardrails(guardrails)
        freshness_sla_hours = sla.freshness_hours
        maintenance_sla_days = sla.maintenance_days
        p95_latency_sla_ms = sla.p95_latency_ms

        scan_staleness_hours = (now - stats.scan_at).total_seconds() / 3600
        last_commit = _raw_datetime(stats.raw, "last_snapshot_committed_at")
        if last_commit is not None:
            freshness_hours = (now - last_commit).total_seconds() / 3600
        else:
            freshness_hours = scan_staleness_hours

        # Calculate maintenance recency
        maintenance_days = None
        last_maintenance = last_maintenance_by_table.get(table.id)
        if last_maintenance:
            maintenance_days = (now - last_maintenance).total_seconds() / 86400

        p95_latency_ms = p95_by_table.get(table.id)

        # Check breaches
        freshness_breached = freshness_hours is not None and freshness_hours > freshness_sla_hours
        maintenance_breached = (
            maintenance_days is not None and maintenance_days > maintenance_sla_days
        )
        p95_breached = p95_latency_ms is not None and p95_latency_ms > p95_latency_sla_ms

        if breached_only and not (freshness_breached or maintenance_breached or p95_breached):
            continue

        slas.append(
            SLAStatusRead(
                table_id=table.id,
                table_name=qualified_table_name(table),
                schema_name=table.schema_name,
                freshness_hours=freshness_hours,
                freshness_sla_hours=freshness_sla_hours,
                freshness_breached=freshness_breached,
                scan_staleness_hours=scan_staleness_hours,
                maintenance_days=maintenance_days,
                maintenance_sla_days=maintenance_sla_days,
                maintenance_breached=maintenance_breached,
                p95_latency_ms=p95_latency_ms,
                p95_latency_sla_ms=p95_latency_sla_ms,
                p95_latency_breached=p95_breached,
                has_preset=effective is not None,
            )
        )

    total_breached = sum(
        1
        for sla in slas
        if sla.freshness_breached or sla.maintenance_breached or sla.p95_latency_breached
    )

    return SLAResponse(
        slas=slas,
        total_breached=total_breached,
        total_monitored=len(slas),
    )


@router.get(
    "/dashboards/cost-attribution", response_model=CostAttributionResponse, tags=["dashboards"]
)
def get_cost_attribution(
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
    month: Annotated[
        str | None,
        Query(description="Month in YYYY-MM format (default: current month)"),
    ] = None,
) -> CostAttributionResponse:
    """Team/department cost stub.

    Lives off ``/cost-attribution`` so it cannot shadow the scan-cost report at
    ``GET /api/v1/cost-attribution``.
    """
    if month:
        try:
            year, month_num = map(int, month.split("-"))
            datetime(year, month_num, 1, tzinfo=UTC)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid month format, use YYYY-MM",
            ) from None
    else:
        now = datetime.now(UTC)
        month = f"{now.year}-{now.month:02d}"

    # TODO: This is a placeholder implementation
    # Real implementation needs:
    # 1. Custom tags on Source/Connection models (cost_center, department)
    # 2. Join tables to their source/connection
    # 3. Compute costs from query duration × engine hourly cost
    # 4. Maintenance costs from AuditLog duration × engine hourly cost

    attributions: list[CostAttributionRead] = []

    # For now, return stub data showing the structure
    # In production, this would query from properly tagged tables

    return CostAttributionResponse(
        attributions=attributions,
        total_cost_usd=0.0,
        unattributed_cost_usd=0.0,
        month=month,
    )


# ===================== S3 Intelligent-Tiering =====================


class S3TieringRecommendationRead(BaseModel):
    """S3 Intelligent-Tiering recommendation."""

    table_id: UUID
    table_name: str
    current_size_gb: float
    avg_file_size_kb: float
    days_since_last_access: int | None
    current_monthly_cost_usd: float
    intelligent_tiering_monthly_cost_usd: float
    estimated_annual_savings_usd: float
    recommendation: str
    reason: str


class S3TieringResponse(BaseModel):
    """Response for S3 Intelligent-Tiering recommendations."""

    recommendations: list[S3TieringRecommendationRead]
    total_potential_savings_usd: float


@router.get("/s3-tiering-recommendations", response_model=S3TieringResponse, tags=["dashboards"])
def get_s3_tiering_recommendations(
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
    min_size_gb: Annotated[float, Query(description="Minimum table size in GB")] = 100.0,
    lookback_days: Annotated[
        int,
        Query(description="Days to look back for access patterns"),
    ] = 90,
) -> S3TieringResponse:
    """
    Get S3 Intelligent-Tiering recommendations for all large tables.

    Returns tables that would benefit from enabling S3 Intelligent-Tiering,
    sorted by estimated annual savings.
    """
    recommendations = get_all_tiering_recommendations(
        session=session,
        min_size_gb=min_size_gb,
        lookback_days=lookback_days,
    )

    total_savings = sum(r.estimated_annual_savings_usd for r in recommendations)

    return S3TieringResponse(
        recommendations=[
            S3TieringRecommendationRead(
                table_id=UUID(r.table_id),
                table_name=r.table_name,
                current_size_gb=r.current_size_gb,
                avg_file_size_kb=r.avg_file_size_kb,
                days_since_last_access=r.days_since_last_access,
                current_monthly_cost_usd=r.current_monthly_cost_usd,
                intelligent_tiering_monthly_cost_usd=r.intelligent_tiering_monthly_cost_usd,
                estimated_annual_savings_usd=r.estimated_annual_savings_usd,
                recommendation=r.recommendation,
                reason=r.reason,
            )
            for r in recommendations
        ],
        total_potential_savings_usd=total_savings,
    )


@router.get(
    "/tables/{table_id}/s3-tiering",
    response_model=S3TieringRecommendationRead,
    tags=["tables"],
)
def get_table_s3_tiering(
    table_id: UUID,
    session: SessionDep,
    _principal: Annotated[dict, Depends(require_viewer)],
    lookback_days: Annotated[
        int,
        Query(description="Days to look back for access patterns"),
    ] = 90,
) -> S3TieringRecommendationRead:
    """
    Get S3 Intelligent-Tiering recommendation for a specific table.
    """
    get_scoped_resource(session, Table, table_id, resource_type="Table")
    rec = analyze_table_for_tiering(
        session=session,
        table_id=str(table_id),
        lookback_days=lookback_days,
    )

    return S3TieringRecommendationRead(
        table_id=UUID(rec.table_id),
        table_name=rec.table_name,
        current_size_gb=rec.current_size_gb,
        avg_file_size_kb=rec.avg_file_size_kb,
        days_since_last_access=rec.days_since_last_access,
        current_monthly_cost_usd=rec.current_monthly_cost_usd,
        intelligent_tiering_monthly_cost_usd=rec.intelligent_tiering_monthly_cost_usd,
        estimated_annual_savings_usd=rec.estimated_annual_savings_usd,
        recommendation=rec.recommendation,
        reason=rec.reason,
    )
