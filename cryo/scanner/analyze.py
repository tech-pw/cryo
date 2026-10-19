from __future__ import annotations

import logging
import statistics
import uuid
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from cryo.config import Settings, get_settings
from cryo.cost.model import CostStatsInput
from cryo.domain.enums import Priority
from cryo.planner.partitions import normalize_transform
from cryo.scanner.commit_signals import commit_signals, commits_from_snapshots
from cryo.scanner.cost import (
    GB,
    MB,
    MetadataCostAccounting,
    TableCostAnalysis,
    _snapshot_timestamp,
    account_metadata_cost,
    build_table_cost_analysis,
    estimate_storage_cost,
)
from cryo.scanner.health import compute_health_score
from cryo.scanner.orphans import table_location
from cryo.scanner.quality import ColumnQualityAccumulator, summarize_column_quality
from cryo.scanner.signals import detect_format_version, enrich_stats_signals, manifest_stats

logger = logging.getLogger(__name__)

FILE_SIZE_BIN_COUNT = 40


def record_file_size_bin(bins: list[int], size_bytes: int) -> None:
    if size_bytes <= 0:
        bins[0] += 1
        return
    index = min(size_bytes.bit_length() - 1, len(bins) - 1)
    bins[index] += 1


HEALTH_SMALL_FILE_BYTES = 8 * MB
# Rewrite target when the table sets no write.target-file-size-bytes; matches the Go scanner.
DEFAULT_REDUCTION_TARGET_BYTES = 256 * MB


class _LoadableCatalog(Protocol):
    def load_table(self, schema: str, table: str) -> object: ...


@dataclass(frozen=True)
class PartitionAnalysis:
    partition_value: str
    partition_values: dict[str, Any]
    partition_fields: list[dict[str, Any]]
    partition_spec_id: int | None
    partition_path: str | None
    partition_key: str
    base_column: str | None
    base_column_type: str | None
    transform: str | None
    files_per_partition: int
    small_files_in_partition_count: int
    partition_size_gb: float
    compaction_status: str
    raw: dict[str, Any]


# The full partition list is persisted to `table_partitions`; `raw` only keeps the worst few.
# Storing all of them (up to 5,000 per table) made `table_stats.raw` multi-MB per row and
# was copied into every `table_stats_history` row.
RAW_PARTITION_PREVIEW_LIMIT = 50


def raw_partition_preview(partitions: Sequence[PartitionAnalysis]) -> list[dict[str, Any]]:
    worst = sorted(
        partitions,
        key=lambda p: (-p.small_files_in_partition_count, -p.partition_size_gb, p.partition_key),
    )[:RAW_PARTITION_PREVIEW_LIMIT]
    return [partition.__dict__ for partition in worst]


@dataclass(frozen=True)
class TableAnalysis:
    schema_name: str
    table_name: str
    scan_at: datetime
    needs_compaction: bool
    compaction_priority: str
    grade: str
    overall_score: int
    total_files: int
    small_files_count: int
    total_partitions: int
    max_files_per_partition: int
    avg_files_per_partition: float
    partition_skew_ratio: float
    total_records: int
    total_size_gb: float
    avg_file_size_mb: float
    total_snapshots: int
    total_delete_files: int
    delete_file_pct: float
    manifest_count: int
    partition_col: str | None
    partition_col_type: str | None
    write_distribution_mode: str | None
    has_sort_order: bool | None
    target_file_size_bytes: int | None
    estimated_monthly_s3_cost_usd: float
    cost: TableCostAnalysis | None
    partitions: list[PartitionAnalysis]
    raw: dict[str, Any]


@dataclass
class _PartitionAccum:
    values: dict[str, Any]
    fields: list[dict[str, Any]]
    spec_id: int | None
    files: int = 0
    small: int = 0
    size_bytes: int = 0
    delete_files: int = 0
    equality_delete_files: int = 0
    equality_delete_bytes: int = 0
    last_write_ms: int | None = None
    sample_path: str | None = None


def analyze_catalog_table(
    catalog: object,
    schema_name: str,
    table_name: str,
    *,
    scan_at: datetime | None = None,
    small_file_mb: float = 89.6,
    file_size_threshold_mb: int = 128,
    settings: Settings | None = None,
    parallel_manifests: bool = True,
    stats_history: Sequence[Mapping[str, Any]] | None = None,
    source_config: Mapping[str, Any] | None = None,
    wair_columns: Sequence[str] = (),
) -> TableAnalysis:
    del file_size_threshold_mb
    effective_settings = settings or Settings(scan_small_file_mb=small_file_mb)
    if effective_settings.scan_engine == "go":
        if source_config is None:
            raise ValueError("source_config is required when CRYO_SCAN_ENGINE=go")
        from cryo.scanner.go_scan import analyze_catalog_table_go

        # Regression, discovered 2026-07-31: this used to pass table_obj=None
        # unconditionally, which silently disabled Tier-1 cost accounting for every
        # Go-scanned table fleet-wide (run_metadata_cost requires table_obj is not
        # None) -- table_cost/table_cost_history stopped getting any new rows the
        # day the Go engine became the primary scanner, and nothing surfaced the
        # gap since analyze_catalog_table_go/_go_payload degrade to cost=None
        # rather than raising. A lazy loader (not an eager load_table() call) so
        # the catalog fetch only happens for tables that already pass the cheap
        # size/manifest thresholds computed from the Go engine's own payload --
        # huge tables that would be threshold-filtered anyway never pay for it.
        return analyze_catalog_table_go(
            source_config=source_config,
            schema_name=schema_name,
            table_name=table_name,
            scan_at=scan_at,
            settings=effective_settings,
            stats_history=stats_history,
            table_obj_loader=lambda: cast(_LoadableCatalog, catalog).load_table(
                schema_name, table_name
            ),
            wair_columns=wair_columns,
        )
    table_obj = cast(_LoadableCatalog, catalog).load_table(schema_name, table_name)
    result = analyze_table(
        table_obj,
        schema_name,
        table_name,
        scan_at=scan_at,
        settings=effective_settings,
        parallel_manifests=parallel_manifests,
        stats_history=stats_history,
        wair_columns=wair_columns,
    )
    assert isinstance(result, TableAnalysis)
    return result


def analyze_table(
    table_obj: object,
    schema_name: str | Settings | None = None,
    table_name: str | None = None,
    *,
    scan_at: datetime | None = None,
    settings: Settings | None = None,
    parallel_manifests: bool = True,
    stats_history: Sequence[Mapping[str, Any]] | None = None,
    wair_columns: Sequence[str] = (),
) -> TableAnalysis | dict[str, Any]:
    if isinstance(schema_name, Settings):
        settings = schema_name
        schema_name = None
    settings = settings or get_settings()
    scan_time = scan_at or datetime.now(UTC)
    if scan_time.tzinfo is None:
        scan_time = scan_time.replace(tzinfo=UTC)

    fields = partition_fields(table_obj)
    manifests = _manifests(table_obj)
    props = _table_properties(table_obj)
    reduction_target_bytes = int(props["target_file_size_bytes"] or DEFAULT_REDUCTION_TARGET_BYTES)
    partitions: dict[str, _PartitionAccum] = {}
    total_files = total_records = total_size_bytes = 0
    files_below_target = bytes_below_target = 0
    delete_files = delete_bytes = health_small_files = 0
    equality_delete_files = 0
    position_delete_files = 0
    data_size_bins = [0] * FILE_SIZE_BIN_COUNT
    delete_size_bins = [0] * FILE_SIZE_BIN_COUNT
    unknown_file_sizes = 0
    file_formats: set[str] = set()
    schema = _call_or_attr(table_obj, "schema", default=None)
    quality_accum = ColumnQualityAccumulator(schema)
    wair_file_samples: dict[str, dict[str, Any]] = {}

    max_workers = settings.scan_parallel_manifests if parallel_manifests else 1
    manifest_read_errors: list[str] = []
    snapshot_times = _snapshot_times_by_id(table_obj)
    for entry in _iter_live_entries(
        table_obj,
        manifests,
        parallel=parallel_manifests,
        max_workers=max_workers,
        manifest_errors=manifest_read_errors,
    ):
        data_file = _attr(entry, "data_file", entry)
        write_ms = _entry_write_ms(entry, snapshot_times)
        raw_size = _attr(data_file, "file_size_in_bytes", None)
        if raw_size is None:
            unknown_file_sizes += 1
            file_size = 0
        else:
            file_size = int(raw_size or 0)
            target_bins = delete_size_bins if _delete_file_kind(data_file) else data_size_bins
            record_file_size_bin(target_bins, file_size)
        file_format = _attr(data_file, "file_format", None)
        if file_format:
            file_formats.add(str(file_format).upper())
        delete_kind = _delete_file_kind(data_file)
        if delete_kind is not None:
            # Delete files (position/equality) are tracked in delete_files/delete_bytes/
            # *_delete_files -- they must NOT also fall into total_files/total_size_bytes/
            # total_records or the per-partition files/small/size_bytes tallies below.
            # Mirrors the identical fix in engine-go/internal/scanstats/scan.go (2026-07-28):
            # a table with 34,176 position-delete files (each a few KB, so always "small") had
            # every one of them double-counted as a small DATA file too. Still attributed to
            # its partition's own delete_files count, separate from files/small, so
            # convert_deletes can be scoped by where deletes concentrate independent of where
            # small data files concentrate.
            delete_files += 1
            delete_bytes += file_size
            if delete_kind == "equality":
                equality_delete_files += 1
            elif delete_kind == "position":
                position_delete_files += 1
            values = data_file_partition_values(data_file, fields)
            key = partition_key(values)
            accum = partitions.setdefault(
                key,
                _PartitionAccum(
                    values=values,
                    fields=list(fields),
                    spec_id=_optional_int(_attr(data_file, "spec_id", None)),
                ),
            )
            accum.delete_files += 1
            if delete_kind == "equality":
                accum.equality_delete_files += 1
                accum.equality_delete_bytes += file_size
            _note_last_write(accum, write_ms)
            continue
        total_files += 1
        total_size_bytes += file_size
        total_records += int(_attr(data_file, "record_count", 0) or 0)
        if file_size < HEALTH_SMALL_FILE_BYTES:
            health_small_files += 1
        if file_size < reduction_target_bytes:
            files_below_target += 1
            bytes_below_target += file_size
        quality_accum.add(data_file)
        if settings.scanner_wair_file_sample_size and wair_columns:
            sample = quality_accum.file_bound_sample(data_file, wair_columns)
            if sample is not None:
                wair_file_samples[sample["file_path"]] = sample
                if len(wair_file_samples) > settings.scanner_wair_file_sample_size:
                    del wair_file_samples[max(wair_file_samples)]

        values = data_file_partition_values(data_file, fields)
        key = partition_key(values)
        accum = partitions.setdefault(
            key,
            _PartitionAccum(
                values=values,
                fields=list(fields),
                spec_id=_optional_int(_attr(data_file, "spec_id", None)),
            ),
        )
        accum.files += 1
        accum.size_bytes += file_size
        if file_size < settings.scan_small_file_mb * MB:
            accum.small += 1
        _note_last_write(accum, write_ms)
        if accum.sample_path is None:
            file_path = _attr(data_file, "file_path", None)
            if isinstance(file_path, str) and file_path:
                accum.sample_path = file_path

    partition_rows = [
        partition_row(key, accum)
        for key, accum in sorted(partitions.items(), key=lambda item: item[0])
    ]
    total_partitions = len(partition_rows)
    files_per_partition = [row.files_per_partition for row in partition_rows]
    max_files_per_partition = max(files_per_partition, default=0)
    avg_files_per_partition = (
        round(sum(files_per_partition) / len(files_per_partition), 1)
        if files_per_partition
        else 0.0
    )
    median_files = statistics.median(files_per_partition) if files_per_partition else 0
    skew = round(max_files_per_partition / max(median_files, 1), 1) if files_per_partition else 0.0
    small_files_count = sum(
        row.small_files_in_partition_count for row in partition_rows if row.compaction_status == "F"
    )
    small_file_bytes = sum(
        accum.size_bytes for accum in partitions.values() if accum.small > 1 and accum.files > 1
    )
    # Cap retained partition rows the same way persistence / Go scan-stats do — totals above
    # still use the full map so small_files_count / skew stay correct.
    max_partitions_kept = settings.scanner_max_partitions_per_table
    if len(partition_rows) > max_partitions_kept:
        partition_rows = sorted(
            partition_rows,
            key=lambda p: (p.small_files_in_partition_count, p.partition_size_gb),
            reverse=True,
        )[:max_partitions_kept]
        partition_rows = sorted(partition_rows, key=lambda p: p.partition_key)
    avg_file_size_mb = (total_size_bytes / max(total_files, 1)) / MB if total_files else 0.0
    snapshot_info = _snapshot_info(table_obj)
    format_version = detect_format_version(table_obj)
    manifest_total_bytes = sum(
        int(_attr(manifest, "manifest_length", 0) or 0) for manifest in manifests
    )
    cost = estimate_storage_cost(
        total_size_bytes=total_size_bytes,
        total_delete_bytes=delete_bytes,
        manifest_total_bytes=manifest_total_bytes,
        total_snapshots=snapshot_info["total_snapshots"],
    )
    stats_for_health = {
        "total_files": total_files,
        # Clamped to 100: delete_files can outnumber total_files on a heavily MOR-fragmented
        # table (total_files correctly excludes delete files as of 2026-07-28 -- see
        # engine-go/internal/scanstats/scan.go), so this ratio is no longer bounded at 100%
        # by construction. incremental.py already clamped this same ratio; this just matches.
        "delete_file_pct": round(min(100.0, (delete_files / max(total_files, 1)) * 100), 2),
        "total_delete_files": delete_files,
        "total_snapshots": snapshot_info["total_snapshots"],
        "first_snapshot_at": snapshot_info["first_snapshot_at"],
        "last_snapshot_committed_at": snapshot_info["last_snapshot_committed_at"],
        "first_updated_at": snapshot_info["last_snapshot_committed_at"],
        "manifest_count": len(manifests),
        "avg_file_size_mb": avg_file_size_mb,
        "total_partitions": total_partitions,
        "partition_skew_ratio": skew,
        "avg_files_per_partition": avg_files_per_partition,
        "write_distribution_mode": props["write_distribution_mode"],
        "has_sort_order": props["has_sort_order"],
        "target_file_size_bytes": props["target_file_size_bytes"],
        "file_formats": sorted(file_formats),
        "format_version": format_version,
    }
    primary = fields[0] if fields else {}
    cost_analysis: TableCostAnalysis | None = None
    metadata_accounting: MetadataCostAccounting | None = None
    run_metadata_cost = settings.cost_scan_tier in {"metadata", "storage_truth"} and (
        total_files < settings.cost_metadata_max_files_estimate
        and len(manifests) < settings.cost_metadata_max_manifests_current
    )
    if settings.cost_scan_tier in {"metadata", "storage_truth"} and not run_metadata_cost:
        logger.info(
            "Skipping Tier-1 metadata cost for %s.%s (files=%s manifests=%s); using heuristic",
            schema_name,
            table_name,
            total_files,
            len(manifests),
        )
    if run_metadata_cost:
        metadata_accounting = account_metadata_cost(
            table_obj,
            scan_at=scan_time,
            settings=settings,
            small_file_bytes=small_file_bytes,
        )
        cost_stats = CostStatsInput(
            small_files_count=small_files_count,
            small_file_bytes=small_file_bytes,
            target_file_size_bytes=props["target_file_size_bytes"] or 268_435_456,
            query_count_30d=0,
        )
        cost_analysis = build_table_cost_analysis(
            metadata_accounting,
            scan_at=scan_time,
            settings=settings,
            stats=cost_stats,
        )
    if metadata_accounting is not None:
        stats_for_health = enrich_stats_signals(
            stats_for_health,
            metadata_bytes=metadata_accounting.breakdown.metadata_bytes,
            data_bytes=metadata_accounting.breakdown.live_bytes,
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
        stats_for_health, health_small_files, now=scan_time
    )
    column_quality = quality_accum.finish()
    quality_summary = summarize_column_quality(column_quality)
    partitions_for_raw = partition_rows
    puffin_blobs: list[dict[str, Any]] = []
    try:
        from cryo.scanner.puffin_writer import build_sketch_payload, write_puffin_blob

        # The Puffin writer only sees the per-column stats we already aggregated -- this is
        # a zero-scan enrichment, not a separate data pass.
        column_quality_dicts = [
            {
                "field_id": stat.field_id,
                "column_name": stat.column_name,
                "column_type": stat.column_type,
                "null_count": stat.null_count,
                "value_count": stat.value_count,
                "lower_bound": stat.min_value,
                "upper_bound": stat.max_value,
            }
            for stat in column_quality
        ]
        partition_dicts = [partition.__dict__ for partition in partitions_for_raw]
        payload = build_sketch_payload(
            column_quality_dicts,
            partitions=partition_dicts,
        )
        if payload:
            table_uuid = _table_uuid(table_obj)
            blob = write_puffin_blob(
                payload,
                table_id=table_uuid or uuid.uuid4(),
                snapshot_id=snapshot_info.get("current_snapshot_id"),
            )
            puffin_blobs.append(blob.to_dict())
    except Exception as exc:  # pragma: no cover - defensive: never break a scan on sketch failures.
        logger.warning("Puffin sketch write failed: %s", exc)
    raw = {
        "partition_fields": fields,
        "partitions_to_rewrite": raw_partition_preview(partitions_for_raw),
        "file_formats": sorted(file_formats),
        "snapshot_overhead_gb": cost.snapshot_overhead_gb,
        "health_deductions": health_deductions,
        "column_quality": [
            {
                "field_id": stat.field_id,
                "column_name": stat.column_name,
                "column_type": stat.column_type,
                "null_count": stat.null_count,
                "value_count": stat.value_count,
                "null_rate": stat.null_rate,
                "lower_bound": stat.min_value,
                "upper_bound": stat.max_value,
                "bounds_complete": stat.bounds_complete,
            }
            for stat in column_quality
        ],
        **quality_summary,
        "first_snapshot_at": snapshot_info["first_snapshot_at"],
        "last_snapshot_committed_at": snapshot_info["last_snapshot_committed_at"],
        "current_snapshot_id": snapshot_info["current_snapshot_id"],
        "equality_delete_files": equality_delete_files,
        "position_delete_files": position_delete_files,
        "files_below_target": files_below_target,
        "bytes_below_target": bytes_below_target,
        "reduction_target_bytes": reduction_target_bytes,
        "location": table_location(table_obj),
        "iceberg_properties": props["iceberg_properties"],
        "gc_enabled": props["gc_enabled"],
        "wair_file_sample": [wair_file_samples[path] for path in sorted(wair_file_samples)],
        "wair_file_sample_complete": len(wair_file_samples) == total_files,
        "commit_signals": commit_signals(snapshot_info["commits"], now=scan_time),
    }
    for signal_key in (
        "format_version",
        "v3_upgrade_candidate",
        "metadata_to_data_ratio",
        "is_streaming",
        "commits_per_day",
    ):
        if signal_key in stats_for_health:
            raw[signal_key] = stats_for_health[signal_key]
    if manifest_read_errors:
        raw["manifest_read_errors"] = manifest_read_errors
    if puffin_blobs:
        raw["puffin_blobs"] = puffin_blobs
    raw["file_size_bins"] = {
        "data": data_size_bins,
        "delete": delete_size_bins,
        "unknown": unknown_file_sizes,
    }
    raw["manifest_stats"] = manifest_stats(len(manifests), manifest_total_bytes, total_files)
    if cost_analysis is not None:
        raw["cost"] = cost_analysis.raw
    common = {
        "needs_compaction": _needs_compaction(
            total_files, small_files_count, max_files_per_partition
        ),
        "compaction_priority": _priority(total_files, small_files_count, avg_file_size_mb).value,
        "grade": grade,
        "overall_score": score,
        "total_files": total_files,
        "small_files_count": small_files_count,
        "total_partitions": total_partitions,
        "max_files_per_partition": max_files_per_partition,
        "avg_files_per_partition": avg_files_per_partition,
        "partition_skew_ratio": skew,
        "total_records": total_records,
        "total_size_gb": round(total_size_bytes / GB, 6),
        "avg_file_size_mb": round(avg_file_size_mb, 3),
        "total_snapshots": snapshot_info["total_snapshots"],
        "total_delete_files": delete_files,
        "delete_file_pct": stats_for_health["delete_file_pct"],
        "manifest_count": len(manifests),
        # None (not the "_unpartitioned" scan-time display label used elsewhere for humans/UI)
        # -- planner.partitions.derive_partition_fields() treats any truthy partition_col as a
        # real, bindable column name and builds a row-filter term from it. The literal string
        # "_unpartitioned" doesn't exist as a column on any real table, so a job for an
        # unpartitioned table's single pseudo-partition would panic in the Go engine's
        # BuildRowFilter trying to bind it: "invalid schema: could not bind reference
        # '_unpartitioned'".
        "partition_col": primary.get("base_column") if primary else None,
        "partition_col_type": primary.get("col_type") if primary else "",
        "write_distribution_mode": props["write_distribution_mode"],
        "has_sort_order": props["has_sort_order"],
        "target_file_size_bytes": props["target_file_size_bytes"],
        "estimated_monthly_s3_cost_usd": cost.monthly_s3_cost_usd,
        "cost": cost_analysis,
        "raw": raw,
    }

    if schema_name is None or table_name is None:
        return {
            "stats": {**common, "partition_fields": fields},
            "partitions": [partition.__dict__ for partition in partition_rows],
        }
    return TableAnalysis(
        schema_name=str(schema_name),
        table_name=table_name,
        scan_at=scan_time,
        partitions=partition_rows,
        **common,
    )


def iter_data_files(
    table_obj: object,
    manifests: Sequence[object] | None = None,
    *,
    parallel: bool = True,
    max_workers: int = 8,
    manifest_errors: list[str] | None = None,
) -> Iterable[object]:
    """Iterate over data files from a table's current-snapshot manifests.

    Intentionally does **not** use ``table.scan().plan_files()``: that path
    materializes the full FileScanTask graph (including delete associations) and
    dominates RSS on large tables. Stats only need per-file size/records/partition
    from manifest entries.

    When parallel=True, manifests are fetched concurrently with a bounded worker
    pool. Entries from each completed manifest are yielded immediately so at most
    ``max_workers`` entry-lists are retained at once (not the whole snapshot).
    """
    for entry in _iter_live_entries(
        table_obj,
        manifests,
        parallel=parallel,
        max_workers=max_workers,
        manifest_errors=manifest_errors,
    ):
        yield _attr(entry, "data_file", entry)


def _iter_live_entries(
    table_obj: object,
    manifests: Sequence[object] | None = None,
    *,
    parallel: bool = True,
    max_workers: int = 8,
    manifest_errors: list[str] | None = None,
) -> Iterable[object]:
    manifest_list = list(manifests) if manifests is not None else _manifests(table_obj)
    io = _attr(table_obj, "io", None)

    if not parallel or len(manifest_list) <= 1:
        for manifest in manifest_list:
            yield from _iter_manifest_entries(manifest, io, manifest_errors=manifest_errors)
        return

    workers = max(1, min(max_workers, len(manifest_list)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_read_manifest_entries, manifest, io): manifest
            for manifest in manifest_list
        }
        for future in as_completed(futures):
            entries, error = future.result()
            if error and manifest_errors is not None:
                manifest_errors.append(error)
            for entry in entries:
                if _entry_status_deleted(entry):
                    continue
                yield entry


def _iter_manifest_entries(
    manifest: object,
    io: object,
    *,
    manifest_errors: list[str] | None = None,
) -> Iterable[object]:
    entries, error = _read_manifest_entries(manifest, io)
    if error and manifest_errors is not None:
        manifest_errors.append(error)
    for entry in entries:
        if _entry_status_deleted(entry):
            continue
        yield entry


def _read_manifest_entries(manifest: object, io: object) -> tuple[list[object], str | None]:
    """Read manifest entries; return empty list + error message on failure."""
    try:
        return _call_manifest_entries(manifest, io), None
    except Exception as exc:
        logger.warning("Failed to read Iceberg manifest entries: %s", exc)
        return [], str(exc)


def _fetch_manifest_entries_safe(manifest: object, io: object) -> list[object]:
    """Backward-compatible wrapper used by tests."""
    entries, _ = _read_manifest_entries(manifest, io)
    return entries


def partition_fields(table_obj: object) -> list[dict[str, Any]]:
    spec = _call_or_attr(table_obj, "spec", default=None)
    raw_fields = list(_attr(spec, "fields", []) or []) if spec is not None else []
    schema = _call_or_attr(table_obj, "schema", default=None)
    result: list[dict[str, Any]] = []
    for field in raw_fields:
        partition_field = str(_attr(field, "name", "") or "")
        transform_raw = str(_attr(field, "transform", "") or "")
        transform = normalize_transform(transform_raw)
        source_field = _source_field(schema, _attr(field, "source_id", None))
        base_column = str(_attr(source_field, "name", None) or partition_field)
        result.append(
            {
                "base_column": base_column,
                "partition_field": partition_field or base_column,
                "transform": transform_raw or transform.value,
                "transform_param": _transform_param(transform_raw),
                "col_type": str(_attr(source_field, "field_type", "") or ""),
            }
        )
    return result


def data_file_partition_values(
    data_file: object,
    fields: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    partition = _attr(data_file, "partition", None)
    values: dict[str, Any] = {}
    for index, field in enumerate(fields):
        name = str(field["partition_field"])
        values[name] = _jsonable(_partition_value(partition, name, index))
    return values


def partition_key(values: Mapping[str, Any]) -> str:
    if not values:
        return "_unpartitioned"
    return "|".join(f"{key}={values[key]}" for key in sorted(values))


def partition_row(key: str, accum: _PartitionAccum) -> PartitionAnalysis:
    primary = accum.fields[0] if accum.fields else {}
    return PartitionAnalysis(
        partition_value=str(next(iter(accum.values.values()), key)),
        partition_values=dict(accum.values),
        partition_fields=list(accum.fields),
        partition_spec_id=accum.spec_id,
        partition_path=key,
        partition_key=key,
        base_column=primary.get("base_column"),
        base_column_type=primary.get("col_type"),
        transform=primary.get("transform"),
        files_per_partition=accum.files,
        small_files_in_partition_count=accum.small,
        partition_size_gb=round(accum.size_bytes / GB, 6),
        compaction_status="F" if accum.small > 1 and accum.files > 1 else "T",
        raw=_partition_raw(accum),
    )


def _partition_raw(accum: _PartitionAccum) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "partition_values": dict(accum.values),
        "delete_files": accum.delete_files,
        "equality_delete_files": accum.equality_delete_files,
        "equality_delete_bytes": accum.equality_delete_bytes,
    }
    if accum.last_write_ms is not None:
        raw["last_write_at"] = _format_last_write_at(accum.last_write_ms)
    if accum.sample_path:
        raw["sample_file_path"] = accum.sample_path
    return raw


def _note_last_write(accum: _PartitionAccum, write_ms: int | None) -> None:
    if write_ms is None:
        return
    if accum.last_write_ms is None or write_ms > accum.last_write_ms:
        accum.last_write_ms = write_ms


def _format_last_write_at(ms: int) -> str:
    seconds, millis = divmod(ms, 1000)
    utc = datetime.fromtimestamp(seconds, tz=UTC)
    return f"{utc.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"


def _snapshot_times_by_id(table_obj: object) -> dict[int, int]:
    times: dict[int, int] = {}
    for snapshot in _table_snapshots(table_obj):
        snapshot_id = _optional_int(_attr(snapshot, "snapshot_id", None))
        millis = _snapshot_millis(snapshot)
        if snapshot_id is None or millis is None:
            continue
        times[snapshot_id] = millis
    return times


def _entry_write_ms(entry: object, times: Mapping[int, int]) -> int | None:
    """Snapshot time that added the file. When that snapshot has been expired, the file is
    older than the oldest retained snapshot, so that timestamp is a conservative stand-in
    (it can only make a partition look more recently written, never colder)."""
    snapshot_id = _entry_snapshot_id(entry)
    if snapshot_id is None:
        return None
    known = times.get(snapshot_id)
    if known is not None:
        return known
    return min(times.values()) if times else None


def _entry_snapshot_id(entry: object) -> int | None:
    value = getattr(entry, "snapshot_id", None)
    if callable(value):
        try:
            value = value()
        except TypeError:
            return None
    return _optional_int(value)


def _snapshot_millis(snapshot: object) -> int | None:
    raw = _attr(snapshot, "timestamp_ms", None)
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    committed = _attr(snapshot, "committed_at", None)
    if isinstance(committed, datetime):
        utc = committed if committed.tzinfo else committed.replace(tzinfo=UTC)
        return int(utc.timestamp() * 1000)
    return None


def _table_snapshots(table_obj: object) -> list[object]:
    metadata = _attr(table_obj, "metadata", None)
    snapshots = _call_or_attr(table_obj, "snapshots", default=None)
    if snapshots is None:
        snapshots = _attr(metadata, "snapshots", []) or []
    return list(snapshots or [])


def _needs_compaction(total_files: int, small_files: int, max_files_per_partition: int) -> bool:
    return small_files > total_files * 0.2 and max_files_per_partition > 2


def _priority(total_files: int, small_files: int, avg_file_size_mb: float) -> Priority:
    if small_files > total_files * 0.25 or (avg_file_size_mb < 32 and small_files > 1):
        return Priority.HIGH
    if small_files > total_files * 0.1:
        return Priority.MEDIUM
    return Priority.LOW


def _manifests(table_obj: object) -> list[object]:
    snapshot = _call_or_attr(table_obj, "current_snapshot", default=None)
    if snapshot is None:
        return []
    return list(
        _call_or_attr(snapshot, "manifests", _attr(table_obj, "io", None), default=()) or []
    )


def _call_manifest_entries(manifest: object, io: object) -> list[object]:
    fetch = getattr(manifest, "fetch_manifest_entry", None)
    if not callable(fetch):
        return []
    for call in (lambda: fetch(io=io, discard_deleted=True), lambda: fetch(io), fetch):
        try:
            entries = list(call() or [])
            return [entry for entry in entries if not _entry_status_deleted(entry)]
        except TypeError:
            continue
    return []


def _entry_status_deleted(entry: object) -> bool:
    status = _attr(entry, "status", None)
    if status is None:
        return False
    status_str = str(status).upper()
    return status_str.endswith("DELETED") or status_str == "2"


def _snapshot_info(table_obj: object) -> dict[str, Any]:
    metadata = _attr(table_obj, "metadata", None)
    snapshots = _table_snapshots(table_obj)
    committed_at = [
        ts for snapshot in snapshots if (ts := _snapshot_timestamp(snapshot)) is not None
    ]
    first_snapshot_at = min(committed_at) if committed_at else None
    last_snapshot_committed_at = max(committed_at) if committed_at else None
    current_snapshot_id = _attr(metadata, "current_snapshot_id", None)
    if current_snapshot_id is not None:
        try:
            current_snapshot_id = int(current_snapshot_id)
        except (TypeError, ValueError):
            current_snapshot_id = None
    return {
        "total_snapshots": len(snapshots),
        "first_snapshot_at": first_snapshot_at,
        "last_snapshot_committed_at": last_snapshot_committed_at,
        "current_snapshot_id": current_snapshot_id,
        "commits": commits_from_snapshots(snapshots),
    }


_ICEBERG_PROPERTY_KEYS = (
    "write.target-file-size-bytes",
    "write.distribution-mode",
    "write.metadata.delete-after-commit.enabled",
    "write.metadata.previous-versions-max",
    "commit.retry.num-retries",
    "commit.retry.min-wait-ms",
    "commit.retry.max-wait-ms",
    "write.parquet.compression-codec",
    "write.delete.mode",
    "write.update.mode",
    "write.merge.mode",
    "history.expire.max-snapshot-age-ms",
    "history.expire.min-snapshots-to-keep",
    "gc.enabled",
    "format-version",
    "write.delete.target-file-size-bytes",
    "write.format.default",
    "write.parquet.row-group-size-bytes",
    "write.object-storage.enabled",
    "write.data.path",
    "write.delete.granularity",
    "write.upsert.enabled",
    "commit.retry.total-timeout-ms",
    "commit.manifest-merge.enabled",
    "commit.manifest.target-size-bytes",
    "commit.manifest.min-count-to-merge",
    "write.metadata.metrics.default",
    "write.metadata.metrics.max-inferred-column-defaults",
    "history.expire.max-ref-age-ms",
    "comment",
)


def _parse_bool_property(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"true", "1", "yes"}:
        return True
    if lowered in {"false", "0", "no"}:
        return False
    return None


def tracked_iceberg_properties(properties: Mapping[Any, Any]) -> dict[str, str]:
    """The subset of table properties persisted into ``TableStats.raw``."""
    tracked: dict[str, str] = {}
    for key in _ICEBERG_PROPERTY_KEYS:
        if key in properties:
            tracked[key] = str(properties[key])
    for key, value in properties.items():
        key_str = str(key)
        if key_str.startswith("write.parquet.bloom-filter-enabled.column."):
            tracked[key_str] = str(value)
    return tracked


def _has_sort_order(table_obj: object, metadata: object) -> bool:
    """Sort order is table metadata (sort-orders + default-sort-order-id), not a property."""
    sort_order_fn = getattr(table_obj, "sort_order", None)
    if callable(sort_order_fn):
        try:
            return bool(getattr(sort_order_fn(), "fields", None))
        except Exception:
            pass
    default_id = _attr(metadata, "default_sort_order_id", None)
    for order in _attr(metadata, "sort_orders", None) or []:
        if _attr(order, "order_id", None) == default_id:
            return bool(_attr(order, "fields", None))
    return False


def _table_properties(table_obj: object) -> dict[str, Any]:
    metadata = _attr(table_obj, "metadata", None)
    properties = _attr(table_obj, "properties", None) or _attr(metadata, "properties", None) or {}
    if callable(properties):
        properties = properties()
    if not isinstance(properties, Mapping):
        properties = {}
    iceberg_properties = tracked_iceberg_properties(properties)
    target = properties.get("write.target-file-size-bytes")
    return {
        "write_distribution_mode": properties.get("write.distribution-mode"),
        "has_sort_order": _has_sort_order(table_obj, metadata),
        "target_file_size_bytes": int(target)
        if target is not None and str(target).isdigit()
        else None,
        "iceberg_properties": iceberg_properties,
        "gc_enabled": _parse_bool_property(properties.get("gc.enabled")),
    }


def _source_field(schema: object, source_id: object) -> object | None:
    if schema is None or source_id is None:
        return None
    parsed_source_id = _optional_int(source_id)
    if parsed_source_id is None:
        return None
    finder = getattr(schema, "find_field", None)
    if callable(finder):
        return cast(object, finder(parsed_source_id))
    return None


def _partition_value(partition: object, field_name: str, index: int) -> Any:
    if partition is None:
        return None
    if isinstance(partition, Mapping):
        return partition.get(field_name)
    if hasattr(partition, field_name):
        return getattr(partition, field_name)
    if isinstance(partition, Sequence) and not isinstance(partition, str | bytes):
        try:
            return partition[index]
        except IndexError:
            return None
    if hasattr(partition, "__getitem__"):
        try:
            return partition[index]
        except (TypeError, IndexError, KeyError):
            return None
    return None


def _is_delete_file(data_file: object) -> bool:
    return _delete_file_kind(data_file) is not None


def _delete_file_kind(data_file: object) -> str | None:
    content = str(_attr(data_file, "content", "")).lower().strip()
    if "equality" in content:
        return "equality"
    if "position" in content:
        return "position"
    if content.endswith("deletes"):
        return "position"
    if content == "2":
        return "equality"
    if content == "1":
        return "position"
    return None


def _transform_param(transform_raw: str) -> int | None:
    digits = "".join(ch if ch.isdigit() else " " for ch in transform_raw).split()
    return int(digits[0]) if digits else None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _attr(obj: object, name: str, default: Any = None) -> Any:
    value = getattr(obj, name, default)
    return value() if callable(value) and name in {"content", "partition"} else value


def _table_uuid(table_obj: object) -> uuid.UUID | None:
    """Best-effort extraction of a table's Iceberg UUID for the Puffin writer.

    PyIceberg tables expose ``metadata.table_uuid``; for catalogs that don't we fall back
    to a fresh UUID so the Puffin blob path is still unique. This is only used to namespace
    on-disk Puffin files, so a generated UUID is fine for the local-dev case.
    """
    raw = _attr(table_obj, "metadata", None)
    candidate = _attr(raw, "table_uuid", None) if raw is not None else None
    if candidate is None:
        candidate = _attr(table_obj, "table_uuid", None)
    if candidate is None:
        return None
    try:
        return uuid.UUID(str(candidate))
    except (ValueError, TypeError):
        return None


def _call_or_attr(obj: object, name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    value = getattr(obj, name, default)
    if callable(value):
        for call in (lambda: value(*args, **kwargs), lambda: value(*args), value):
            try:
                return call()
            except TypeError:
                continue
    return value
