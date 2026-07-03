from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from cryo.config import Settings
from cryo.cost.model import CostStatsInput, ReclaimBytes, project_savings
from cryo.domain.enums import CostPrecision, OperationType
from cryo.domain.models import SavingsProjection, StorageBreakdown

_OPERATION_RECLAIM_FIELDS: tuple[tuple[OperationType, str], ...] = (
    (OperationType.EXPIRE_SNAPSHOTS, "reclaim_expire_bytes"),
    (OperationType.REMOVE_ORPHAN_FILES, "reclaim_orphan_bytes"),
    (OperationType.OPTIMIZE, "reclaim_optimize_bytes"),
    (OperationType.OPTIMIZE_MANIFESTS, "reclaim_manifests_bytes"),
)


def _stats_with_preview_params(stats: dict[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(stats)
    raw = dict(merged.get("raw") or {})
    target = params.get("target_file_size") or params.get("target_max_file_size_bytes")
    if target is not None:
        merged["target_file_size_bytes"] = int(target)
    retention_days = params.get("retention_days")
    retain_last = params.get("retain_last")
    if retention_days is not None or retain_last is not None:
        metadata = dict(raw.get("metadata") or {})
        if retention_days is not None:
            metadata["retention_days"] = int(retention_days)
        if retain_last is not None:
            metadata["retain_last"] = int(retain_last)
        raw["metadata"] = metadata
        merged["raw"] = raw
    return merged


def savings_projection_from_stats(
    stats: dict[str, Any],
    settings: Settings,
    *,
    params: Mapping[str, Any] | None = None,
) -> SavingsProjection:
    effective_stats = _stats_with_preview_params(stats, params or {})
    raw = dict(effective_stats.get("raw") or {})
    breakdown = StorageBreakdown(
        live_bytes=int(raw.get("live_bytes") or effective_stats.get("live_bytes") or 0),
        dead_bytes=int(raw.get("dead_bytes") or effective_stats.get("dead_bytes") or 0),
        metadata_bytes=int(raw.get("metadata_bytes") or effective_stats.get("metadata_bytes") or 0),
        delete_bytes=int(raw.get("delete_bytes") or effective_stats.get("delete_bytes") or 0),
        orphan_bytes=int(raw.get("orphan_bytes") or effective_stats.get("orphan_bytes") or 0),
    )
    reclaim = ReclaimBytes(
        reclaim_expire_bytes=int(
            raw.get("reclaim_expire_bytes") or effective_stats.get("reclaim_expire_bytes") or 0
        ),
        reclaim_orphan_bytes=int(
            raw.get("reclaim_orphan_bytes") or effective_stats.get("reclaim_orphan_bytes") or 0
        ),
        reclaim_optimize_bytes=int(
            raw.get("reclaim_optimize_bytes") or effective_stats.get("reclaim_optimize_bytes") or 0
        ),
        reclaim_manifests_bytes=int(
            raw.get("reclaim_manifests_bytes")
            or effective_stats.get("reclaim_manifests_bytes")
            or 0
        ),
    )
    cost_stats = CostStatsInput(
        small_files_count=int(effective_stats.get("small_files_count") or 0),
        small_file_bytes=int(
            raw.get("small_file_bytes") or effective_stats.get("small_file_bytes") or 0
        ),
        target_file_size_bytes=int(effective_stats.get("target_file_size_bytes") or 268_435_456),
        query_count_30d=int(effective_stats.get("query_count_30d") or 0),
    )
    precision_raw = effective_stats.get("cost_precision") or raw.get("precision")
    precision = CostPrecision(precision_raw) if precision_raw else CostPrecision.METADATA
    assumptions: dict[str, Any] = {
        "source": "persisted_scan",
        "retention_days": params.get("retention_days") if params else None,
        "retain_last": params.get("retain_last") if params else None,
        "target_file_size_bytes": effective_stats.get("target_file_size_bytes"),
    }
    metadata = raw.get("metadata")
    if isinstance(metadata, dict):
        if assumptions["retention_days"] is None:
            assumptions["retention_days"] = metadata.get("retention_days")
        if assumptions["retain_last"] is None:
            assumptions["retain_last"] = metadata.get("retain_last")
    return project_savings(
        breakdown,
        reclaim,
        cost_stats,
        settings,
        precision=precision,
        assumptions={k: v for k, v in assumptions.items() if v is not None},
    )


def savings_for_plan_operations(
    projection: SavingsProjection,
    operations: Sequence[OperationType | str],
) -> float:
    resolved = {OperationType(op) if not isinstance(op, OperationType) else op for op in operations}
    return round(sum(projection.by_operation.get(op, 0.0) for op in resolved), 4)


def reclaim_bytes_for_plan_operations(
    stats: dict[str, Any], operations: Sequence[OperationType | str]
) -> int:
    raw = dict(stats.get("raw") or {})
    reclaim = ReclaimBytes(
        reclaim_expire_bytes=int(
            raw.get("reclaim_expire_bytes") or stats.get("reclaim_expire_bytes") or 0
        ),
        reclaim_orphan_bytes=int(
            raw.get("reclaim_orphan_bytes") or stats.get("reclaim_orphan_bytes") or 0
        ),
        reclaim_optimize_bytes=int(
            raw.get("reclaim_optimize_bytes") or stats.get("reclaim_optimize_bytes") or 0
        ),
        reclaim_manifests_bytes=int(
            raw.get("reclaim_manifests_bytes") or stats.get("reclaim_manifests_bytes") or 0
        ),
    )
    resolved = {OperationType(op) if not isinstance(op, OperationType) else op for op in operations}
    total = 0
    for operation, field_name in _OPERATION_RECLAIM_FIELDS:
        if operation in resolved:
            total += int(getattr(reclaim, field_name) or 0)
    return total


def savings_projection_for_plan(
    stats: dict[str, Any],
    settings: Settings,
    *,
    params: Mapping[str, Any],
    operations: Sequence[OperationType | str],
) -> SavingsProjection:
    projection = savings_projection_from_stats(stats, settings, params=params)
    resolved = {OperationType(op) if not isinstance(op, OperationType) else op for op in operations}
    filtered_ops = {
        operation: amount
        for operation, amount in projection.by_operation.items()
        if operation in resolved
    }
    planned_savings = round(sum(filtered_ops.values()), 4)
    planned_reclaim = reclaim_bytes_for_plan_operations(stats, operations)
    query_savings = (
        projection.query_request_savings_usd_month if OperationType.OPTIMIZE in resolved else 0.0
    )
    assumptions = dict(projection.assumptions)
    assumptions["planned_operations"] = [
        op.value for op in sorted(resolved, key=lambda item: item.value)
    ]
    return SavingsProjection(
        precision=projection.precision,
        current_monthly_cost_usd=projection.current_monthly_cost_usd,
        projected_monthly_cost_usd=round(
            max(0.0, projection.current_monthly_cost_usd - planned_savings), 4
        ),
        projected_savings_usd_month=planned_savings,
        reclaimable_bytes=planned_reclaim,
        by_operation=filtered_ops,
        query_request_savings_usd_month=query_savings,
        assumptions=assumptions,
    )


def savings_projection_to_dict(projection: SavingsProjection) -> dict[str, Any]:
    return {
        "precision": projection.precision.value,
        "current_monthly_cost_usd": projection.current_monthly_cost_usd,
        "projected_monthly_cost_usd": projection.projected_monthly_cost_usd,
        "projected_savings_usd_month": projection.projected_savings_usd_month,
        "reclaimable_bytes": projection.reclaimable_bytes,
        "query_request_savings_usd_month": projection.query_request_savings_usd_month,
        "by_operation": {
            operation.value: amount for operation, amount in projection.by_operation.items()
        },
        "assumptions": dict(projection.assumptions),
    }
