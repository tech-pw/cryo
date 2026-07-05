from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cryo.db.models import (
    Table,
    TableCost,
    TableCostHistory,
    TablePartition,
    TableStats,
    TableStatsHistory,
)
from cryo.scanner.analyze import PartitionAnalysis, TableAnalysis


def upsert_table(
    session: Session,
    *,
    source_id: uuid.UUID,
    schema_name: str,
    table_name: str,
) -> Table:
    table = session.scalar(
        select(Table).where(
            Table.source_id == source_id,
            Table.schema_name == schema_name,
            Table.table_name == table_name,
        )
    )
    if table is None:
        table = Table(source_id=source_id, schema_name=schema_name, table_name=table_name)
        session.add(table)
        session.flush()
    else:
        table.is_active = True
    return table


def already_scanned_today(
    session: Session,
    *,
    table_id: uuid.UUID,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    start_of_day = datetime.combine(current.date(), time.min, tzinfo=current.tzinfo)
    return (
        session.scalar(
            select(TableStats.table_id).where(
                TableStats.table_id == table_id,
                TableStats.scan_at >= start_of_day,
            )
        )
        is not None
    )


def stats_history_for_signals(
    session: Session,
    table_id: uuid.UUID,
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Recent scan history rows for streaming/commit-velocity signal detection."""
    rows = session.scalars(
        select(TableStatsHistory)
        .where(TableStatsHistory.table_id == table_id)
        .order_by(TableStatsHistory.scan_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "scan_at": row.scan_at.isoformat() if row.scan_at else None,
            "total_snapshots": _history_snapshot_count(row),
        }
        for row in reversed(rows)
    ]


def _history_snapshot_count(row: TableStatsHistory) -> int:
    raw = row.raw if isinstance(row.raw, dict) else {}
    value = raw.get("total_snapshots", raw.get("snapshot_count", 0))
    return int(value or 0)


def persist_table_analysis(
    session: Session,
    *,
    source_id: uuid.UUID,
    analysis: TableAnalysis,
    table: Table | None = None,
) -> uuid.UUID:
    table_row = table or upsert_table(
        session,
        source_id=source_id,
        schema_name=analysis.schema_name,
        table_name=analysis.table_name,
    )
    table_id = table_row.id

    stats_values = _stats_values(table_id, analysis)
    existing_stats = session.get(TableStats, table_id)
    if existing_stats is None:
        session.add(TableStats(**stats_values))
    else:
        for key, value in stats_values.items():
            setattr(existing_stats, key, value)

    session.execute(
        delete(TablePartition).where(
            TablePartition.table_id == table_id,
            TablePartition.scan_at == analysis.scan_at,
        )
    )
    session.execute(
        delete(TableStatsHistory).where(
            TableStatsHistory.table_id == table_id,
            TableStatsHistory.scan_at == analysis.scan_at,
        )
    )
    session.add_all(_partition_rows(table_id, analysis))
    session.add(
        TableStatsHistory(
            table_id=table_id,
            scan_at=analysis.scan_at,
            small_files_count=analysis.small_files_count,
            total_files=analysis.total_files,
            total_size_gb=analysis.total_size_gb,
            grade=analysis.grade,
            overall_score=analysis.overall_score,
            raw={
                "total_snapshots": analysis.total_snapshots,
                "total_delete_files": analysis.total_delete_files,
                "delete_file_pct": analysis.delete_file_pct,
                "manifest_count": analysis.manifest_count,
                "metadata_to_data_ratio": (analysis.raw or {}).get("metadata_to_data_ratio"),
                "is_streaming": (analysis.raw or {}).get("is_streaming"),
                "format_version": (analysis.raw or {}).get("format_version"),
                "projected_savings_usd_month": (
                    analysis.cost.projected_savings_usd_month if analysis.cost else None
                ),
                "avg_null_rate": (analysis.raw or {}).get("avg_null_rate"),
                "max_null_rate": (analysis.raw or {}).get("max_null_rate"),
                "worst_null_column": (analysis.raw or {}).get("worst_null_column"),
                # Full per-column detail, not just the summary scalars above — the quality
                # profiler (cryo/quality/profiler.py) needs per-column history for baseline
                # comparison and schema-drift detection, not just the table-level rollup.
                "column_quality": (analysis.raw or {}).get("column_quality"),
            },
        )
    )
    if analysis.cost is not None:
        persist_table_cost(session, table_id=table_id, cost=analysis.cost)

    # Update AutoComp benefit scores after scan (if enabled)
    try:
        from cryo.config import get_settings
        from cryo.planner.benefit_score import update_partition_benefit_scores

        settings = get_settings()
        if hasattr(settings, "compute_statistics_enabled") and settings.compute_statistics_enabled:
            updated = update_partition_benefit_scores(session, table_id)
            if updated > 0:
                import logging

                logger = logging.getLogger(__name__)
                logger.info(f"Updated benefit scores for {updated} partitions of table {table_id}")
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Failed to update benefit scores for table {table_id}: {e}")

    return table_id


def persist_table_cost(
    session: Session,
    *,
    table_id: uuid.UUID,
    cost: Any,
) -> None:
    cost_values = _cost_values(table_id, cost)
    existing_cost = session.get(TableCost, table_id)
    if existing_cost is None:
        session.add(TableCost(**cost_values))
    else:
        for key, value in cost_values.items():
            setattr(existing_cost, key, value)

    session.execute(
        delete(TableCostHistory).where(
            TableCostHistory.table_id == table_id,
            TableCostHistory.scan_at == cost.scan_at,
        )
    )
    session.add(
        TableCostHistory(
            table_id=table_id,
            scan_at=cost.scan_at,
            current_monthly_cost_usd=cost.current_monthly_cost_usd,
            projected_savings_usd_month=cost.projected_savings_usd_month,
            reclaimable_bytes=cost.reclaimable_bytes,
            precision=cost.precision.value if hasattr(cost.precision, "value") else cost.precision,
        )
    )


def _stats_values(table_id: uuid.UUID, analysis: TableAnalysis) -> dict[str, Any]:
    return {
        "table_id": table_id,
        "scan_at": analysis.scan_at,
        "needs_compaction": analysis.needs_compaction,
        "compaction_priority": analysis.compaction_priority,
        "grade": analysis.grade,
        "overall_score": analysis.overall_score,
        "total_files": analysis.total_files,
        "small_files_count": analysis.small_files_count,
        "total_partitions": analysis.total_partitions,
        "max_files_per_partition": analysis.max_files_per_partition,
        "avg_files_per_partition": analysis.avg_files_per_partition,
        "partition_skew_ratio": analysis.partition_skew_ratio,
        "total_records": analysis.total_records,
        "total_size_gb": analysis.total_size_gb,
        "avg_file_size_mb": analysis.avg_file_size_mb,
        "total_snapshots": analysis.total_snapshots,
        "total_delete_files": analysis.total_delete_files,
        "delete_file_pct": analysis.delete_file_pct,
        "manifest_count": analysis.manifest_count,
        "partition_col": analysis.partition_col,
        "partition_col_type": analysis.partition_col_type,
        "write_distribution_mode": analysis.write_distribution_mode,
        "has_sort_order": analysis.has_sort_order,
        "target_file_size_bytes": analysis.target_file_size_bytes,
        "estimated_monthly_s3_cost_usd": analysis.estimated_monthly_s3_cost_usd,
        "projected_monthly_s3_cost_usd": (
            analysis.cost.projected_monthly_cost_usd if analysis.cost else None
        ),
        "projected_savings_usd_month": (
            analysis.cost.projected_savings_usd_month if analysis.cost else None
        ),
        "reclaimable_bytes": analysis.cost.reclaimable_bytes if analysis.cost else None,
        "raw": analysis.raw,
    }


def _cost_values(table_id: uuid.UUID, cost: Any) -> dict[str, Any]:
    precision = cost.precision.value if hasattr(cost.precision, "value") else cost.precision
    return {
        "table_id": table_id,
        "scan_at": cost.scan_at,
        "precision": precision,
        "price_per_gb_month": cost.price_per_gb_month,
        "live_bytes": cost.breakdown.live_bytes,
        "dead_bytes": cost.breakdown.dead_bytes,
        "metadata_bytes": cost.breakdown.metadata_bytes,
        "orphan_bytes": cost.breakdown.orphan_bytes,
        "listed_bytes": cost.breakdown.listed_bytes,
        "last_full_cost_scan_at": cost.last_full_cost_scan_at,
        "reclaim_expire_bytes": cost.reclaim_expire_bytes,
        "reclaim_orphan_bytes": cost.reclaim_orphan_bytes,
        "reclaim_optimize_bytes": cost.reclaim_optimize_bytes,
        "reclaim_manifests_bytes": cost.reclaim_manifests_bytes,
        "current_monthly_cost_usd": cost.current_monthly_cost_usd,
        "projected_monthly_cost_usd": cost.projected_monthly_cost_usd,
        "projected_savings_usd_month": cost.projected_savings_usd_month,
        "query_request_savings_usd_month": cost.query_request_savings_usd_month,
        "raw": cost.raw,
    }


def _partition_rows(table_id: uuid.UUID, analysis: TableAnalysis) -> list[TablePartition]:
    return [
        TablePartition(
            table_id=table_id,
            scan_at=analysis.scan_at,
            partition_value=partition.partition_value,
            partition_values=partition.partition_values,
            partition_fields=partition.partition_fields,
            partition_spec_id=partition.partition_spec_id,
            partition_path=partition.partition_path,
            partition_key=partition.partition_key,
            base_column=partition.base_column,
            base_column_type=partition.base_column_type,
            transform=partition.transform,
            files_per_partition=partition.files_per_partition,
            small_files_in_partition_count=partition.small_files_in_partition_count,
            partition_size_gb=partition.partition_size_gb,
            compaction_status=partition.compaction_status,
            raw=partition.raw,
        )
        for partition in analysis.partitions
    ]


def partition_to_dict(partition: PartitionAnalysis) -> dict[str, Any]:
    return {
        "partition_value": partition.partition_value,
        "partition_values": partition.partition_values,
        "partition_fields": partition.partition_fields,
        "files_per_partition": partition.files_per_partition,
        "small_files_in_partition_count": partition.small_files_in_partition_count,
        "partition_size_gb": partition.partition_size_gb,
        "compaction_status": partition.compaction_status,
    }
