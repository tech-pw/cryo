from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, time, timedelta
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
from cryo.scanner.detection_tiers import TierInputs, classify_detection_tier
from cryo.scanner.analyze import PartitionAnalysis, TableAnalysis


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, str) and "\x00" in value:
        # Postgres text/JSONB can never store a NUL byte -- unlike the source column data being
        # profiled here (e.g. a dirty VARCHAR min/max bound), which can legally contain one. A
        # single such value would otherwise fail the whole row's INSERT, not just that one field.
        return value.replace("\x00", "")
    if isinstance(value, float) and not math.isfinite(value):
        # A decoded manifest min/max bound for a float/double column can legally be +-Infinity
        # or NaN, but Postgres's JSON/JSONB parser rejects those literal tokens outright (they
        # aren't valid per RFC 8259) -- same failure mode as the NUL-byte case above, just for
        # numeric values instead of strings. Store as null rather than fail the whole row.
        return None
    return value


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


def record_table_scan_outcome(
    session: Session,
    *,
    source_id: uuid.UUID,
    schema_name: str,
    table_name: str,
    error: str | None,
    scanned_at: datetime,
) -> Table:
    """Idempotently records whether a table's scan attempt succeeded or failed.

    Callers pass ``error=None`` on success (resets the failure streak) or the exception message
    on failure (increments it). A single failed scan is often just a transient blip -- this is
    meant to catch tables that fail *repeatedly* (e.g. a stale dbt __dbt_tmp pointer whose
    metadata.json was deleted), so callers/consumers should threshold on
    ``consecutive_scan_failures`` rather than treating any nonzero value as "broken".

    Re-upserts the table row rather than trusting a caller-held reference, since callers on the
    failure path typically call this right after ``session.rollback()`` -- which can discard a
    same-transaction insert from an earlier ``upsert_table`` call for a table's very first scan,
    leaving no row to update by id.
    """
    table = upsert_table(
        session, source_id=source_id, schema_name=schema_name, table_name=table_name
    )
    if error is None:
        table.consecutive_scan_failures = 0
        table.last_scan_error = None
        table.last_scan_error_at = None
    else:
        table.consecutive_scan_failures = (table.consecutive_scan_failures or 0) + 1
        table.last_scan_error = _json_safe(error)[:2000]
        table.last_scan_error_at = scanned_at
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


def needs_weekly_reconcile(
    session: Session,
    *,
    table_id: uuid.UUID,
    now: datetime | None = None,
    cadence_days: int = 7,
) -> bool:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    stats = session.get(TableStats, table_id)
    if stats is None or stats.last_full_reconcile_at is None:
        return True
    last = stats.last_full_reconcile_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (current - last).days >= cadence_days


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

    location = (analysis.raw or {}).get("location")
    if location:
        table_row.location = location

    existing_stats = session.get(TableStats, table_id)
    stats_values = _stats_values(table_id, analysis)
    previous_raw = dict(existing_stats.raw or {}) if existing_stats and isinstance(existing_stats.raw, dict) else {}
    previous_history = previous_raw.get("commit_event_history")
    if isinstance(previous_history, list):
        raw_payload = dict(stats_values.get("raw") or {})
        raw_payload["commit_event_history"] = previous_history
        previous_smart = previous_raw.get("smart_maintenance")
        if isinstance(previous_smart, dict):
            raw_payload["smart_maintenance"] = previous_smart
        stats_values["raw"] = raw_payload

    scan_at = analysis.scan_at.astimezone(UTC) if analysis.scan_at.tzinfo else analysis.scan_at.replace(tzinfo=UTC)
    history = stats_values.get("raw", {}).get("commit_event_history") if isinstance(stats_values.get("raw"), dict) else None
    commits_7d = _count_commits_in_window(history, scan_at, days=7)
    commits_24h = _count_commits_in_window(history, scan_at, hours=24)
    query_count_7d = 0
    if existing_stats is not None and existing_stats.query_count_30d:
        query_count_7d = max(0, int(existing_stats.query_count_30d // 4))
    tier_decision = classify_detection_tier(
        TierInputs(
            commits_24h=commits_24h,
            commits_7d=commits_7d,
            query_count_7d=query_count_7d,
            small_files_count=max(0, int(analysis.small_files_count)),
            delete_file_pct=float(analysis.delete_file_pct or 0.0),
            last_scan_at=scan_at,
        ),
        now=scan_at,
    )
    stats_values["detection_tier"] = tier_decision.tier.value

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
            # _json_safe is required here (not just cosmetic, unlike _stats_values/_cost_values
            # where the same call guards against the same thing): column_quality embeds decoded
            # manifest min/max bounds, which can legally contain a NUL byte for a dirty source
            # column -- Postgres text/JSONB can never store one, and without this the INSERT
            # fails for the whole row, discarding an otherwise-successful scan's real stats.
            raw=_json_safe(
                {
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
                }
            ),
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
    raw = dict(_json_safe(analysis.raw) or {})
    if not isinstance(raw, dict):
        raw = {}
    current_snapshot_id = raw.get("current_snapshot_id")
    try:
        snapshot_watermark = int(current_snapshot_id) if current_snapshot_id is not None else None
    except (TypeError, ValueError):
        snapshot_watermark = None

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
        "equality_delete_files": int((analysis.raw or {}).get("equality_delete_files") or 0),
        "position_delete_files": int((analysis.raw or {}).get("position_delete_files") or 0),
        "delete_file_pct": analysis.delete_file_pct,
        "snapshot_watermark": snapshot_watermark,
        "last_full_reconcile_at": analysis.scan_at,
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
        "raw": raw,
    }


def _count_commits_in_window(
    history: Any,
    scan_at: datetime,
    *,
    days: int = 0,
    hours: int = 0,
) -> int:
    if not isinstance(history, list):
        return 0
    lower = scan_at - timedelta(days=days, hours=hours)
    total = 0
    for value in history:
        if not isinstance(value, str):
            continue
        try:
            ts = datetime.fromisoformat(value).astimezone(UTC)
        except ValueError:
            continue
        if ts >= lower:
            total += 1
    return total


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
        "raw": _json_safe(cost.raw),
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
            raw=_json_safe(partition.raw),
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
