from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from cryo.config import Settings, get_settings
from cryo.cost.model import CostStatsInput
from cryo.domain.enums import Priority
from cryo.planner.partitions import normalize_transform
from cryo.scanner.cost import (
    GB,
    MB,
    MetadataCostAccounting,
    TableCostAnalysis,
    account_metadata_cost,
    build_table_cost_analysis,
    estimate_storage_cost,
)
from cryo.scanner.health import compute_health_score

HEALTH_SMALL_FILE_BYTES = 8 * MB


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


def analyze_catalog_table(
    catalog: object,
    schema_name: str,
    table_name: str,
    *,
    scan_at: datetime | None = None,
    small_file_mb: float = 89.6,
    file_size_threshold_mb: int = 128,
    settings: Settings | None = None,
) -> TableAnalysis:
    del file_size_threshold_mb
    table_obj = cast(_LoadableCatalog, catalog).load_table(schema_name, table_name)
    effective_settings = settings or Settings(scan_small_file_mb=small_file_mb)
    result = analyze_table(
        table_obj, schema_name, table_name, scan_at=scan_at, settings=effective_settings
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
    partitions: dict[str, _PartitionAccum] = {}
    total_files = total_records = total_size_bytes = 0
    delete_files = delete_bytes = health_small_files = 0
    file_formats: set[str] = set()

    for data_file in iter_data_files(table_obj, manifests):
        total_files += 1
        file_size = int(_attr(data_file, "file_size_in_bytes", 0) or 0)
        total_size_bytes += file_size
        total_records += int(_attr(data_file, "record_count", 0) or 0)
        file_format = _attr(data_file, "file_format", None)
        if file_format:
            file_formats.add(str(file_format).upper())
        if _is_delete_file(data_file):
            delete_files += 1
            delete_bytes += file_size
        if file_size < HEALTH_SMALL_FILE_BYTES:
            health_small_files += 1

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

    partition_rows = [
        partition_row(key, accum)
        for key, accum in sorted(partitions.items(), key=lambda item: item[0])
    ]
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
        row.small_files_in_partition_count
        for row in partition_rows
        if row.compaction_status == "F"
    )
    small_file_bytes = sum(
        accum.size_bytes
        for accum in partitions.values()
        if accum.small > 1 and accum.files > 1
    )
    avg_file_size_mb = (total_size_bytes / max(total_files, 1)) / MB if total_files else 0.0
    snapshot_info = _snapshot_info(table_obj)
    props = _table_properties(table_obj)
    cost = estimate_storage_cost(
        total_size_bytes=total_size_bytes,
        total_delete_bytes=delete_bytes,
        manifest_total_bytes=sum(
            int(_attr(manifest, "manifest_length", 0) or 0) for manifest in manifests
        ),
        total_snapshots=snapshot_info["total_snapshots"],
    )
    stats_for_health = {
        "total_files": total_files,
        "delete_file_pct": round((delete_files / max(total_files, 1)) * 100, 2),
        "total_delete_files": delete_files,
        "total_snapshots": snapshot_info["total_snapshots"],
        "first_updated_at": snapshot_info["first_updated_at"],
        "manifest_count": len(manifests),
        "avg_file_size_mb": avg_file_size_mb,
        "total_partitions": len(partition_rows),
        "partition_skew_ratio": skew,
        "avg_files_per_partition": avg_files_per_partition,
        "write_distribution_mode": props["write_distribution_mode"],
        "has_sort_order": props["has_sort_order"],
        "target_file_size_bytes": props["target_file_size_bytes"],
        "file_formats": sorted(file_formats),
    }
    score, grade = compute_health_score(stats_for_health, health_small_files, now=scan_time)
    primary = fields[0] if fields else {}
    cost_analysis: TableCostAnalysis | None = None
    metadata_accounting: MetadataCostAccounting | None = None
    if settings.cost_scan_tier in {"metadata", "storage_truth"}:
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
    raw = {
        "partition_fields": fields,
        "partitions_to_rewrite": [partition.__dict__ for partition in partition_rows],
        "file_formats": sorted(file_formats),
        "snapshot_overhead_gb": cost.snapshot_overhead_gb,
    }
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
        "total_partitions": len(partition_rows),
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
        "partition_col": primary.get("base_column") if primary else "_unpartitioned",
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
    table_obj: object, manifests: Sequence[object] | None = None
) -> Iterable[object]:
    scan = _call_or_attr(table_obj, "scan", default=None)
    if scan is not None:
        plan_files = _call_or_attr(scan, "plan_files", default=None)
        if plan_files is not None:
            for task in plan_files:
                yield _attr(task, "file", task)
            return

    for manifest in manifests if manifests is not None else _manifests(table_obj):
        for entry in _call_manifest_entries(manifest, _attr(table_obj, "io", None)):
            if _entry_status_deleted(entry):
                continue
            yield _attr(entry, "data_file", entry)


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
        raw={"partition_values": dict(accum.values)},
    )


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
    snapshots = _call_or_attr(table_obj, "snapshots", default=None)
    if snapshots is None:
        snapshots = _attr(metadata, "snapshots", []) or []
    snapshots = list(snapshots or [])
    first_updated_at = None
    if snapshots:
        first_updated_at = _attr(snapshots[0], "committed_at", None) or _attr(
            snapshots[0], "timestamp_ms", None
        )
    return {"total_snapshots": len(snapshots), "first_updated_at": first_updated_at}


def _table_properties(table_obj: object) -> dict[str, Any]:
    metadata = _attr(table_obj, "metadata", None)
    properties = _attr(table_obj, "properties", None) or _attr(metadata, "properties", None) or {}
    if callable(properties):
        properties = properties()
    if not isinstance(properties, Mapping):
        properties = {}
    target = properties.get("write.target-file-size-bytes")
    return {
        "write_distribution_mode": properties.get("write.distribution-mode"),
        "has_sort_order": bool(properties.get("write.sort-order")),
        "target_file_size_bytes": int(target)
        if target is not None and str(target).isdigit()
        else None,
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
    content = str(_attr(data_file, "content", "")).lower()
    return content.endswith("deletes") or content in {"1", "2"}


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


def _call_or_attr(obj: object, name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    value = getattr(obj, name, default)
    if callable(value):
        for call in (lambda: value(*args, **kwargs), lambda: value(*args), value):
            try:
                return call()
            except TypeError:
                continue
    return value
