"""Unchanged-snapshot fast path (#532).

When a table's current snapshot ID in ``metadata.json`` matches the ``snapshot_watermark``
from its last full scan (and neither ``--full-scan`` nor the weekly reconcile cadence forces
a full walk), the file and partition layout cannot have changed. We skip reading the manifest
list and every manifest, reuse the stored file/partition/column stats, recompute only the
time-based signals and health grade, and mark ``raw.scan_kind = "unchanged"``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.config import Settings
from cryo.cost.model import CostStatsInput
from cryo.db.models import TableCost, TablePartition, TableStats
from cryo.domain.enums import CostPrecision
from cryo.domain.models import StorageBreakdown
from cryo.observability.metrics import scan_object_store_requests_total
from cryo.scanner.analyze import (
    PartitionAnalysis,
    TableAnalysis,
    _attr,
    _needs_compaction,
    _priority,
    _snapshot_info,
    _table_properties,
    detect_format_version,
    raw_partition_preview,
)
from cryo.scanner.commit_signals import commit_signals
from cryo.scanner.cost import (
    GB,
    MetadataCostAccounting,
    TableCostAnalysis,
    build_table_cost_analysis,
    estimate_storage_cost,
)
from cryo.scanner.health import compute_design_findings, compute_health_score
from cryo.scanner.orphans import table_location
from cryo.scanner.signals import enrich_stats_signals


def load_table_for_probe(catalog: object, schema_name: str, table_name: str) -> object:
    """Load table metadata (1 object-store GET for metadata.json) and record the request metric."""
    scan_object_store_requests_total.inc()
    load_fn = getattr(catalog, "load_table", None)
    if not callable(load_fn):
        raise TypeError("catalog has no callable load_table")
    return load_fn(schema_name, table_name)


def current_snapshot_id_of(table_obj: object) -> int | None:
    metadata = _attr(table_obj, "metadata", None)
    raw_id = _attr(metadata, "current_snapshot_id", None)
    if raw_id is None:
        snap = getattr(table_obj, "current_snapshot", None)
        if callable(snap):
            snap = snap()
        raw_id = _attr(snap, "snapshot_id", None)
    if raw_id is None:
        return None
    try:
        return int(raw_id)
    except (TypeError, ValueError):
        return None


def can_reuse_unchanged_stats(
    existing_stats: TableStats | None,
    *,
    current_snapshot_id: int | None,
    full_scan: bool,
    weekly_reconcile_due: bool,
    settings: Settings,
) -> bool:
    """True when the table's current snapshot matches its last full scan and fast-path is safe."""
    if full_scan or weekly_reconcile_due or not settings.scan_unchanged_fast_path_enabled:
        return False
    if existing_stats is None or existing_stats.last_full_reconcile_at is None:
        return False
    raw = existing_stats.raw if isinstance(existing_stats.raw, Mapping) else {}
    if raw.get("commits_since_scan"):
        # apply_commit_delta moved snapshot_watermark without a real manifest walk; do a full scan.
        return False
    if current_snapshot_id is None:
        # Empty table (no snapshots): safe only if previous scan also had 0 snapshots & 0 files.
        return (
            existing_stats.snapshot_watermark is None
            and int(existing_stats.total_snapshots or 0) == 0
            and int(existing_stats.total_files or 0) == 0
        )
    return existing_stats.snapshot_watermark is not None and int(
        existing_stats.snapshot_watermark
    ) == int(current_snapshot_id)


def build_unchanged_table_analysis(
    session: Session,
    *,
    table_id: UUID,
    schema_name: str,
    table_name: str,
    table_obj: object,
    existing_stats: TableStats,
    scan_at: datetime,
    settings: Settings,
    stats_history: Sequence[Mapping[str, Any]] | None = None,
) -> TableAnalysis:
    """Rebuild TableAnalysis from stored file/partition stats + live metadata.json properties."""
    scan_time = scan_at if scan_at.tzinfo else scan_at.replace(tzinfo=UTC)
    prev_raw: dict[str, Any] = (
        dict(existing_stats.raw) if isinstance(existing_stats.raw, Mapping) else {}
    )

    props = _table_properties(table_obj)
    snapshot_info = _snapshot_info(table_obj)
    format_version = detect_format_version(table_obj)

    total_files = int(existing_stats.total_files or 0)
    small_files_count = int(existing_stats.small_files_count or 0)
    total_partitions = int(existing_stats.total_partitions or 0)
    max_files_per_partition = int(existing_stats.max_files_per_partition or 0)
    avg_files_per_partition = float(existing_stats.avg_files_per_partition or 0.0)
    skew = float(existing_stats.partition_skew_ratio or 0.0)
    total_records = int(existing_stats.total_records or 0)
    total_size_gb = float(existing_stats.total_size_gb or 0.0)
    avg_file_size_mb = float(existing_stats.avg_file_size_mb or 0.0)
    delete_files = int(existing_stats.total_delete_files or 0)
    delete_file_pct = float(existing_stats.delete_file_pct or 0.0)
    manifest_count = int(existing_stats.manifest_count or 0)

    manifest_stats_dict = prev_raw.get("manifest_stats")
    manifest_total_bytes = (
        int(manifest_stats_dict.get("manifest_total_bytes") or 0)
        if isinstance(manifest_stats_dict, Mapping)
        else 0
    )
    total_size_bytes = int(round(total_size_gb * GB))

    # Recompute StorageCostEstimate with current total_snapshots (in case snapshots expired).
    existing_cost_row = session.get(TableCost, table_id)
    delete_bytes = (
        int((existing_cost_row.raw or {}).get("delete_bytes") or 0)
        if existing_cost_row is not None and isinstance(existing_cost_row.raw, Mapping)
        else 0
    )
    cost_est = estimate_storage_cost(
        total_size_bytes=total_size_bytes,
        total_delete_bytes=delete_bytes,
        manifest_total_bytes=manifest_total_bytes,
        total_snapshots=snapshot_info["total_snapshots"],
    )

    file_formats = list(prev_raw.get("file_formats") or [])
    stats_for_health: dict[str, Any] = {
        "total_files": total_files,
        "delete_file_pct": delete_file_pct,
        "total_delete_files": delete_files,
        "total_snapshots": snapshot_info["total_snapshots"],
        "first_snapshot_at": snapshot_info["first_snapshot_at"],
        "last_snapshot_committed_at": snapshot_info["last_snapshot_committed_at"],
        "first_updated_at": snapshot_info["last_snapshot_committed_at"],
        "manifest_count": manifest_count,
        "avg_file_size_mb": avg_file_size_mb,
        "total_size_gb": total_size_gb,
        "total_partitions": total_partitions,
        "partition_skew_ratio": skew,
        "avg_files_per_partition": avg_files_per_partition,
        "write_distribution_mode": props["write_distribution_mode"],
        "has_sort_order": props["has_sort_order"],
        "target_file_size_bytes": props["target_file_size_bytes"],
        "file_formats": file_formats,
        "format_version": format_version,
    }

    cost_analysis = _reuse_cost_analysis(
        existing_cost_row,
        scan_at=scan_time,
        settings=settings,
        small_files_count=small_files_count,
        target_file_size_bytes=props["target_file_size_bytes"],
    )
    if cost_analysis is not None and cost_analysis.precision in {
        CostPrecision.METADATA,
        CostPrecision.STORAGE_TRUTH,
    }:
        stats_for_health = enrich_stats_signals(
            stats_for_health,
            metadata_bytes=cost_analysis.breakdown.metadata_bytes,
            data_bytes=cost_analysis.breakdown.live_bytes,
            format_version=format_version,
            history=stats_history,
        )
    else:
        stats_for_health = enrich_stats_signals(
            stats_for_health,
            format_version=format_version,
            history=stats_history,
        )

    score, grade, health_deductions = compute_health_score(
        stats_for_health, small_files_count, now=scan_time
    )

    partition_rows = _load_stored_partitions(session, table_id)

    raw = dict(prev_raw)
    raw.update(
        {
            "partitions_to_rewrite": raw_partition_preview(partition_rows),
            "snapshot_overhead_gb": cost_est.snapshot_overhead_gb,
            "health_deductions": health_deductions,
            "health_design_findings": compute_design_findings(stats_for_health),
            "first_snapshot_at": snapshot_info["first_snapshot_at"],
            "last_snapshot_committed_at": snapshot_info["last_snapshot_committed_at"],
            "current_snapshot_id": snapshot_info["current_snapshot_id"],
            "location": table_location(table_obj) or prev_raw.get("location"),
            "iceberg_properties": props["iceberg_properties"],
            "gc_enabled": props["gc_enabled"],
            "commit_signals": commit_signals(snapshot_info["commits"], now=scan_time),
            "scan_kind": "unchanged",
        }
    )
    raw.pop("commits_since_scan", None)
    for signal_key in (
        "format_version",
        "v3_upgrade_candidate",
        "metadata_to_data_ratio",
        "is_streaming",
        "commits_per_day",
    ):
        if signal_key in stats_for_health:
            raw[signal_key] = stats_for_health[signal_key]
    if cost_analysis is not None:
        raw["cost"] = cost_analysis.raw

    return TableAnalysis(
        schema_name=schema_name,
        table_name=table_name,
        scan_at=scan_time,
        needs_compaction=_needs_compaction(total_files, small_files_count, max_files_per_partition),
        compaction_priority=_priority(total_files, small_files_count, avg_file_size_mb).value,
        grade=grade,
        overall_score=score,
        total_files=total_files,
        small_files_count=small_files_count,
        total_partitions=total_partitions,
        max_files_per_partition=max_files_per_partition,
        avg_files_per_partition=avg_files_per_partition,
        partition_skew_ratio=skew,
        total_records=total_records,
        total_size_gb=total_size_gb,
        avg_file_size_mb=avg_file_size_mb,
        total_snapshots=snapshot_info["total_snapshots"],
        total_delete_files=delete_files,
        delete_file_pct=delete_file_pct,
        manifest_count=manifest_count,
        partition_col=existing_stats.partition_col,
        partition_col_type=existing_stats.partition_col_type or "",
        write_distribution_mode=props["write_distribution_mode"],
        has_sort_order=props["has_sort_order"],
        target_file_size_bytes=props["target_file_size_bytes"],
        estimated_monthly_s3_cost_usd=cost_est.monthly_s3_cost_usd,
        cost=cost_analysis,
        partitions=partition_rows,
        raw=raw,
    )


def _load_stored_partitions(session: Session, table_id: UUID) -> list[PartitionAnalysis]:
    rows = session.scalars(
        select(TablePartition)
        .where(TablePartition.table_id == table_id)
        .order_by(TablePartition.partition_key.asc())
    ).all()
    out: list[PartitionAnalysis] = []
    for row in rows:
        key = row.partition_key or row.partition_path or row.partition_value or "_unpartitioned"
        val = row.partition_value if row.partition_value is not None else key
        out.append(
            PartitionAnalysis(
                partition_value=val,
                partition_values=dict(row.partition_values or {}),
                partition_fields=[dict(f) for f in (row.partition_fields or [])],
                partition_spec_id=row.partition_spec_id,
                partition_path=row.partition_path,
                partition_key=key,
                base_column=row.base_column,
                base_column_type=row.base_column_type,
                transform=row.transform,
                files_per_partition=int(row.files_per_partition or 0),
                small_files_in_partition_count=int(row.small_files_in_partition_count or 0),
                partition_size_gb=float(row.partition_size_gb or 0.0),
                compaction_status=row.compaction_status or "T",
                raw=dict(row.raw or {}),
            )
        )
    return out


def _reuse_cost_analysis(
    existing_cost: TableCost | None,
    *,
    scan_at: datetime,
    settings: Settings,
    small_files_count: int,
    target_file_size_bytes: int | None,
) -> TableCostAnalysis | None:
    if existing_cost is None:
        return None
    raw_cost = dict(existing_cost.raw or {}) if isinstance(existing_cost.raw, Mapping) else {}
    delete_bytes = int(raw_cost.get("delete_bytes") or 0)
    small_file_bytes = int(raw_cost.get("small_file_bytes") or 0)
    try:
        precision = CostPrecision(existing_cost.precision)
    except ValueError:
        precision = CostPrecision.METADATA
    accounting = MetadataCostAccounting(
        precision=precision,
        breakdown=StorageBreakdown(
            live_bytes=int(existing_cost.live_bytes or 0),
            dead_bytes=int(existing_cost.dead_bytes or 0),
            metadata_bytes=int(existing_cost.metadata_bytes or 0),
            delete_bytes=delete_bytes,
            orphan_bytes=existing_cost.orphan_bytes,
            listed_bytes=existing_cost.listed_bytes,
        ),
        reclaim_expire_bytes=int(existing_cost.reclaim_expire_bytes or 0),
        reclaim_optimize_bytes=int(existing_cost.reclaim_optimize_bytes or 0),
        reclaim_manifests_bytes=int(existing_cost.reclaim_manifests_bytes or 0),
        small_file_bytes=small_file_bytes,
        referenced_file_paths={},
        raw=raw_cost,
    )
    return build_table_cost_analysis(
        accounting,
        scan_at=scan_at,
        settings=settings,
        stats=CostStatsInput(
            small_files_count=small_files_count,
            small_file_bytes=small_file_bytes,
            target_file_size_bytes=target_file_size_bytes or 268_435_456,
            query_count_30d=0,
        ),
        orphan_bytes=existing_cost.orphan_bytes,
        listed_bytes=existing_cost.listed_bytes,
        last_full_cost_scan_at=existing_cost.last_full_cost_scan_at,
        precision=precision,
    )
