from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cryo.config import Settings
from cryo.cost.model import CostStatsInput, ReclaimBytes, project_savings
from cryo.domain.enums import CostPrecision, OperationType
from cryo.domain.models import SavingsProjection, StorageBreakdown


def savings_projection_from_stats(stats: dict[str, Any], settings: Settings) -> SavingsProjection:
    raw = dict(stats.get("raw") or {})
    breakdown = StorageBreakdown(
        live_bytes=int(raw.get("live_bytes") or stats.get("live_bytes") or 0),
        dead_bytes=int(raw.get("dead_bytes") or stats.get("dead_bytes") or 0),
        metadata_bytes=int(raw.get("metadata_bytes") or stats.get("metadata_bytes") or 0),
        delete_bytes=int(raw.get("delete_bytes") or stats.get("delete_bytes") or 0),
        orphan_bytes=int(raw.get("orphan_bytes") or stats.get("orphan_bytes") or 0),
    )
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
    cost_stats = CostStatsInput(
        small_files_count=int(stats.get("small_files_count") or 0),
        small_file_bytes=int(raw.get("small_file_bytes") or stats.get("small_file_bytes") or 0),
        target_file_size_bytes=int(stats.get("target_file_size_bytes") or 268_435_456),
        query_count_30d=int(stats.get("query_count_30d") or 0),
    )
    precision_raw = stats.get("cost_precision") or raw.get("precision")
    precision = CostPrecision(precision_raw) if precision_raw else CostPrecision.METADATA
    return project_savings(breakdown, reclaim, cost_stats, settings, precision=precision)


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
    }


def savings_for_plan_operations(
    projection: SavingsProjection,
    operations: Sequence[OperationType | str],
) -> float:
    resolved = {OperationType(op) if not isinstance(op, OperationType) else op for op in operations}
    return round(sum(projection.by_operation.get(op, 0.0) for op in resolved), 4)
