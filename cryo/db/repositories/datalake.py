from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, SupportsFloat, cast

from sqlalchemy import and_, case, desc, func, literal, select
from sqlalchemy.orm import Session, load_only, selectinload

from cryo.api.schemas import TableRead
from cryo.api.serializers import table_to_read
from cryo.config import Settings
from cryo.cost.model import bytes_to_usd
from cryo.db.models import (
    AuditLog,
    Destination,
    JobStep,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableCost,
    TableCostHistory,
    TablePartition,
    TableStats,
    TableStatsHistory,
)
from cryo.db.repositories import presets
from cryo.db.repositories import scanner as scanner_repo
from cryo.domain.enums import CostPrecision, OperationType
from cryo.observability.correlation import correlation_summary

_OPERATION_RECLAIM_FIELDS: tuple[tuple[OperationType, str], ...] = (
    (OperationType.EXPIRE_SNAPSHOTS, "reclaim_expire_bytes"),
    (OperationType.REMOVE_ORPHAN_FILES, "reclaim_orphan_bytes"),
    (OperationType.OPTIMIZE, "reclaim_optimize_bytes"),
    (OperationType.OPTIMIZE_MANIFESTS, "reclaim_manifests_bytes"),
)
BROKEN_TABLE_FAILURE_THRESHOLD = 2
_FRESHNESS_BUCKETS: tuple[tuple[str, str, timedelta | None, timedelta | None], ...] = (
    ("within_24h", "Updated in last 24 hours", None, timedelta(hours=24)),
    ("1_3d", "Not updated 1-3 days", timedelta(hours=24), timedelta(days=3)),
    ("3_7d", "Not updated 3-7 days", timedelta(days=3), timedelta(days=7)),
    ("7_15d", "Not updated 7-15 days", timedelta(days=7), timedelta(days=15)),
    ("15_30d", "Not updated 15-30 days", timedelta(days=15), timedelta(days=30)),
    ("1_3m", "Not updated 1-3 months", timedelta(days=30), timedelta(days=90)),
    ("3_6m", "Not updated 3-6 months", timedelta(days=90), timedelta(days=180)),
    ("6_12m", "Not updated 6-12 months", timedelta(days=180), timedelta(days=365)),
    ("over_1y", "Not updated over 1 year", timedelta(days=365), None),
)


def dashboard_datalake(
    session: Session,
    *,
    top_limit: int = 10,
    drift_limit: int = 30,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> dict[str, object]:
    settings = settings or Settings()
    freshness_as_of = now or datetime.now(UTC)
    total_tables = int(session.scalar(select(func.count(Table.id))) or 0)
    active_row = session.execute(
        select(
            func.count(Table.id),
            func.coalesce(func.sum(TableStats.total_size_gb), 0.0),
            func.coalesce(func.sum(TableStats.total_files), 0),
            func.coalesce(func.sum(TableStats.small_files_count), 0),
            func.sum(TableStats.reclaimable_bytes),
            func.count(TableStats.reclaimable_bytes),
            func.coalesce(func.sum(TableStats.query_count_30d), 0),
            func.coalesce(func.sum(case((TableStats.needs_compaction.is_(True), 1), else_=0)), 0),
        )
        .select_from(Table)
        .outerjoin(TableStats)
        .where(Table.is_active.is_(True))
    ).one()

    # Aggregate in SQL -- never `select(Table, TableStats, TableCost)` for the whole fleet.
    # Materializing every active table's ORM row (including TableStats.raw / TableCost.raw
    # JSONB) OOMs cryo-api once the catalog reaches ~10k tables: observed VmPeak ~7.8Gi and
    # OOMKill at the 6Gi limit on a single GET /dashboards/datalake. Same class of bug as
    # smart_maintenance_overview (see values.yaml api.resources comment).
    cost_totals = _sql_aggregate_cost_totals(session, settings=settings)
    cost_by_schema = _sql_aggregate_cost_by_schema(session)

    grade_distribution = [
        {"grade": grade or "unknown", "count": int(count)}
        for grade, count in session.execute(
            select(TableStats.grade, func.count(Table.id))
            .select_from(Table)
            .join(TableStats)
            .where(Table.is_active.is_(True))
            .group_by(TableStats.grade)
            .order_by(TableStats.grade)
        )
    ]
    freshness_distribution = _sql_aggregate_freshness(
        session,
        as_of=freshness_as_of,
    )

    top_offenders = [
        {
            "table_id": row.id,
            "schema_name": row.schema_name,
            "table_name": row.table_name,
            "grade": row.grade,
            "overall_score": row.overall_score,
            "total_size_gb": float(row.total_size_gb or 0),
            "total_files": int(row.total_files or 0),
            "small_files_count": int(row.small_files_count or 0),
            "delete_file_pct": row.delete_file_pct,
            "estimated_monthly_s3_cost_usd": row.estimated_monthly_s3_cost_usd,
            "query_count_30d": int(row.query_count_30d or 0),
        }
        for row in session.execute(
            select(
                Table.id,
                Table.schema_name,
                Table.table_name,
                TableStats.grade,
                TableStats.overall_score,
                TableStats.total_size_gb,
                TableStats.total_files,
                TableStats.small_files_count,
                TableStats.delete_file_pct,
                TableStats.estimated_monthly_s3_cost_usd,
                TableStats.query_count_30d,
            )
            .select_from(Table)
            .join(TableStats)
            .where(Table.is_active.is_(True))
            .order_by(
                TableStats.needs_compaction.desc(),
                TableStats.small_files_count.desc(),
                TableStats.overall_score.asc(),
                Table.schema_name.asc(),
                Table.table_name.asc(),
            )
            .limit(top_limit)
        )
    ]

    drift_rows = list(
        session.execute(
            select(
                TableStatsHistory.scan_at,
                func.coalesce(func.sum(TableStatsHistory.total_size_gb), 0.0),
                func.coalesce(func.sum(TableStatsHistory.total_files), 0),
                func.coalesce(func.sum(TableStatsHistory.small_files_count), 0),
                func.avg(TableStatsHistory.overall_score),
            )
            .group_by(TableStatsHistory.scan_at)
            .order_by(desc(TableStatsHistory.scan_at))
            .limit(drift_limit)
        )
    )
    drift = [
        {
            "scan_at": scan_at,
            "total_size_gb": float(total_size_gb or 0),
            "total_files": int(total_files or 0),
            "small_files_count": int(small_files_count or 0),
            "avg_overall_score": float(avg_score) if avg_score is not None else None,
        }
        for scan_at, total_size_gb, total_files, small_files_count, avg_score in reversed(
            drift_rows
        )
    ]

    return {
        "totals": {
            "total_tables": total_tables,
            "active_tables": int(active_row[0] or 0),
            "total_size_gb": float(active_row[1] or 0),
            "total_files": int(active_row[2] or 0),
            "small_files_count": int(active_row[3] or 0),
            "estimated_monthly_s3_cost_usd": cast(float, cost_totals["current_monthly_cost_usd"]),
            "projected_monthly_s3_cost_usd": cast(float, cost_totals["projected_monthly_cost_usd"]),
            "projected_savings_usd_month": cast(float, cost_totals["projected_savings_usd_month"]),
            "reclaimable_bytes": int(active_row[4]) if active_row[5] else None,
            "query_count_30d": int(active_row[6] or 0),
            "needs_compaction_count": int(active_row[7] or 0),
        },
        "grade_distribution": grade_distribution,
        "freshness_distribution": freshness_distribution,
        "freshness_as_of": freshness_as_of,
        "top_offenders": top_offenders,
        "drift": drift,
        "cost_by_schema": cost_by_schema,
    }


def _sql_aggregate_freshness(
    session: Session,
    *,
    as_of: datetime,
) -> list[dict[str, object]]:
    failed = Table.consecutive_scan_failures >= BROKEN_TABLE_FAILURE_THRESHOLD
    healthy = Table.consecutive_scan_failures < BROKEN_TABLE_FAILURE_THRESHOLD
    committed_at = TableStats.last_snapshot_committed_at
    expressions = []
    for _key, _label, lower_age, upper_age in _FRESHNESS_BUCKETS:
        conditions = [healthy, committed_at.is_not(None)]
        if lower_age is not None:
            conditions.append(committed_at <= as_of - lower_age)
        if upper_age is not None:
            conditions.append(committed_at > as_of - upper_age)
        expressions.append(func.coalesce(func.sum(case((and_(*conditions), 1), else_=0)), 0))
    expressions.extend(
        [
            func.coalesce(
                func.sum(case((and_(healthy, committed_at.is_(None)), 1), else_=0)),
                0,
            ),
            func.coalesce(func.sum(case((failed, 1), else_=0)), 0),
        ]
    )
    row = session.execute(
        select(*expressions)
        .select_from(Table)
        .outerjoin(TableStats)
        .where(Table.is_active.is_(True))
    ).one()
    items = [
        {"key": key, "label": label, "count": int(row[index] or 0)}
        for index, (key, label, _lower, _upper) in enumerate(_FRESHNESS_BUCKETS)
    ]
    items.extend(
        [
            {
                "key": "unknown",
                "label": "No snapshot timestamp",
                "count": int(row[len(_FRESHNESS_BUCKETS)] or 0),
            },
            {
                "key": "failed",
                "label": "Failed / corrupt tables",
                "count": int(row[len(_FRESHNESS_BUCKETS) + 1] or 0),
            },
        ]
    )
    return items


def dashboard_schema_cost(session: Session, *, limit: int = 50) -> dict[str, object]:
    cost = func.coalesce(func.sum(TableStats.estimated_monthly_s3_cost_usd), 0.0)
    rows = session.execute(
        select(
            Table.schema_name,
            func.count(Table.id),
            func.coalesce(func.sum(TableStats.total_size_gb), 0.0),
            cost,
            func.coalesce(func.sum(TableStats.small_files_count), 0),
            func.coalesce(func.sum(TableStats.query_count_30d), 0),
        )
        .select_from(Table)
        .outerjoin(TableStats)
        .where(Table.is_active.is_(True))
        .group_by(Table.schema_name)
        .order_by(cost.desc(), func.coalesce(func.sum(TableStats.total_size_gb), 0.0).desc())
        .limit(limit)
    )
    return {
        "items": [
            {
                "schema_name": schema_name,
                "table_count": int(table_count or 0),
                "total_size_gb": float(total_size_gb or 0),
                "estimated_monthly_s3_cost_usd": float(monthly_cost or 0),
                "small_files_count": int(small_files_count or 0),
                "query_count_30d": int(query_count_30d or 0),
            }
            for (
                schema_name,
                table_count,
                total_size_gb,
                monthly_cost,
                small_files_count,
                query_count_30d,
            ) in rows
        ],
        "limit": limit,
    }


def dashboard_cost(
    session: Session,
    *,
    top_limit: int = 10,
    trend_limit: int = 30,
    settings: Settings | None = None,
) -> dict[str, object]:
    settings = settings or Settings()
    # Same fleet-wide materialization trap as dashboard_datalake -- aggregate in SQL / load
    # only the reclaim columns needed for savings_by_operation (never TableStats.raw).
    totals = _sql_aggregate_cost_totals(session, settings=settings)
    totals["total_realized_savings_usd"] = _total_realized_savings(session, settings=settings)
    savings_by_operation = _sql_aggregate_savings_by_operation(session, settings=settings)
    cost_by_schema = _sql_aggregate_cost_by_schema(session)
    top_opportunities = _top_cost_opportunities(session, limit=top_limit)
    trend = _cost_trend(session, limit=trend_limit, settings=settings)

    return {
        "totals": totals,
        "savings_by_operation": savings_by_operation,
        "cost_by_schema": cost_by_schema,
        "top_opportunities": top_opportunities,
        "trend": trend,
    }


def _total_realized_savings(session: Session, *, settings: Settings) -> float:
    """All-time cumulative $ saved from completed maintenance runs -- distinct from
    _realized_savings_by_day's trend, which is bounded to the last `limit` days. This is
    "cost saved to date," not "cost saved recently"."""
    total_bytes = session.scalar(
        select(func.coalesce(func.sum(AuditLog.actual_bytes_freed), 0)).where(
            AuditLog.actual_bytes_freed.isnot(None)
        )
    )
    return bytes_to_usd(
        int(total_bytes or 0),
        price_per_gb_month=settings.cost_price_per_gb_month,
        use_gib=settings.cost_use_gib,
    )


def table_cost(session: Session, table_id: uuid.UUID) -> dict[str, object] | None:
    table = session.scalar(
        select(Table)
        .where(Table.id == table_id)
        .options(selectinload(Table.stats), selectinload(Table.cost))
    )
    if table is None:
        return None
    cost = table.cost
    if cost is not None:
        return _table_cost_payload(table_id, cost)
    stats = table.stats
    if stats is None:
        return None
    return _table_cost_payload_from_stats(table_id, stats)


def _prefer_cost_expr(cost_col: Any, stats_col: Any, *, stats_fallback: Any | None = None) -> Any:
    """Prefer TableCost column; else TableStats (with optional coalesce fallback)."""
    stats_value = stats_col if stats_fallback is None else func.coalesce(stats_col, stats_fallback)
    return case((TableCost.table_id.isnot(None), cost_col), else_=stats_value)


def _sql_aggregate_cost_totals(session: Session, *, settings: Settings) -> dict[str, object]:
    """Fleet cost totals without materializing ORM rows (or their raw JSONB)."""
    stale_after = timedelta(hours=settings.cost_full_scan_every_hours)
    stats_cur = func.coalesce(TableStats.estimated_monthly_s3_cost_usd, 0.0)
    stats_proj = func.coalesce(TableStats.projected_monthly_s3_cost_usd, stats_cur)
    stats_gap = stats_cur - stats_proj
    # case() instead of greatest() -- SQLite (unit tests) has no GREATEST.
    stats_sav = func.coalesce(
        TableStats.projected_savings_usd_month,
        case((stats_gap > 0, stats_gap), else_=literal(0.0)),
    )

    current_expr = _prefer_cost_expr(TableCost.current_monthly_cost_usd, stats_cur)
    projected_expr = _prefer_cost_expr(TableCost.projected_monthly_cost_usd, stats_proj)
    savings_expr = _prefer_cost_expr(TableCost.projected_savings_usd_month, stats_sav)
    reclaim_expr = case(
        (
            TableCost.table_id.isnot(None),
            TableCost.reclaim_expire_bytes
            + TableCost.reclaim_orphan_bytes
            + TableCost.reclaim_optimize_bytes
            + TableCost.reclaim_manifests_bytes,
        ),
        else_=func.coalesce(TableStats.reclaimable_bytes, 0),
    )
    storage_expr = case(
        (
            TableCost.table_id.isnot(None),
            TableCost.live_bytes
            + TableCost.dead_bytes
            + TableCost.metadata_bytes
            + func.coalesce(TableCost.orphan_bytes, 0),
        ),
        else_=func.coalesce(TableStats.total_size_gb, 0.0) * (1024**3),
    )

    row = session.execute(
        select(
            func.coalesce(func.sum(current_expr), 0.0),
            func.coalesce(func.sum(projected_expr), 0.0),
            func.coalesce(func.sum(savings_expr), 0.0),
            func.coalesce(func.sum(reclaim_expr), 0),
            func.coalesce(func.sum(storage_expr), 0),
        )
        .select_from(Table)
        .outerjoin(TableStats, TableStats.table_id == Table.id)
        .outerjoin(TableCost, TableCost.table_id == Table.id)
        .where(Table.is_active.is_(True))
    ).one()

    # Reuse one expression object for both the select list and GROUP BY: two separately
    # constructed func.coalesce(...) calls bind the same literal to two different
    # parameters, and Postgres then can't verify the select expression is functionally
    # dependent on the GROUP BY expression -- "column must appear in the GROUP BY clause
    # or be used in an aggregate function" even though they're semantically identical.
    precision_expr = func.coalesce(TableCost.precision, CostPrecision.HEURISTIC.value)
    precision_coverage: dict[str, int] = {
        str(precision or CostPrecision.HEURISTIC.value): int(count)
        for precision, count in session.execute(
            select(
                precision_expr,
                func.count(),
            )
            .select_from(Table)
            .join(TableCost, TableCost.table_id == Table.id)
            .where(Table.is_active.is_(True))
            .group_by(precision_expr)
        )
    }
    last_full_scan = session.scalar(
        select(func.max(TableCost.last_full_cost_scan_at))
        .select_from(Table)
        .join(TableCost, TableCost.table_id == Table.id)
        .where(Table.is_active.is_(True))
    )
    orphan_scan_stale = bool(
        session.scalar(
            select(func.count())
            .select_from(Table)
            .join(TableCost, TableCost.table_id == Table.id)
            .where(
                Table.is_active.is_(True),
                TableCost.precision == CostPrecision.STORAGE_TRUTH.value,
                TableCost.last_full_cost_scan_at.isnot(None),
                TableCost.last_full_cost_scan_at < datetime.now(UTC) - stale_after,
            )
        )
    )

    return {
        "current_monthly_cost_usd": round(float(row[0] or 0), 4),
        "projected_monthly_cost_usd": round(float(row[1] or 0), 4),
        "projected_savings_usd_month": round(float(row[2] or 0), 4),
        "current_storage_bytes": int(row[4] or 0),
        "reclaimable_bytes": int(row[3] or 0),
        "precision_coverage": precision_coverage,
        "orphan_scan_stale": orphan_scan_stale,
        "last_full_cost_scan_at": last_full_scan,
    }


def _sql_aggregate_cost_by_schema(session: Session) -> list[dict[str, Any]]:
    stats_cur = func.coalesce(TableStats.estimated_monthly_s3_cost_usd, 0.0)
    stats_proj = func.coalesce(TableStats.projected_monthly_s3_cost_usd, stats_cur)
    stats_gap = stats_cur - stats_proj
    # case() instead of greatest() -- SQLite (unit tests) has no GREATEST.
    stats_sav = func.coalesce(
        TableStats.projected_savings_usd_month,
        case((stats_gap > 0, stats_gap), else_=literal(0.0)),
    )
    current_expr = _prefer_cost_expr(TableCost.current_monthly_cost_usd, stats_cur)
    projected_expr = _prefer_cost_expr(TableCost.projected_monthly_cost_usd, stats_proj)
    savings_expr = _prefer_cost_expr(TableCost.projected_savings_usd_month, stats_sav)

    rows = session.execute(
        select(
            Table.schema_name,
            func.coalesce(func.sum(current_expr), 0.0),
            func.coalesce(func.sum(projected_expr), 0.0),
            func.coalesce(func.sum(savings_expr), 0.0),
        )
        .select_from(Table)
        .outerjoin(TableStats, TableStats.table_id == Table.id)
        .outerjoin(TableCost, TableCost.table_id == Table.id)
        .where(Table.is_active.is_(True))
        .group_by(Table.schema_name)
    ).all()

    items = [
        {
            "schema_name": schema_name,
            "current_usd": float(current or 0),
            "projected_usd": float(projected or 0),
            "savings_usd": float(savings or 0),
        }
        for schema_name, current, projected, savings in rows
        if schema_name is not None
    ]
    return sorted(items, key=lambda item: float(item["savings_usd"]), reverse=True)


def _sql_aggregate_savings_by_operation(
    session: Session, *, settings: Settings
) -> list[dict[str, object]]:
    """Per-operation savings from TableCost reclaim columns only (no raw JSONB)."""
    totals: dict[OperationType, dict[str, float | int]] = {
        operation: {"savings_usd_month": 0.0, "reclaimable_bytes": 0}
        for operation, _field in _OPERATION_RECLAIM_FIELDS
    }
    # load_only the reclaim + price columns -- never TableCost.raw (can be large fleet-wide).
    costs = session.scalars(
        select(TableCost)
        .join(Table, Table.id == TableCost.table_id)
        .where(Table.is_active.is_(True))
        .options(
            load_only(
                TableCost.price_per_gb_month,
                TableCost.reclaim_expire_bytes,
                TableCost.reclaim_orphan_bytes,
                TableCost.reclaim_optimize_bytes,
                TableCost.reclaim_manifests_bytes,
            )
        )
    )
    for cost in costs:
        for operation, field_name in _OPERATION_RECLAIM_FIELDS:
            reclaim_bytes = int(getattr(cost, field_name) or 0)
            if reclaim_bytes <= 0:
                continue
            usd = bytes_to_usd(
                reclaim_bytes,
                price_per_gb_month=cost.price_per_gb_month,
                use_gib=settings.cost_use_gib,
            )
            bucket = totals[operation]
            bucket["savings_usd_month"] = float(bucket["savings_usd_month"]) + usd
            bucket["reclaimable_bytes"] = int(bucket["reclaimable_bytes"]) + reclaim_bytes
    return [
        {
            "operation": operation,
            "savings_usd_month": round(float(bucket["savings_usd_month"]), 4),
            "reclaimable_bytes": int(bucket["reclaimable_bytes"]),
        }
        for operation, bucket in totals.items()
        if float(bucket["savings_usd_month"]) > 0 or int(bucket["reclaimable_bytes"]) > 0
    ]


def _top_cost_opportunities(session: Session, *, limit: int) -> list[TableRead]:
    tables = list(
        session.scalars(
            select(Table)
            .join(TableStats)
            .outerjoin(TableCost)
            .where(Table.is_active.is_(True))
            .options(
                selectinload(Table.stats).defer(TableStats.raw),
                selectinload(Table.cost).defer(TableCost.raw),
            )
            .order_by(
                TableStats.projected_savings_usd_month.desc().nulls_last(),
                TableStats.estimated_monthly_s3_cost_usd.desc().nulls_last(),
                Table.schema_name.asc(),
                Table.table_name.asc(),
            )
            .limit(limit)
        )
    )
    return [table_to_read(table, include_raw=False) for table in tables]


def _cost_trend(
    session: Session,
    *,
    limit: int,
    settings: Settings | None = None,
) -> list[dict[str, object]]:
    settings = settings or Settings()
    drift_rows = list(
        session.execute(
            select(
                TableCostHistory.scan_at,
                func.coalesce(func.sum(TableCostHistory.current_monthly_cost_usd), 0.0),
                func.coalesce(func.sum(TableCostHistory.projected_savings_usd_month), 0.0),
            )
            .group_by(TableCostHistory.scan_at)
            .order_by(desc(TableCostHistory.scan_at))
            .limit(limit)
        )
    )
    realized_by_day = _realized_savings_by_day(session, settings=settings, limit=limit)
    return [
        {
            "scan_at": scan_at,
            "current_monthly_cost_usd": float(current_cost or 0),
            "projected_savings_usd_month": float(savings or 0),
            "realized_usd_month": realized_by_day.get(scan_at.date()) if scan_at else None,
        }
        for scan_at, current_cost, savings in reversed(drift_rows)
    ]


def _realized_savings_by_day(
    session: Session,
    *,
    settings: Settings,
    limit: int,
) -> dict[Any, float]:
    day_bucket = func.date_trunc("day", AuditLog.ts).label("day_bucket")
    rows = list(
        session.execute(
            select(
                day_bucket,
                func.coalesce(func.sum(AuditLog.actual_bytes_freed), 0),
            )
            .where(AuditLog.actual_bytes_freed.isnot(None))
            .group_by(day_bucket)
            .order_by(desc(day_bucket))
            .limit(limit)
        )
    )
    result: dict[Any, float] = {}
    for day, freed_bytes in rows:
        if day is None:
            continue
        day_key = day.date() if hasattr(day, "date") else day
        result[day_key] = bytes_to_usd(
            int(freed_bytes or 0),
            price_per_gb_month=settings.cost_price_per_gb_month,
            use_gib=settings.cost_use_gib,
        )
    return result


def _table_cost_payload_from_stats(table_id: uuid.UUID, stats: TableStats) -> dict[str, object]:
    raw = dict(stats.raw or {})
    current = float(stats.estimated_monthly_s3_cost_usd or 0)
    projected = float(stats.projected_monthly_s3_cost_usd or current)
    savings = float(stats.projected_savings_usd_month or max(0.0, current - projected))
    precision_raw = raw.get("precision")
    try:
        precision_enum = CostPrecision(precision_raw) if precision_raw else CostPrecision.HEURISTIC
    except ValueError:
        precision_enum = CostPrecision.HEURISTIC
    raw_ops = raw.get("by_operation", {})
    by_operation: list[dict[str, object]] = []
    if isinstance(raw_ops, dict):
        for operation, field_name in _OPERATION_RECLAIM_FIELDS:
            usd = float(raw_ops.get(operation.value, 0) or 0)
            reclaim_bytes = int(raw.get(field_name, 0) or 0)
            if usd <= 0 and reclaim_bytes <= 0:
                continue
            by_operation.append(
                {
                    "operation": operation,
                    "savings_usd_month": round(usd, 4),
                    "reclaimable_bytes": reclaim_bytes,
                }
            )
    settings = Settings()
    return {
        "table_id": table_id,
        "precision": precision_enum,
        "price_per_gb_month": settings.cost_price_per_gb_month,
        "current_monthly_cost_usd": current,
        "projected_monthly_cost_usd": projected,
        "projected_savings_usd_month": savings,
        "query_request_savings_usd_month": 0.0,
        "live_bytes": int(raw.get("live_bytes") or 0),
        "dead_bytes": int(raw.get("dead_bytes") or 0),
        "metadata_bytes": int(raw.get("metadata_bytes") or 0),
        "orphan_bytes": raw.get("orphan_bytes"),
        "listed_bytes": raw.get("listed_bytes"),
        "reclaimable_bytes": int(stats.reclaimable_bytes or 0),
        "by_operation": by_operation,
        "last_full_cost_scan_at": None,
        "scan_at": stats.scan_at,
    }


def _table_cost_payload(table_id: uuid.UUID, cost: TableCost) -> dict[str, object]:
    raw_ops = (cost.raw or {}).get("by_operation", {})
    by_operation: list[dict[str, object]] = []
    for operation, field_name in _OPERATION_RECLAIM_FIELDS:
        reclaim_bytes = int(getattr(cost, field_name) or 0)
        usd = float(raw_ops.get(operation.value, 0) or 0)
        if usd <= 0 and reclaim_bytes > 0:
            usd = bytes_to_usd(
                reclaim_bytes,
                price_per_gb_month=cost.price_per_gb_month,
                use_gib=Settings().cost_use_gib,
            )
        if usd <= 0 and reclaim_bytes <= 0:
            continue
        by_operation.append(
            {
                "operation": operation,
                "savings_usd_month": round(usd, 4),
                "reclaimable_bytes": reclaim_bytes,
            }
        )
    precision = cost.precision
    try:
        precision_enum = CostPrecision(precision)
    except ValueError:
        precision_enum = CostPrecision.HEURISTIC
    return {
        "table_id": table_id,
        "precision": precision_enum,
        "price_per_gb_month": cost.price_per_gb_month,
        "current_monthly_cost_usd": cost.current_monthly_cost_usd,
        "projected_monthly_cost_usd": cost.projected_monthly_cost_usd,
        "projected_savings_usd_month": cost.projected_savings_usd_month,
        "query_request_savings_usd_month": cost.query_request_savings_usd_month,
        "live_bytes": cost.live_bytes,
        "dead_bytes": cost.dead_bytes,
        "metadata_bytes": cost.metadata_bytes,
        "orphan_bytes": cost.orphan_bytes,
        "listed_bytes": cost.listed_bytes,
        "reclaimable_bytes": int(
            cost.reclaim_expire_bytes
            + cost.reclaim_orphan_bytes
            + cost.reclaim_optimize_bytes
            + cost.reclaim_manifests_bytes
        ),
        "by_operation": by_operation,
        "last_full_cost_scan_at": cost.last_full_cost_scan_at,
        "scan_at": cost.scan_at,
        # Measured S3 request-cost rollup (docs/s3-request-cost-tracking.md §6.3/§9.2). NULL
        # (not 0) means no ingestion has run for this table yet -- the frontend must not render
        # that as $0, see TableCost's docstring.
        "get_requests_30d": cost.get_requests_30d,
        "put_requests_30d": cost.put_requests_30d,
        "list_requests_30d": cost.list_requests_30d,
        "head_requests_30d": cost.head_requests_30d,
        "delete_requests_30d": cost.delete_requests_30d,
        "request_cost_usd_month": cost.request_cost_usd_month,
        "request_cost_pricing_source": cost.request_cost_pricing_source,
    }


def table_partitions(
    session: Session,
    table_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
    scan_at: datetime | None = None,
) -> dict[str, object] | None:
    if session.get(Table, table_id) is None:
        return None
    selected_scan_at = scan_at or session.scalar(
        select(func.max(TablePartition.scan_at)).where(TablePartition.table_id == table_id)
    )
    if selected_scan_at is None:
        return {
            "table_id": table_id,
            "scan_at": None,
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
        }

    filters = [TablePartition.table_id == table_id, TablePartition.scan_at == selected_scan_at]
    total = int(session.scalar(select(func.count(TablePartition.id)).where(*filters)) or 0)
    items = list(
        session.scalars(
            select(TablePartition)
            .where(*filters)
            .order_by(
                TablePartition.small_files_in_partition_count.desc().nulls_last(),
                TablePartition.files_per_partition.desc().nulls_last(),
                TablePartition.partition_key.asc().nulls_last(),
            )
            .offset(offset)
            .limit(limit)
        )
    )
    return {
        "table_id": table_id,
        "scan_at": selected_scan_at,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def table_audit(
    session: Session,
    table_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
) -> dict[str, object] | None:
    if session.get(Table, table_id) is None:
        return None
    filters = [AuditLog.table_id == table_id]
    total = int(session.scalar(select(func.count(AuditLog.id)).where(*filters)) or 0)
    items = list(
        session.scalars(
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return {
        "table_id": table_id,
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _nearest_stats_snapshot(
    session: Session,
    table_id: uuid.UUID,
    *,
    before_or_at: datetime | None,
    after_or_at: datetime | None,
) -> TableStatsHistory | None:
    """The TableStatsHistory row closest to a job's start (before_or_at) or finish
    (after_or_at). Exactly one of the two kwargs should be set. No repository helper for this
    existed before -- table_history()/stats_snapshot() only do exact-match, offset, or range
    queries, none of which answer "what did the table look like right before/after this run."
    """
    if before_or_at is None and after_or_at is None:
        return None
    query = select(TableStatsHistory).where(TableStatsHistory.table_id == table_id)
    if before_or_at is not None:
        query = query.where(TableStatsHistory.scan_at <= before_or_at).order_by(
            TableStatsHistory.scan_at.desc()
        )
    else:
        query = query.where(TableStatsHistory.scan_at >= after_or_at).order_by(
            TableStatsHistory.scan_at.asc()
        )
    return session.scalars(query.limit(1)).first()


def _stats_snapshot_payload(row: TableStatsHistory | None) -> dict[str, object] | None:
    if row is None:
        return None
    raw = row.raw or {}
    return {
        "scan_at": row.scan_at,
        "grade": row.grade,
        "overall_score": row.overall_score,
        "total_files": row.total_files,
        "small_files_count": row.small_files_count,
        "total_size_gb": row.total_size_gb,
        "total_delete_files": raw.get("total_delete_files"),
        "delete_file_pct": raw.get("delete_file_pct"),
    }


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, (SupportsFloat, str)) else None


def _pct_change(before: float | None, after: float | None) -> float | None:
    """Signed percent change, before -> after. Negative means it went down. None if either
    side is missing or before is zero (no baseline to compare against)."""
    if before is None or after is None or before == 0:
        return None
    return round(((after - before) / before) * 100, 1)


def _run_effectiveness_payload(
    before: dict[str, object] | None, after: dict[str, object] | None
) -> dict[str, object] | None:
    """File-count and byte-size reduction as separate, independently-signed percentages.

    Confirmed live, 2026-08-10: mview.gold_dbt_drip_marketing_flow's compaction cut file
    count 2,549 -> 268 (-89%) while total size only moved -4% -- a genuinely large S3
    request-cost win (S3 bills per request, not per byte) that a size-only "before/after"
    summary would make look unremarkable. Reporting both separately, rather than one blended
    number, is the fix. See docs/open-items-2026-08-10.md item #3.
    """
    if before is None or after is None:
        return None
    total_files_before = before.get("total_files")
    total_files_after = after.get("total_files")
    small_files_before = before.get("small_files_count")
    small_files_after = after.get("small_files_count")
    size_before = before.get("total_size_gb")
    size_after = after.get("total_size_gb")

    file_pct = _pct_change(_as_float(total_files_before), _as_float(total_files_after))
    small_file_pct = _pct_change(_as_float(small_files_before), _as_float(small_files_after))
    byte_pct = _pct_change(_as_float(size_before), _as_float(size_after))
    if file_pct is None and byte_pct is None:
        return None

    parts = []
    if file_pct is not None:
        parts.append(f"{abs(file_pct):.0f}% {'fewer' if file_pct < 0 else 'more'} files")
    if byte_pct is not None:
        direction = "smaller" if byte_pct < 0 else "larger" if byte_pct > 0 else "unchanged"
        size_word = f"{abs(byte_pct):.0f}% {direction}" if byte_pct != 0 else "unchanged"
        parts.append(f"size {size_word}")
    headline = ", ".join(parts) if parts else None

    return {
        "file_count_reduction_pct": file_pct,
        "small_file_reduction_pct": small_file_pct,
        "byte_reduction_pct": byte_pct,
        "headline": headline,
    }


def _resource_estimate_payload(
    steps: list[JobStep], destination: Destination | None
) -> dict[str, object] | None:
    """Reserved-capacity estimate: configured executor/driver shape x active wall-clock time
    actually spent on completed steps. This is a config x duration estimate, not measured
    utilization -- Cryo has no Prometheus/Mimir scraping pipeline wired up (see
    cryo/observability/metrics.py's unused gauges), so this is the best available signal
    without one. Only meaningful for the Spark-on-K8s engine today; other engines return None
    for the numeric fields (still useful to show engine + duration alone).
    """
    active_seconds = sum(
        (step.finished_at - step.started_at).total_seconds()
        for step in steps
        if step.started_at is not None and step.finished_at is not None
    )
    engine = destination.engine if destination is not None else None
    if destination is None or engine not in {"spark_emr", "spark_k8s", "spark"}:
        return {
            "engine": engine,
            "vcpu_hours": None,
            "memory_gib_hours": None,
            "active_duration_seconds": active_seconds,
        }
    spark_cfg = (
        destination.config.get("spark_k8s") if isinstance(destination.config, dict) else None
    )
    spark_cfg = spark_cfg if isinstance(spark_cfg, dict) else {}
    executor_instances = int(spark_cfg.get("executor_instances") or 2)
    executor_cpu_milli = int(spark_cfg.get("executor_cpu_milli") or 2000)
    executor_mem_mib = int(spark_cfg.get("executor_mem_mib") or 4096)
    driver_cpu_milli = int(spark_cfg.get("driver_cpu_milli") or 1000)
    driver_mem_mib = int(spark_cfg.get("driver_mem_mib") or 2048)
    hours = active_seconds / 3600.0
    vcpu_hours = (
        executor_instances * (executor_cpu_milli / 1000.0) + (driver_cpu_milli / 1000.0)
    ) * hours
    memory_gib_hours = (
        executor_instances * (executor_mem_mib / 1024.0) + (driver_mem_mib / 1024.0)
    ) * hours
    return {
        "engine": engine,
        "vcpu_hours": round(vcpu_hours, 2),
        "memory_gib_hours": round(memory_gib_hours, 2),
        "active_duration_seconds": active_seconds,
    }


def table_runs(
    session: Session, table_id: uuid.UUID, *, limit: int, offset: int
) -> dict[str, object] | None:
    """Maintenance runs that touched this table, each enriched with its operations
    (job_steps), a before/after TableStatsHistory delta, and a resource-usage estimate.

    No endpoint previously scoped runs/jobs by table_id at all (GET /runs has no table_id
    filter; every frontend caller filtered client-side by preset/kind instead) -- this is the
    first one that does the join, and the first place before/after stats or resource estimates
    are computed anywhere in the API layer.
    """
    if session.get(Table, table_id) is None:
        return None
    filters = [MaintenanceJob.table_id == table_id]
    total = int(session.scalar(select(func.count(MaintenanceJob.id)).where(*filters)) or 0)
    jobs = list(
        session.scalars(
            select(MaintenanceJob)
            .where(*filters)
            .order_by(MaintenanceJob.started_at.desc().nullslast(), MaintenanceJob.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    if not jobs:
        return {"table_id": table_id, "items": [], "total": total, "limit": limit, "offset": offset}

    job_ids = [job.id for job in jobs]
    steps_by_job: dict[uuid.UUID, list[JobStep]] = {job_id: [] for job_id in job_ids}
    for step in session.scalars(
        select(JobStep).where(JobStep.job_id.in_(job_ids)).order_by(JobStep.step_index.asc())
    ):
        steps_by_job[step.job_id].append(step)

    run_ids = {job.run_id for job in jobs}
    runs_by_id = {
        run.id: run
        for run in session.scalars(select(MaintenanceRun).where(MaintenanceRun.id.in_(run_ids)))
    }
    destination_ids = {job.destination_id for job in jobs}
    destinations_by_id = {
        dest.id: dest
        for dest in session.scalars(select(Destination).where(Destination.id.in_(destination_ids)))
    }

    items: list[dict[str, object]] = []
    for job in jobs:
        run = runs_by_id.get(job.run_id)
        steps = steps_by_job.get(job.id, [])
        started_at = job.started_at
        finished_at = job.finished_at
        duration_seconds = (
            (finished_at - started_at).total_seconds()
            if started_at is not None and finished_at is not None
            else None
        )
        before = _nearest_stats_snapshot(
            session, table_id, before_or_at=started_at, after_or_at=None
        )
        after = (
            _nearest_stats_snapshot(session, table_id, before_or_at=None, after_or_at=finished_at)
            if finished_at is not None
            else None
        )
        items.append(
            {
                "job_id": job.id,
                "run_id": job.run_id,
                "status": job.status,
                "trigger": run.trigger if run is not None else None,
                "mode": job.mode,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_seconds": duration_seconds,
                "error": job.error,
                "steps": [
                    {
                        "step_index": step.step_index,
                        "operation_type": step.operation_type,
                        "status": step.status,
                        "started_at": step.started_at,
                        "finished_at": step.finished_at,
                    }
                    for step in steps
                ],
                "before_stats": (before_payload := _stats_snapshot_payload(before)),
                "after_stats": (after_payload := _stats_snapshot_payload(after)),
                "resource_estimate": _resource_estimate_payload(
                    steps, destinations_by_id.get(job.destination_id)
                ),
                "effectiveness": _run_effectiveness_payload(before_payload, after_payload),
            }
        )

    return {"table_id": table_id, "items": items, "total": total, "limit": limit, "offset": offset}


def table_history(session: Session, table_id: uuid.UUID, *, limit: int) -> dict[str, object] | None:
    if session.get(Table, table_id) is None:
        return None
    items = list(
        session.scalars(
            select(TableStatsHistory)
            .where(TableStatsHistory.table_id == table_id)
            .order_by(TableStatsHistory.scan_at.desc(), TableStatsHistory.id.desc())
            .limit(limit)
        )
    )
    return {"table_id": table_id, "items": list(reversed(items)), "limit": limit}


def create_scan_trigger(session: Session, payload: dict[str, Any]) -> dict[str, object]:
    _validate_scan_scope(session, payload)
    conflict = scanner_repo.active_scan_conflict(session, payload)
    if conflict is not None:
        raise ValueError(conflict)
    run = MaintenanceRun(
        id=uuid.uuid4(),
        kind="scan",
        trigger=str(payload.get("trigger") or "api"),
        status="accepted",
        summary=correlation_summary(
            {
                "scope": payload["scope"],
                "source_id": str(payload["source_id"]) if payload.get("source_id") else None,
                "table_ids": [str(table_id) for table_id in payload.get("table_ids", [])],
                "table_names": list(payload.get("table_names", [])),
                "schema_names": list(payload.get("schema_names", [])),
                "requested_by": payload.get("requested_by"),
                "cost_full_scan": bool(payload.get("cost_full_scan")),
                "full_scan": bool(payload.get("full_scan", True)),
                "queued": False,
                "placeholder": False,
            }
        ),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return {
        "run_id": run.id,
        "status": run.status,
        "scope": payload["scope"],
        "queued": False,
        "message": (
            "Scan request accepted. A scanner worker will pick it up from the queue "
            "or poll accepted scan runs."
        ),
        "source_id": payload.get("source_id"),
        "table_ids": payload.get("table_ids", []),
        "table_names": payload.get("table_names", []),
        "schema_names": payload.get("schema_names", []),
        "cost_full_scan": bool(payload.get("cost_full_scan")),
        "full_scan": bool(payload.get("full_scan", True)),
    }


def realized_savings_for_run(session: Session, run_id: uuid.UUID) -> dict[str, int | None]:
    """Sum measured reclaim for one run. Does not load audit rows.

    ``realized_bytes_freed`` is None when no successful action reported bytes.
    A SQL SUM of NULLs would otherwise look like a measured zero.
    """
    from cryo.reporting.impact_signals import _RECLAIM_ACTIONS, _SUCCESS_STATUSES

    row = session.execute(
        select(
            func.coalesce(func.sum(AuditLog.actual_bytes_freed), 0),
            func.count(AuditLog.actual_bytes_freed),
            func.count(AuditLog.id),
        )
        .select_from(AuditLog)
        .join(MaintenanceJob, AuditLog.job_id == MaintenanceJob.id)
        .where(
            MaintenanceJob.run_id == run_id,
            AuditLog.action.in_(_RECLAIM_ACTIONS),
            AuditLog.status.in_(tuple(_SUCCESS_STATUSES)),
        )
    ).one()
    measured = int(row[1] or 0)
    return {
        "realized_bytes_freed": int(row[0] or 0) if measured else None,
        "realized_actions_measured": measured,
        "realized_actions_succeeded": int(row[2] or 0),
    }


def run_jobs_snapshot(session: Session, run_id: uuid.UUID) -> dict[str, object] | None:
    run = session.scalar(
        select(MaintenanceRun)
        .where(MaintenanceRun.id == run_id)
        .options(
            selectinload(MaintenanceRun.jobs).selectinload(MaintenanceJob.table),
            selectinload(MaintenanceRun.jobs).selectinload(MaintenanceJob.destination),
        )
    )
    if run is None:
        return None
    jobs = sorted(
        run.jobs,
        key=lambda job: (
            job.table.schema_name if job.table else "",
            job.table.table_name if job.table else "",
            str(job.id),
        ),
    )
    summary = presets.run_summary(run)
    summary.update(realized_savings_for_run(session, run.id))
    return {"run": summary, "jobs": [presets.job_summary(job) for job in jobs]}


def _validate_scan_scope(session: Session, payload: dict[str, Any]) -> None:
    scope = payload["scope"]
    if scope == "source":
        source_id = payload.get("source_id")
        if source_id is None:
            raise ValueError("source scan requires source_id")
        if session.get(Source, source_id) is None:
            raise ValueError("Source does not exist")
    elif scope == "tables":
        table_ids = payload.get("table_ids", [])
        table_names = payload.get("table_names", [])
        if not table_ids and not table_names:
            raise ValueError("tables scan requires table_ids or table_names")
        if table_ids:
            existing = set(session.scalars(select(Table.id).where(Table.id.in_(table_ids))))
            missing = set(table_ids) - existing
            if missing:
                missing_ids = sorted(str(item) for item in missing)
                raise ValueError(f"Unknown table ids: {missing_ids}")
        if table_names and not payload.get("source_id"):
            # Name-only identifiers are ambiguous across lakehouses -- the same schema.table name
            # could exist in more than one, so this path requires knowing which one to look in.
            raise ValueError("table_names requires source_id")
    elif scope == "schemas" and not payload.get("schema_names"):
        raise ValueError("schemas scan requires schema_names")
