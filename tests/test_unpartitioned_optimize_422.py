from __future__ import annotations

import pytest

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.spark.sql import render
from cryo.engines.spark.worker_guard import SparkPlanScopeError, validate_spark_plan_scope
from cryo.planner.planner import build_plan
from cryo.planner.serialization import plan_from_dict, plan_to_dict
from cryo.worker.worker import requested_operation_skips

_STATS = {
    "schema_name": "demo",
    "table_name": "events",
    "small_files_count": 1,
    "total_size_gb": 10,
}


def test_explicit_optimize_below_threshold_is_recorded() -> None:
    plan = build_plan(
        _STATS,
        [],
        Mode.STANDARD,
        {"min_small_files": 5, "requested_operations": ["optimize"]},
    )

    assert plan.skipped_operations == (
        (
            "optimize",
            "Skipped optimize: table has 1 small files below threshold 5.",
        ),
    )
    assert OperationType.OPTIMIZE not in [operation.type for operation in plan.operations]


def test_nightly_plan_omits_unrequested_skips_from_the_hash() -> None:
    plan = build_plan(_STATS, [], Mode.STANDARD, {"min_small_files": 5})

    assert plan.skipped_operations == ()
    assert "skipped_operations" not in plan_to_dict(plan)


def test_skipped_operations_round_trip() -> None:
    plan = build_plan(
        _STATS,
        [],
        Mode.STANDARD,
        {"min_small_files": 5, "requested_operations": ["optimize", "expire_snapshots"]},
    )
    restored = plan_from_dict(plan_to_dict(plan))

    assert restored.skipped_operations == plan.skipped_operations
    assert requested_operation_skips(plan_to_dict(plan)) == tuple(
        reason for _, reason in plan.skipped_operations
    )


def test_spark_renders_unpartitioned_group_cap() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {
                    "bounded_unpartitioned": True,
                    "max_file_group_size_bytes": 50 * 1024**3,
                    "partial_progress": True,
                },
            ),
        ),
    )

    sql = render(plan)[0].sql
    assert "'max-file-group-size-bytes', '53687091200'" in sql
    assert "'partial-progress.enabled', 'true'" in sql


def test_spark_guard_allows_a_bounded_group_and_rejects_a_larger_one() -> None:
    chunk = 50
    group = int(chunk * 1024**3)
    allowed = MaintenancePlan(
        table_ref="demo.analytics.events",
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {
                    "chunk_size_gb": chunk,
                    "bounded_unpartitioned": True,
                    "max_file_group_size_bytes": group,
                },
            ),
        ),
    )
    validate_spark_plan_scope(allowed, stats={"total_size_gb": 400})

    forged = MaintenancePlan(
        table_ref="demo.analytics.events",
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {
                    "chunk_size_gb": chunk,
                    "bounded_unpartitioned": True,
                    "max_file_group_size_bytes": group + 1,
                },
            ),
        ),
    )
    with pytest.raises(SparkPlanScopeError, match="outside chunk size budget"):
        validate_spark_plan_scope(forged, stats={"total_size_gb": 400})
