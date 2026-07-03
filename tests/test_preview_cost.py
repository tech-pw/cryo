from __future__ import annotations

from cryo.api.preview_cost import (
    savings_for_plan_operations,
    savings_projection_for_plan,
    savings_projection_from_stats,
)
from cryo.config import Settings
from cryo.domain.enums import CostPrecision, OperationType


def test_preview_projection_filters_to_planned_operations() -> None:
    settings = Settings(_env_file=None)
    stats = {
        "estimated_monthly_s3_cost_usd": 10.0,
        "small_files_count": 100,
        "target_file_size_bytes": 268_435_456,
        "raw": {
            "live_bytes": 100 * 1024**3,
            "dead_bytes": 20 * 1024**3,
            "metadata_bytes": 5 * 1024**3,
            "delete_bytes": 2 * 1024**3,
            "reclaim_expire_bytes": 20 * 1024**3,
            "reclaim_optimize_bytes": 2 * 1024**3,
            "reclaim_orphan_bytes": 0,
            "reclaim_manifests_bytes": 0,
            "precision": CostPrecision.METADATA.value,
        },
        "cost_precision": CostPrecision.METADATA.value,
    }
    projection = savings_projection_for_plan(
        stats,
        settings,
        params={"target_file_size": 268_435_456},
        operations=[OperationType.EXPIRE_SNAPSHOTS],
    )
    full = savings_projection_from_stats(stats, settings)
    assert projection.projected_savings_usd_month < full.projected_savings_usd_month
    assert OperationType.EXPIRE_SNAPSHOTS in projection.by_operation
    assert OperationType.OPTIMIZE not in projection.by_operation
    assert projection.assumptions.get("planned_operations") == ["expire_snapshots"]
    assert savings_for_plan_operations(full, [OperationType.EXPIRE_SNAPSHOTS]) == round(
        projection.projected_savings_usd_month, 4
    )
