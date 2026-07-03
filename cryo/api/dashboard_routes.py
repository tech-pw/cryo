"""API routes for dashboard features: query performance, health trends, SLA, cost attribution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from cryo.api.deps import require_viewer
from cryo.db.models import AuditLog, QueryLog, QueryPattern, Table, TablePartition, TableStats, TableStatsHistory
from cryo.db.session import get_session
from cryo.domain.enums import JobStatus

router = APIRouter(prefix="/api", tags=["dashboard"])


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


class SLAStatusRead(BaseModel):
    """SLA status for a table."""

    table_id: UUID
    table_name: str
    freshness_hours: float | None  # Hours since last data update
    freshness_sla_hours: int | None
    freshness_breached: bool
    maintenance_days: float | None  # Days since last maintenance
    maintenance_sla_days: int | None
    maintenance_breached: bool
    p95_latency_ms: float | None
    p95_latency_sla_ms: int | None
    p95_latency_breached: bool


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


# ===================== Endpoints =====================


@router.get("/query-performance", response_model=QueryPerformanceResponse)
def get_query_performance(
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
    table_id: UUID | None = Query(None, description="Filter by table ID"),
    engine: str | None = Query(None, description="Filter by engine (trino, spark, etc.)"),
    hours: int = Query(24, description="Hours of history to fetch", ge=1, le=168),
    limit: int = Query(100, description="Max queries to return", ge=1, le=1000),
) -> QueryPerformanceResponse:
    """
    Get query performance metrics for the dashboard.
    
    Returns top slowest queries, latency percentiles, and failure counts.
    """
    since = datetime.now(UTC) - timedelta(hours=hours)
    
    stmt = select(QueryLog).where(QueryLog.started_at >= since)
    
    if table_id:
        # Filter by table (stored in tables_accessed JSON array)
        stmt = stmt.where(func.jsonb_exists_any(QueryLog.tables_accessed, [str(table_id)]))
    
    if engine:
        stmt = stmt.where(QueryLog.engine == engine)
    
    # Order by duration descending (slowest first)
    stmt = stmt.order_by(desc(QueryLog.duration_ms)).limit(limit)
    
    queries = session.execute(stmt).scalars().all()
    
    # Compute percentiles
    percentile_stmt = select(
        func.percentile_cont(0.5).within_group(QueryLog.duration_ms).label("p50"),
        func.percentile_cont(0.95).within_group(QueryLog.duration_ms).label("p95"),
        func.percentile_cont(0.99).within_group(QueryLog.duration_ms).label("p99"),
        func.avg(QueryLog.bytes_scanned).label("avg_bytes"),
        func.count().filter(QueryLog.status == "failed").label("failed_count"),
        func.count().label("total"),
    ).where(QueryLog.started_at >= since)
    
    if table_id:
        percentile_stmt = percentile_stmt.where(
            func.jsonb_exists_any(QueryLog.tables_accessed, [str(table_id)])
        )
    if engine:
        percentile_stmt = percentile_stmt.where(QueryLog.engine == engine)
    
    agg_result = session.execute(percentile_stmt).one()
    
    return QueryPerformanceResponse(
        queries=[
            QueryPerformanceRead(
                query_id=str(q.id),
                query_text=q.query_text[:500] if q.query_text else None,  # Truncate
                engine=q.engine,
                user=q.user,
                duration_ms=q.duration_ms,
                bytes_scanned=q.bytes_scanned,
                rows_produced=q.rows_produced,
                status=q.status,
                started_at=q.started_at,
                table_name=q.tables_accessed[0] if q.tables_accessed else None,
            )
            for q in queries
        ],
        total=agg_result.total,
        p50_latency_ms=agg_result.p50,
        p95_latency_ms=agg_result.p95,
        p99_latency_ms=agg_result.p99,
        avg_bytes_scanned=agg_result.avg_bytes,
        failed_count=agg_result.failed_count,
    )


@router.get("/tables/{table_id}/health-trends", response_model=TableHealthTrendsResponse)
def get_table_health_trends(
    table_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
    days: int = Query(30, description="Days of history to fetch", ge=1, le=90),
) -> TableHealthTrendsResponse:
    """
    Get health trends for a table over time.
    
    Shows grade, score, file counts, and size trends.
    """
    table = session.get(Table, table_id)
    if not table:
        raise HTTPException(status_code=404, detail="Table not found")
    
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
            grade=h.grade,
            score=h.score,
            small_files=h.small_files_count,
            total_size_gb=h.total_size_gb,
            snapshot_count=h.snapshot_count,
            projected_savings_usd_month=h.projected_savings_usd_month,
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
    current_score = current_stats.score if current_stats else 0.0
    
    return TableHealthTrendsResponse(
        table_id=table_id,
        table_name=f"{table.catalog}.{table.schema_name}.{table.table_name}",
        current_grade=current_grade,
        current_score=current_score,
        trend_points=trend_points,
        degradation_rate=degradation_rate,
        days_to_critical=days_to_critical,
    )


@router.get("/jobs/{job_id}/detail", response_model=JobDetailRead)
def get_job_detail(
    job_id: UUID,
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
) -> JobDetailRead:
    """
    Get detailed information about a maintenance job.
    
    Includes SQL statements, effectiveness ratio, and timing.
    """
    job = session.get(MaintenanceJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Get associated audit log entry
    audit_stmt = (
        select(AuditLog)
        .where(AuditLog.table_id == job.table_id)
        .where(AuditLog.started_at >= job.created_at - timedelta(minutes=5))
        .where(AuditLog.started_at <= job.created_at + timedelta(hours=12))
        .order_by(desc(AuditLog.started_at))
        .limit(1)
    )
    audit = session.execute(audit_stmt).scalar_one_or_none()
    
    # Get table name
    table = session.get(Table, job.table_id)
    table_name = f"{table.catalog}.{table.schema_name}.{table.table_name}" if table else str(job.table_id)
    
    # Calculate effectiveness ratio
    effectiveness_ratio = None
    if audit and audit.input_files_count and audit.output_files_count:
        effectiveness_ratio = float(audit.input_files_count) / audit.output_files_count
    
    # Extract SQL from audit detail
    sql_statements = None
    if audit and audit.detail:
        import json
        try:
            detail = json.loads(audit.detail) if isinstance(audit.detail, str) else audit.detail
            sql_statements = detail.get("sql_statements", [])
        except Exception:
            pass
    
    # Extract partition predicates
    partition_predicates = None
    if job.plan and "operations" in job.plan:
        operations = job.plan["operations"]
        predicates = []
        for op in operations:
            if "predicates" in op:
                for pred in op["predicates"]:
                    predicates.append(str(pred))
        partition_predicates = predicates if predicates else None
    
    return JobDetailRead(
        job_id=job_id,
        table_id=job.table_id,
        table_name=table_name,
        operation_type=job.plan.get("operations", [{}])[0].get("type", "unknown") if job.plan else "unknown",
        status=job.status,
        engine=job.destination_type.split("_")[0] if job.destination_type else "unknown",
        replica=job.replica,
        started_at=audit.started_at if audit else None,
        completed_at=audit.completed_at if audit else None,
        duration_seconds=audit.duration_seconds if audit else None,
        input_files_count=audit.input_files_count if audit else None,
        output_files_count=audit.output_files_count if audit else None,
        effectiveness_ratio=effectiveness_ratio,
        error_message=job.error if job.status == JobStatus.FAILED else None,
        sql_statements=sql_statements,
        partition_predicates=partition_predicates,
    )


@router.get("/sla-status", response_model=SLAResponse)
def get_sla_status(
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
    breached_only: bool = Query(False, description="Show only breached SLAs"),
) -> SLAResponse:
    """
    Get SLA status for all monitored tables.
    
    Checks freshness, maintenance, and query performance SLAs.
    """
    # Default SLA thresholds (TODO: make configurable per table)
    freshness_sla_hours = 24
    maintenance_sla_days = 7
    p95_latency_sla_ms = 5000
    
    now = datetime.now(UTC)
    
    # Get all tables with stats
    stmt = select(Table, TableStats).join(TableStats).where(Table.discovered_at.isnot(None))
    
    results = session.execute(stmt).all()
    
    slas: list[SLAStatusRead] = []
    
    for table, stats in results:
        # Calculate freshness
        freshness_hours = None
        if stats.last_updated_at:
            freshness_hours = (now - stats.last_updated_at).total_seconds() / 3600
        
        # Calculate maintenance recency
        maintenance_days = None
        if stats.last_optimized_at:
            maintenance_days = (now - stats.last_optimized_at).total_seconds() / 86400
        
        # Get P95 latency from query logs
        p95_stmt = select(
            func.percentile_cont(0.95).within_group(QueryLog.duration_ms)
        ).where(
            func.jsonb_exists_any(QueryLog.tables_accessed, [str(table.id)]),
            QueryLog.started_at >= now - timedelta(days=1),
        )
        p95_latency_ms = session.execute(p95_stmt).scalar()
        
        # Check breaches
        freshness_breached = freshness_hours is not None and freshness_hours > freshness_sla_hours
        maintenance_breached = maintenance_days is not None and maintenance_days > maintenance_sla_days
        p95_breached = p95_latency_ms is not None and p95_latency_ms > p95_latency_sla_ms
        
        if breached_only and not (freshness_breached or maintenance_breached or p95_breached):
            continue
        
        slas.append(
            SLAStatusRead(
                table_id=table.id,
                table_name=f"{table.catalog}.{table.schema_name}.{table.table_name}",
                freshness_hours=freshness_hours,
                freshness_sla_hours=freshness_sla_hours,
                freshness_breached=freshness_breached,
                maintenance_days=maintenance_days,
                maintenance_sla_days=maintenance_sla_days,
                maintenance_breached=maintenance_breached,
                p95_latency_ms=p95_latency_ms,
                p95_latency_sla_ms=p95_latency_sla_ms,
                p95_latency_breached=p95_breached,
            )
        )
    
    total_breached = sum(
        1 for sla in slas
        if sla.freshness_breached or sla.maintenance_breached or sla.p95_latency_breached
    )
    
    return SLAResponse(
        slas=slas,
        total_breached=total_breached,
        total_monitored=len(slas),
    )


@router.get("/cost-attribution", response_model=CostAttributionResponse)
def get_cost_attribution(
    session: Annotated[Session, Depends(get_session)],
    _principal: Annotated[dict, Depends(require_viewer)],
    month: str | None = Query(None, description="Month in YYYY-MM format (default: current month)"),
) -> CostAttributionResponse:
    """
    Get cost attribution by team/department.
    
    Combines storage costs (from TableStats) with estimated compute costs (from QueryLog).
    """
    if month:
        try:
            year, month_num = map(int, month.split("-"))
            month_start = datetime(year, month_num, 1, tzinfo=UTC)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid month format, use YYYY-MM")
    else:
        now = datetime.now(UTC)
        month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
        month = f"{now.year}-{now.month:02d}"
    
    # Calculate next month start for range queries
    if month_start.month == 12:
        month_end = datetime(month_start.year + 1, 1, 1, tzinfo=UTC)
    else:
        month_end = datetime(month_start.year, month_start.month + 1, 1, tzinfo=UTC)
    
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
