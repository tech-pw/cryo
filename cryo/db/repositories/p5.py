from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import case, desc, func, select
from sqlalchemy.orm import Session, selectinload

from cryo.db.models import (
    AuditLog,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TablePartition,
    TableStats,
    TableStatsHistory,
)
from cryo.db.repositories import p4


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
            "query_count_30d": int(active_row[5] or 0),
            "needs_compaction_count": int(active_row[6] or 0),
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
    run = MaintenanceRun(
        id=uuid.uuid4(),
        kind="scan",
        trigger=str(payload.get("trigger") or "api"),
        status="accepted",
        summary={
            "scope": payload["scope"],
            "source_id": str(payload["source_id"]) if payload.get("source_id") else None,
            "table_ids": [str(table_id) for table_id in payload.get("table_ids", [])],
            "schema_names": list(payload.get("schema_names", [])),
            "requested_by": payload.get("requested_by"),
            "queued": False,
            "placeholder": True,
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
        "message": "Scan request recorded; scanner queue trigger is not implemented yet.",
        "source_id": payload.get("source_id"),
        "table_ids": payload.get("table_ids", []),
        "schema_names": payload.get("schema_names", []),
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
    return {"run": p4.run_summary(run), "jobs": [p4.job_summary(job) for job in jobs]}


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
        if not table_ids:
            raise ValueError("tables scan requires table_ids")
        existing = set(session.scalars(select(Table.id).where(Table.id.in_(table_ids))))
        missing = set(table_ids) - existing
        if missing:
            missing_ids = sorted(str(item) for item in missing)
            raise ValueError(f"Unknown table ids: {missing_ids}")
    elif scope == "schemas" and not payload.get("schema_names"):
        raise ValueError("schemas scan requires schema_names")
