from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session, selectinload

from cryo.api.schemas import TableRead
from cryo.api.serializers import table_to_read
from cryo.config import Settings
from cryo.cost.model import bytes_to_usd
from cryo.db.models import (
    AuditLog,
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

_OPERATION_RECLAIM_FIELDS: tuple[tuple[OperationType, str], ...] = (
    (OperationType.EXPIRE_SNAPSHOTS, "reclaim_expire_bytes"),
    (OperationType.REMOVE_ORPHAN_FILES, "reclaim_orphan_bytes"),
    (OperationType.OPTIMIZE, "reclaim_optimize_bytes"),
    (OperationType.OPTIMIZE_MANIFESTS, "reclaim_manifests_bytes"),
)


def dashboard_datalake(
    session: Session,
    *,
    top_limit: int = 10,
    drift_limit: int = 30,
) -> dict[str, object]:
    total_tables = int(session.scalar(select(func.count(Table.id))) or 0)
    active_row = session.execute(
        select(
            func.count(Table.id),
            func.coalesce(func.sum(TableStats.total_size_gb), 0.0),
            func.coalesce(func.sum(TableStats.total_files), 0),
            func.coalesce(func.sum(TableStats.small_files_count), 0),
            func.coalesce(func.sum(TableStats.estimated_monthly_s3_cost_usd), 0.0),
            func.coalesce(func.sum(TableStats.projected_monthly_s3_cost_usd), 0.0),
            func.coalesce(func.sum(TableStats.projected_savings_usd_month), 0.0),
            func.coalesce(func.sum(TableStats.reclaimable_bytes), 0),
            func.coalesce(func.sum(TableStats.query_count_30d), 0),
            func.coalesce(func.sum(case((TableStats.needs_compaction.is_(True), 1), else_=0)), 0),
        )
        .select_from(Table)
        .outerjoin(TableStats)
        .where(Table.is_active.is_(True))
    ).one()

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
            "estimated_monthly_s3_cost_usd": float(active_row[4] or 0),
            "projected_monthly_s3_cost_usd": float(active_row[5] or 0),
            "projected_savings_usd_month": float(active_row[6] or 0),
            "reclaimable_bytes": int(active_row[7] or 0),
            "query_count_30d": int(active_row[8] or 0),
            "needs_compaction_count": int(active_row[9] or 0),
        },
        "grade_distribution": grade_distribution,
        "top_offenders": top_offenders,
        "drift": drift,
    }


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
    active_tables = (
        select(Table, TableStats, TableCost)
        .select_from(Table)
        .outerjoin(TableStats)
        .outerjoin(TableCost)
        .where(Table.is_active.is_(True))
    )
    rows = list(session.execute(active_tables))

    totals = _aggregate_cost_totals(rows, settings=settings)
    savings_by_operation = _aggregate_savings_by_operation(rows)
    cost_by_schema = _aggregate_cost_by_schema(rows)
    top_opportunities = _top_cost_opportunities(session, limit=top_limit)
    trend = _cost_trend(session, limit=trend_limit, settings=settings)

    return {
        "totals": totals,
        "savings_by_operation": savings_by_operation,
        "cost_by_schema": cost_by_schema,
        "top_opportunities": top_opportunities,
        "trend": trend,
    }


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


def _aggregate_cost_totals(
    rows: list[Any],
    *,
    settings: Settings,
) -> dict[str, object]:
    current_cost = 0.0
    projected_cost = 0.0
    savings = 0.0
    reclaimable = 0
    storage_bytes = 0
    precision_coverage: dict[str, int] = {}
    last_full_scan: datetime | None = None
    orphan_scan_stale = False
    stale_after = timedelta(hours=settings.cost_full_scan_every_hours)

    for _table, stats, cost in rows:
        if cost is not None:
            current_cost += float(cost.current_monthly_cost_usd or 0)
            projected_cost += float(cost.projected_monthly_cost_usd or 0)
            savings += float(cost.projected_savings_usd_month or 0)
            reclaimable += int(
                cost.reclaim_expire_bytes
                + cost.reclaim_orphan_bytes
                + cost.reclaim_optimize_bytes
                + cost.reclaim_manifests_bytes
            )
            storage_bytes += int(
                cost.live_bytes + cost.dead_bytes + cost.metadata_bytes + (cost.orphan_bytes or 0)
            )
            precision = cost.precision or CostPrecision.HEURISTIC.value
            precision_coverage[precision] = precision_coverage.get(precision, 0) + 1
            if cost.last_full_cost_scan_at is not None:
                if last_full_scan is None or cost.last_full_cost_scan_at > last_full_scan:
                    last_full_scan = cost.last_full_cost_scan_at
                if (
                    cost.precision == CostPrecision.STORAGE_TRUTH.value
                    and cost.last_full_cost_scan_at < datetime.now(UTC) - stale_after
                ):
                    orphan_scan_stale = True
        elif stats is not None:
            cur = float(stats.estimated_monthly_s3_cost_usd or 0)
            proj = float(stats.projected_monthly_s3_cost_usd or cur)
            sav = float(stats.projected_savings_usd_month or max(0.0, cur - proj))
            current_cost += cur
            projected_cost += proj
            savings += sav
            reclaimable += int(stats.reclaimable_bytes or 0)
            storage_bytes += int((stats.total_size_gb or 0) * 1024**3)

    return {
        "current_monthly_cost_usd": round(current_cost, 4),
        "projected_monthly_cost_usd": round(projected_cost, 4),
        "projected_savings_usd_month": round(savings, 4),
        "current_storage_bytes": storage_bytes,
        "reclaimable_bytes": reclaimable,
        "precision_coverage": precision_coverage,
        "orphan_scan_stale": orphan_scan_stale,
        "last_full_cost_scan_at": last_full_scan,
    }


def _aggregate_savings_by_operation(rows: list[Any]) -> list[dict[str, object]]:
    totals: dict[OperationType, dict[str, float | int]] = {
        operation: {"savings_usd_month": 0.0, "reclaimable_bytes": 0}
        for operation, _field in _OPERATION_RECLAIM_FIELDS
    }
    for _table, _stats, cost in rows:
        if cost is None:
            continue
        raw_ops = (cost.raw or {}).get("by_operation", {})
        for operation, field_name in _OPERATION_RECLAIM_FIELDS:
            reclaim_bytes = int(getattr(cost, field_name) or 0)
            usd = float(raw_ops.get(operation.value, 0) or 0)
            if usd <= 0 and reclaim_bytes > 0:
                usd = bytes_to_usd(
                    reclaim_bytes,
                    price_per_gb_month=cost.price_per_gb_month,
                    use_gib=Settings().cost_use_gib,
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


def _aggregate_cost_by_schema(rows: list[Any]) -> list[dict[str, Any]]:
    by_schema: dict[str, dict[str, Any]] = {}
    for table, stats, cost in rows:
        schema = table.schema_name
        row = by_schema.setdefault(
            schema,
            {"schema_name": schema, "current_usd": 0.0, "projected_usd": 0.0, "savings_usd": 0.0},
        )
        if cost is not None:
            current = float(cost.current_monthly_cost_usd or 0)
            projected = float(cost.projected_monthly_cost_usd or 0)
            savings = float(cost.projected_savings_usd_month or 0)
        elif stats is not None:
            current = float(stats.estimated_monthly_s3_cost_usd or 0)
            projected = float(stats.projected_monthly_s3_cost_usd or current)
            savings = float(stats.projected_savings_usd_month or max(0.0, current - projected))
        else:
            continue
        row["current_usd"] += current
        row["projected_usd"] += projected
        row["savings_usd"] += savings
    items: list[dict[str, Any]] = list(by_schema.values())
    return sorted(items, key=lambda item: float(item["savings_usd"]), reverse=True)


def _top_cost_opportunities(session: Session, *, limit: int) -> list[TableRead]:
    tables = list(
        session.scalars(
            select(Table)
            .join(TableStats)
            .outerjoin(TableCost)
            .where(Table.is_active.is_(True))
            .options(selectinload(Table.stats), selectinload(Table.cost))
            .order_by(
                TableStats.projected_savings_usd_month.desc().nulls_last(),
                TableStats.estimated_monthly_s3_cost_usd.desc().nulls_last(),
                Table.schema_name.asc(),
                Table.table_name.asc(),
            )
            .limit(limit)
        )
    )
    return [table_to_read(table) for table in tables]


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
        summary={
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
        },
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
    return {"run": presets.run_summary(run), "jobs": [presets.job_summary(job) for job in jobs]}


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
