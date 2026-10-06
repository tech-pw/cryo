"""Deep equality helpers for TableAnalysis parity (no soft skips)."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from cryo.scanner.analyze import PartitionAnalysis, TableAnalysis

# Engine-only annotations; everything else in the scanner contract must match.
_IGNORE_RAW_KEYS = frozenset({"scan_engine", "puffin_blobs"})


def assert_table_analysis_equal(python: TableAnalysis, go: TableAnalysis) -> None:
    """Assert every contract field matches between Python analyze_table and Go mapper."""
    py_norm = normalize_analysis(python)
    go_norm = normalize_analysis(go)
    assert py_norm == go_norm, _diff_message(py_norm, go_norm)


def normalize_analysis(analysis: TableAnalysis) -> dict[str, Any]:
    data = {
        "schema_name": analysis.schema_name,
        "table_name": analysis.table_name,
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
        "cost": None if analysis.cost is None else _to_plain(analysis.cost),
        "partitions": [_normalize_partition(p) for p in analysis.partitions],
        "raw": _normalize_raw(analysis.raw),
    }
    return _normalize_value(data)


def _normalize_partition(part: PartitionAnalysis) -> dict[str, Any]:
    return {
        "partition_value": part.partition_value,
        "partition_values": dict(part.partition_values),
        "partition_fields": list(part.partition_fields),
        "partition_spec_id": part.partition_spec_id,
        "partition_path": part.partition_path,
        "partition_key": part.partition_key,
        "base_column": part.base_column,
        "base_column_type": part.base_column_type,
        "transform": part.transform,
        "files_per_partition": part.files_per_partition,
        "small_files_in_partition_count": part.small_files_in_partition_count,
        "partition_size_gb": part.partition_size_gb,
        "compaction_status": part.compaction_status,
        "raw": dict(part.raw),
    }


def _normalize_raw(raw: dict[str, Any]) -> dict[str, Any]:
    cleaned = {k: v for k, v in raw.items() if k not in _IGNORE_RAW_KEYS}
    # partitions_to_rewrite mirrors partitions; normalize the same way.
    if "partitions_to_rewrite" in cleaned:
        rows = []
        for row in cleaned["partitions_to_rewrite"]:
            if isinstance(row, PartitionAnalysis):
                rows.append(_normalize_partition(row))
            elif isinstance(row, dict):
                rows.append(
                    {
                        "partition_value": row.get("partition_value"),
                        "partition_values": dict(row.get("partition_values") or {}),
                        "partition_fields": list(row.get("partition_fields") or []),
                        "partition_spec_id": row.get("partition_spec_id"),
                        "partition_path": row.get("partition_path"),
                        "partition_key": row.get("partition_key"),
                        "base_column": row.get("base_column"),
                        "base_column_type": row.get("base_column_type"),
                        "transform": row.get("transform"),
                        "files_per_partition": row.get("files_per_partition"),
                        "small_files_in_partition_count": row.get("small_files_in_partition_count"),
                        "partition_size_gb": row.get("partition_size_gb"),
                        "compaction_status": row.get("compaction_status"),
                        "raw": dict(row.get("raw") or {}),
                    }
                )
            else:
                rows.append(row)
        cleaned["partitions_to_rewrite"] = rows
    if "column_quality" in cleaned:
        cols = []
        for col in cleaned["column_quality"] or []:
            if not isinstance(col, dict):
                cols.append(col)
                continue
            cols.append(
                {
                    "field_id": col.get("field_id"),
                    "column_name": col.get("column_name"),
                    "column_type": col.get("column_type"),
                    "null_count": col.get("null_count"),
                    "value_count": col.get("value_count"),
                    "null_rate": col.get("null_rate"),
                    "lower_bound": col.get("lower_bound"),
                    "upper_bound": col.get("upper_bound"),
                }
            )
        cleaned["column_quality"] = cols
    return cleaned


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        ts = value if value.tzinfo else value.replace(tzinfo=UTC)
        return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    if isinstance(value, float):
        return round(value, 10)
    if isinstance(value, dict):
        return {
            str(k): _normalize_value(v) for k, v in sorted(value.items(), key=lambda i: str(i[0]))
        }
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    if isinstance(value, tuple):
        return [_normalize_value(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _normalize_value(asdict(value))
    return value


def _to_plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return value


def _diff_message(left: dict[str, Any], right: dict[str, Any]) -> str:
    diffs: list[str] = []
    _collect_diffs("", left, right, diffs)
    preview = "\n".join(diffs[:40])
    if len(diffs) > 40:
        preview += f"\n... and {len(diffs) - 40} more"
    return f"Python vs Go TableAnalysis mismatch ({len(diffs)} diffs):\n{preview}"


def _collect_diffs(path: str, left: Any, right: Any, out: list[str]) -> None:
    if type(left) is not type(right) and not (
        isinstance(left, (int, float)) and isinstance(right, (int, float))
    ):
        out.append(f"{path or '<'}: type {type(left).__name__} != {type(right).__name__}")
        out.append(f"  py={left!r}")
        out.append(f"  go={right!r}")
        return
    if isinstance(left, dict) and isinstance(right, dict):
        keys = sorted(set(left) | set(right))
        for key in keys:
            child = f"{path}.{key}" if path else key
            if key not in left:
                out.append(f"{child}: missing on Python side (go={right[key]!r})")
            elif key not in right:
                out.append(f"{child}: missing on Go side (py={left[key]!r})")
            else:
                _collect_diffs(child, left[key], right[key], out)
        return
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            out.append(f"{path}: list len {len(left)} != {len(right)}")
        for idx, (lv, rv) in enumerate(zip(left, right, strict=False)):
            _collect_diffs(f"{path}[{idx}]", lv, rv, out)
        return
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if abs(float(left) - float(right)) > 1e-9:
            out.append(f"{path}: {left!r} != {right!r}")
        return
    if left != right:
        out.append(f"{path}: {left!r} != {right!r}")
