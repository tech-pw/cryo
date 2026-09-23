"""Go scan-stats subprocess adapter for current-snapshot Iceberg aggregates."""

from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from cryo.config import Settings, get_settings
from cryo.cost.model import CostStatsInput
from cryo.engines.native.payload import source_catalog_config
from cryo.engines.native.runner import resolve_binary_path
from cryo.scanner.analyze import (
    HEALTH_SMALL_FILE_BYTES,
    PartitionAnalysis,
    TableAnalysis,
    _needs_compaction,
    _priority,
)
from cryo.scanner.cost import (
    GB,
    MB,
    account_metadata_cost,
    build_table_cost_analysis,
    estimate_storage_cost,
)
from cryo.scanner.health import compute_health_score
from cryo.scanner.signals import enrich_stats_signals

logger = logging.getLogger(__name__)


def analyze_catalog_table_go(
    *,
    source_config: Mapping[str, Any],
    schema_name: str,
    table_name: str,
    scan_at: datetime | None = None,
    settings: Settings | None = None,
    stats_history: Sequence[Mapping[str, Any]] | None = None,
    table_obj: object | None = None,
    table_obj_loader: Callable[[], object] | None = None,
) -> TableAnalysis:
    """Run cryo-engine-go scan-stats and map the result into TableAnalysis.

    Health scoring and optional Tier-1 cost still run in Python. When a table
    handle is available (``table_obj`` directly, or lazily via
    ``table_obj_loader`` once size/manifest thresholds are known to pass) and
    cost tier is metadata, cost accounting uses the PyIceberg table handle
    (historical walk); otherwise cost stays heuristic/Tier-0 only.
    """
    settings = settings or get_settings()
    scan_time = scan_at or datetime.now(UTC)
    if scan_time.tzinfo is None:
        scan_time = scan_time.replace(tzinfo=UTC)

    request = _build_scan_stats_request(
        source_config,
        schema_name=schema_name,
        table_name=table_name,
        settings=settings,
    )
    payload = run_scan_stats(request, settings=settings)
    return table_analysis_from_go_payload(
        payload,
        schema_name=schema_name,
        table_name=table_name,
        scan_at=scan_time,
        settings=settings,
        stats_history=stats_history,
        table_obj=table_obj,
        table_obj_loader=table_obj_loader,
    )


def _parse_engine_payload(
    stdout: str,
    *,
    command: str,
    returncode: int,
    stderr: str,
) -> dict[str, Any]:
    """Extract the JSON response object from an engine binary's stdout.

    The whole buffer is tried first because the engine writes exactly one JSON
    document; the per-line pass only exists for the case where it also emitted
    progress lines. Lines are split on ``\\n`` rather than with ``splitlines()``
    because ``splitlines()`` also breaks on U+0085, U+2028 and U+2029, which are
    legal unescaped characters inside a JSON string (Go's encoder only escapes
    below U+0020) and do occur in sampled row data — that shreds a valid
    single-line response into fragments, none of which parse on their own.
    """
    try:
        whole = json.loads(stdout)
    except json.JSONDecodeError:
        whole = None
    if isinstance(whole, dict):
        return whole
    for raw_line in reversed(stdout.split("\n")):
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    detail = f"{command} stdout was not JSON (exit={returncode}): {stdout[:500]}"
    if stderr:
        detail = f"{detail} | stderr: {stderr[:200]}"
    raise RuntimeError(detail)


def run_scan_stats(
    request: Mapping[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    import subprocess

    settings = settings or get_settings()
    binary = _resolve_scan_binary(settings)
    if not binary:
        raise RuntimeError("cryo-engine-go binary not found for scan-stats")

    proc = subprocess.run(
        [binary, "scan-stats"],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    if not stdout:
        raise RuntimeError(
            f"scan-stats produced no JSON (exit={proc.returncode}): {stderr or 'empty stderr'}"
        )
    payload = _parse_engine_payload(
        stdout, command="scan-stats", returncode=proc.returncode, stderr=stderr
    )
    if not payload.get("ok", False):
        raise RuntimeError(payload.get("error") or f"scan-stats failed (exit={proc.returncode})")
    return payload


def table_analysis_from_go_payload(
    payload: Mapping[str, Any],
    *,
    schema_name: str,
    table_name: str,
    scan_at: datetime,
    settings: Settings,
    stats_history: Sequence[Mapping[str, Any]] | None = None,
    table_obj: object | None = None,
    table_obj_loader: Callable[[], object] | None = None,
) -> TableAnalysis:
    fields = list(payload.get("partition_fields") or [])
    # Drop Go-only field_id from persisted partition_fields shape.
    clean_fields = [
        {
            "base_column": f.get("base_column"),
            "partition_field": f.get("partition_field"),
            "transform": f.get("transform"),
            "transform_param": f.get("transform_param"),
            "col_type": f.get("col_type") or "",
        }
        for f in fields
        if isinstance(f, Mapping)
    ]
    partition_rows = [
        _partition_from_go(part, clean_fields) for part in (payload.get("partitions") or [])
    ]
    total_files = int(payload.get("total_files") or 0)
    total_size_bytes = int(payload.get("total_size_bytes") or 0)
    total_records = int(payload.get("total_records") or 0)
    delete_files = int(payload.get("delete_files") or 0)
    delete_bytes = int(payload.get("delete_bytes") or 0)
    health_small_files = int(payload.get("health_small_files") or 0)
    equality_delete_files = int(payload.get("equality_delete_files") or 0)
    position_delete_files = int(payload.get("position_delete_files") or 0)
    manifest_count = int(payload.get("manifest_count") or 0)
    manifest_total_bytes = int(payload.get("manifest_total_bytes") or 0)
    total_snapshots = int(payload.get("total_snapshots") or 0)
    file_formats = [str(f).upper() for f in (payload.get("file_formats") or [])]

    # Prefer Go-computed full-map totals (correct under MaxPartitions truncation).
    total_partitions = int(payload.get("total_partitions") or len(partition_rows))
    if payload.get("small_files_count") is not None:
        small_files_count = int(payload["small_files_count"])
    else:
        small_files_count = sum(
            row.small_files_in_partition_count
            for row in partition_rows
            if row.compaction_status == "F"
        )
    if payload.get("max_files_per_partition") is not None:
        max_files_per_partition = int(payload["max_files_per_partition"])
    else:
        max_files_per_partition = max(
            (row.files_per_partition for row in partition_rows), default=0
        )
    if payload.get("avg_files_per_partition") is not None:
        avg_files_per_partition = float(payload["avg_files_per_partition"])
    else:
        files_per_partition = [row.files_per_partition for row in partition_rows]
        avg_files_per_partition = (
            round(sum(files_per_partition) / len(files_per_partition), 1)
            if files_per_partition
            else 0.0
        )
    if payload.get("partition_skew_ratio") is not None:
        skew = float(payload["partition_skew_ratio"])
    else:
        files_per_partition = [row.files_per_partition for row in partition_rows]
        median_files = statistics.median(files_per_partition) if files_per_partition else 0
        skew = (
            round(max_files_per_partition / max(median_files, 1), 1) if files_per_partition else 0.0
        )
    small_file_bytes = sum(
        int(part.get("size_bytes") or 0)
        for part in (payload.get("partitions") or [])
        if isinstance(part, Mapping)
        and int(part.get("small") or 0) > 1
        and int(part.get("files") or 0) > 1
    )
    avg_file_size_mb = (total_size_bytes / max(total_files, 1)) / MB if total_files else 0.0

    cost = estimate_storage_cost(
        total_size_bytes=total_size_bytes,
        total_delete_bytes=delete_bytes,
        manifest_total_bytes=manifest_total_bytes,
        total_snapshots=total_snapshots,
    )
    write_distribution_mode = payload.get("write_distribution_mode")
    has_sort_order = payload.get("has_sort_order")
    target_file_size_bytes = payload.get("target_file_size_bytes")
    if target_file_size_bytes is not None:
        target_file_size_bytes = int(target_file_size_bytes)
    location = payload.get("location") or None
    first_snapshot_at = _parse_snapshot_ts(payload.get("first_snapshot_at"))
    last_snapshot_committed_at = _parse_snapshot_ts(payload.get("last_snapshot_committed_at"))

    column_quality = [
        {
            "field_id": int(col.get("field_id") or 0),
            "column_name": col.get("column_name"),
            "column_type": col.get("column_type"),
            "null_count": int(col.get("null_count") or 0),
            "value_count": int(col.get("value_count") or 0),
            "null_rate": float(col.get("null_rate") or 0.0),
            "lower_bound": col.get("lower_bound"),
            "upper_bound": col.get("upper_bound"),
        }
        for col in (payload.get("column_quality") or [])
        if isinstance(col, Mapping)
    ]
    # Recompute summary in Python so banker's rounding matches analyze_table.
    quality_summary = _summarize_column_quality(column_quality)
    avg_null_rate = quality_summary["avg_null_rate"]
    max_null_rate = quality_summary["max_null_rate"]
    worst_null_column = quality_summary["worst_null_column"]

    stats_for_health: dict[str, Any] = {
        "total_files": total_files,
        # Clamped to 100: delete_files can outnumber total_files on a heavily MOR-fragmented
        # table (total_files correctly excludes delete files as of 2026-07-28 -- see
        # engine-go/internal/scanstats/scan.go), so this ratio is no longer bounded at 100%
        # by construction. incremental.py already clamped this same ratio; this just matches.
        "delete_file_pct": round(min(100.0, (delete_files / max(total_files, 1)) * 100), 2),
        "total_delete_files": delete_files,
        "total_snapshots": total_snapshots,
        "first_snapshot_at": first_snapshot_at,
        "last_snapshot_committed_at": last_snapshot_committed_at,
        "first_updated_at": last_snapshot_committed_at,
        "manifest_count": manifest_count,
        "avg_file_size_mb": avg_file_size_mb,
        "total_partitions": total_partitions,
        "partition_skew_ratio": skew,
        "avg_files_per_partition": avg_files_per_partition,
        "write_distribution_mode": write_distribution_mode,
        "has_sort_order": has_sort_order,
        "target_file_size_bytes": target_file_size_bytes,
        "file_formats": file_formats,
        "format_version": payload.get("format_version"),
    }

    cost_analysis = None
    metadata_accounting = None
    # Check tier + size eligibility (cheap: settings + the Go engine's own payload, already
    # computed) before ever resolving a table handle -- a huge table that will just get
    # filtered out below shouldn't pay for a catalog load it can't use.
    eligible_for_metadata_cost = (
        settings.cost_scan_tier in {"metadata", "storage_truth"}
        and total_files < settings.cost_metadata_max_files_estimate
        and manifest_count < settings.cost_metadata_max_manifests_current
    )
    resolved_table_obj = table_obj
    if eligible_for_metadata_cost and resolved_table_obj is None and table_obj_loader is not None:
        try:
            resolved_table_obj = table_obj_loader()
        except Exception as exc:  # noqa: BLE001 - Tier-1 cost is an enhancement, not required
            logger.warning(
                "Failed to load table handle for Tier-1 cost accounting on %s.%s: %s",
                schema_name,
                table_name,
                exc,
            )
            resolved_table_obj = None
    run_metadata_cost = eligible_for_metadata_cost and resolved_table_obj is not None
    if run_metadata_cost:
        metadata_accounting = account_metadata_cost(
            resolved_table_obj,
            scan_at=scan_at,
            settings=settings,
            small_file_bytes=small_file_bytes,
        )
        cost_analysis = build_table_cost_analysis(
            metadata_accounting,
            scan_at=scan_at,
            settings=settings,
            stats=CostStatsInput(
                small_files_count=small_files_count,
                small_file_bytes=small_file_bytes,
                target_file_size_bytes=target_file_size_bytes or 268_435_456,
                query_count_30d=0,
            ),
        )

    if metadata_accounting is not None:
        stats_for_health = enrich_stats_signals(
            stats_for_health,
            metadata_bytes=metadata_accounting.breakdown.metadata_bytes,
            data_bytes=metadata_accounting.breakdown.live_bytes,
            format_version=payload.get("format_version"),
            history=stats_history,
        )
    else:
        stats_for_health = enrich_stats_signals(
            stats_for_health,
            format_version=payload.get("format_version"),
            history=stats_history,
        )

    score, grade, health_deductions = compute_health_score(
        stats_for_health, health_small_files, now=scan_at
    )
    primary = clean_fields[0] if clean_fields else {}
    raw: dict[str, Any] = {
        "partition_fields": clean_fields,
        "partitions_to_rewrite": [p.__dict__ for p in partition_rows],
        "file_formats": file_formats,
        "snapshot_overhead_gb": cost.snapshot_overhead_gb,
        "health_deductions": health_deductions,
        "column_quality": column_quality,
        "avg_null_rate": float(avg_null_rate),
        "max_null_rate": float(max_null_rate),
        "worst_null_column": worst_null_column,
        "first_snapshot_at": first_snapshot_at,
        "last_snapshot_committed_at": last_snapshot_committed_at,
        "current_snapshot_id": payload.get("current_snapshot_id"),
        "equality_delete_files": equality_delete_files,
        "position_delete_files": position_delete_files,
        "location": location,
        "iceberg_properties": dict(payload.get("iceberg_properties") or {}),
        "gc_enabled": payload.get("gc_enabled"),
        "manifest_stats": {
            "manifest_count": manifest_count,
            "avg_files_per_manifest": round(total_files / max(manifest_count, 1), 2),
        },
        "scan_engine": "go",
        "wair_file_sample": list(payload.get("wair_file_sample") or []),
        "wair_file_sample_complete": total_files == 0
        or bool(payload.get("wair_file_sample_complete")),
    }
    if payload.get("manifest_read_errors"):
        raw["manifest_read_errors"] = list(payload["manifest_read_errors"])
    if cost_analysis is not None:
        raw["cost"] = cost_analysis.raw
    for signal_key in (
        "format_version",
        "v3_upgrade_candidate",
        "metadata_to_data_ratio",
        "is_streaming",
        "commits_per_day",
    ):
        if signal_key in stats_for_health:
            raw[signal_key] = stats_for_health[signal_key]

    return TableAnalysis(
        schema_name=schema_name,
        table_name=table_name,
        scan_at=scan_at,
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
        total_size_gb=round(total_size_bytes / GB, 6),
        avg_file_size_mb=round(avg_file_size_mb, 3),
        total_snapshots=total_snapshots,
        total_delete_files=delete_files,
        delete_file_pct=float(stats_for_health["delete_file_pct"]),
        manifest_count=manifest_count,
        # None, not the "_unpartitioned" display sentinel -- see analyze.py's identical field
        # for why: a truthy partition_col gets treated as a real, bindable column name by the
        # planner, and "_unpartitioned" panics the Go engine's row-filter binder.
        partition_col=primary.get("base_column") if primary else None,
        partition_col_type=primary.get("col_type") if primary else "",
        write_distribution_mode=write_distribution_mode
        if isinstance(write_distribution_mode, str)
        else None,
        has_sort_order=bool(has_sort_order) if has_sort_order is not None else None,
        target_file_size_bytes=target_file_size_bytes,
        estimated_monthly_s3_cost_usd=cost.monthly_s3_cost_usd,
        cost=cost_analysis,
        partitions=partition_rows,
        raw=raw,
    )


def _partition_from_go(part: Mapping[str, Any], fields: list[dict[str, Any]]) -> PartitionAnalysis:
    values = dict(part.get("partition_values") or {})
    key = str(part.get("partition_key") or "_unpartitioned")
    files = int(part.get("files") or 0)
    small = int(part.get("small") or 0)
    delete_files = int(part.get("delete_files") or 0)
    size_bytes = int(part.get("size_bytes") or 0)
    primary = fields[0] if fields else {}
    spec_id = part.get("partition_spec_id")
    raw_value = part.get("partition_value")
    if raw_value is None:
        raw_value = next(iter(values.values()), key)
    return PartitionAnalysis(
        partition_value=str(raw_value),
        partition_values=values,
        partition_fields=list(fields),
        partition_spec_id=int(spec_id) if spec_id is not None else None,
        partition_path=key,
        partition_key=key,
        base_column=primary.get("base_column"),
        base_column_type=primary.get("col_type"),
        transform=primary.get("transform"),
        files_per_partition=files,
        small_files_in_partition_count=small,
        partition_size_gb=round(size_bytes / GB, 6),
        compaction_status="F" if small > 1 and files > 1 else "T",
        raw={"partition_values": values, "delete_files": delete_files},
    )


def _build_scan_stats_request(
    source_config: Mapping[str, Any],
    *,
    schema_name: str,
    table_name: str,
    settings: Settings,
) -> dict[str, Any]:
    catalog = source_catalog_config(source_config)
    table_ref = f"{schema_name}.{table_name}"
    request: dict[str, Any] = {
        **catalog,
        "table_ref": table_ref,
        "small_file_mb": settings.scan_small_file_mb,
        "health_small_file_bytes": HEALTH_SMALL_FILE_BYTES,
        "max_partitions": settings.scanner_max_partitions_per_table,
        "max_file_samples": settings.scanner_wair_file_sample_size
        if settings.scanner_wair_file_sample_size
        else -1,
    }
    return request


def run_aggregate_fixture(
    request: Mapping[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run cryo-engine-go aggregate-fixture (parity tests; no live catalog)."""
    import subprocess

    settings = settings or get_settings()
    binary = _resolve_scan_binary(settings)
    if not binary:
        raise RuntimeError("cryo-engine-go binary not found for aggregate-fixture")

    proc = subprocess.run(
        [binary, "aggregate-fixture"],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    if not stdout:
        raise RuntimeError(
            f"aggregate-fixture produced no JSON (exit={proc.returncode}): "
            f"{stderr or 'empty stderr'}"
        )
    payload = _parse_engine_payload(
        stdout, command="aggregate-fixture", returncode=proc.returncode, stderr=stderr
    )
    if not payload.get("ok", False):
        raise RuntimeError(
            payload.get("error") or f"aggregate-fixture failed (exit={proc.returncode})"
        )
    return payload


def _parse_snapshot_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _summarize_column_quality(columns: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [c for c in columns if int(c.get("value_count") or 0) > 0]
    if not scored:
        return {"avg_null_rate": 0.0, "max_null_rate": 0.0, "worst_null_column": None}
    avg_null_rate = round(sum(float(c.get("null_rate") or 0.0) for c in scored) / len(scored), 4)
    worst = max(scored, key=lambda c: float(c.get("null_rate") or 0.0))
    worst_rate = float(worst.get("null_rate") or 0.0)
    return {
        "avg_null_rate": avg_null_rate,
        "max_null_rate": worst_rate,
        "worst_null_column": worst.get("column_name") if worst_rate > 0 else None,
    }


def _resolve_scan_binary(settings: Settings) -> str | None:
    config: dict[str, Any] = {}
    if settings.scan_go_binary:
        config["binary_path"] = settings.scan_go_binary
    return resolve_binary_path(config)
