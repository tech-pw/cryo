from __future__ import annotations

from cryo.domain.enums import Mode, OperationType, PartitionTransform, PredicateCapability
from cryo.engines.trino.sql import UnsupportedPredicateError, render_predicate
from cryo.planner.partitions import PartitionFieldSpec, build_predicate
from cryo.planner.planner import build_plan


def test_build_predicate_supports_multi_field_conjunction() -> None:
    predicate = build_predicate(
        (
            PartitionFieldSpec(
                base_column="event_date",
                transform=PartitionTransform.DAY,
                col_type="date",
                partition_field="event_date_day",
            ),
            PartitionFieldSpec(
                base_column="region",
                transform=PartitionTransform.IDENTITY,
                col_type="string",
                partition_field="region",
            ),
        ),
        {"partition_values": {"event_date_day": "2025-05-18", "region": "us"}},
    )

    assert predicate.is_supported
    assert [term.base_column for term in predicate.terms] == ["event_date", "region"]
    assert render_predicate(predicate) == (
        "(event_date >= DATE '2025-05-18' AND event_date < DATE '2025-05-19' AND region = 'us')"
    )


def test_bucket_predicate_exposes_unsupported_capability() -> None:
    predicate = build_predicate(
        (
            PartitionFieldSpec(
                base_column="user_id",
                transform=PartitionTransform.BUCKET,
                col_type="int",
                partition_field="user_id_bucket",
                transform_param=16,
            ),
        ),
        {"partition_value": 7},
    )

    assert predicate.required_capabilities == {PredicateCapability.BUCKET_FILTER}
    assert predicate.unsupported_for({PredicateCapability.BASIC_COMPARISON}) == (
        "requires unsupported predicate capability: bucket_filter"
    )

    try:
        render_predicate(predicate)
    except UnsupportedPredicateError as exc:
        assert "bucket_filter" in str(exc)
    else:
        raise AssertionError("bucket predicates must not render without explicit capability")


def test_build_plan_skips_large_bucket_partition_filters() -> None:
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 500,
            "total_size_gb": 250,
            "partition_fields": [
                {
                    "base_column": "user_id",
                    "partition_field": "user_id_bucket",
                    "transform": "bucket[16]",
                    "col_type": "int",
                }
            ],
        },
        [{"partition_value": 7, "small_files_in_partition_count": 500, "partition_size_gb": 80}],
        Mode.STANDARD,
        {"min_small_files": 10, "chunk_size_gb": 50},
    )

    assert [operation.type for operation in plan.operations] == [OperationType.EXPIRE_SNAPSHOTS]
    assert any("bucket_filter" in note for note in plan.notes)


def test_build_plan_chunks_large_partitioned_tables_and_preserves_deep_order() -> None:
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 1_000,
            "total_size_gb": 250,
            "partition_fields": [
                {
                    "base_column": "event_date",
                    "partition_field": "event_date_day",
                    "transform": "day",
                    "col_type": "date",
                }
            ],
        },
        [
            {
                "partition_value": "2025-05-20",
                "small_files_in_partition_count": 500,
                "partition_size_gb": 60,
            },
            {
                "partition_value": "2025-05-19",
                "small_files_in_partition_count": 300,
                "partition_size_gb": 50,
            },
            {
                "partition_value": "2025-05-18",
                "small_files_in_partition_count": 200,
                "partition_size_gb": 10,
            },
        ],
        Mode.DEEP,
        {
            "min_small_files": 10,
            "chunk_size_gb": 70,
            "max_partitions_per_chunk": 2,
            "retention_days": 14,
            "retain_last": 3,
        },
    )

    assert plan.table_ref == "iceberg.demo.events"
    assert [operation.type for operation in plan.operations] == [
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
        OperationType.OPTIMIZE,
        OperationType.OPTIMIZE,
        OperationType.OPTIMIZE_MANIFESTS,
    ]
    assert [
        len(operation.predicates or ())
        for operation in plan.operations
        if operation.type is OperationType.OPTIMIZE
    ] == [1, 2]


def test_orphan_params_emit_min_age_hours() -> None:
    from cryo.planner.planner import _orphan_params

    params = _orphan_params({"orphan_retention_days": 3})
    assert params["retention_days"] == 3
    assert params["orphan_min_age_hours"] == 72


def test_planner_skips_hot_partitions_with_recent_writes() -> None:
    """Move 14: 'no writes in N hours' filter keeps actively-written partitions alone."""
    from datetime import UTC, datetime, timedelta

    cold_write = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    hot_write = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 500,
            "total_size_gb": 250,
            "partition_fields": [
                {
                    "base_column": "event_date",
                    "partition_field": "event_date_day",
                    "transform": "day",
                    "col_type": "date",
                }
            ],
        },
        [
            {
                "partition_value": "2025-05-20",
                "small_files_in_partition_count": 250,
                "partition_size_gb": 60,
                "last_write_at": cold_write,
            },
            {
                "partition_value": "2025-05-19",
                "small_files_in_partition_count": 250,
                "partition_size_gb": 60,
                "last_write_at": hot_write,
            },
        ],
        Mode.STANDARD,
        {
            "min_small_files": 128,
            "chunk_size_gb": 100,
            "max_partitions_per_chunk": 100,
            "partition_write_idle_hours": 6,
        },
    )
    optimize_ops = [op for op in plan.operations if op.type.value == "optimize"]
    assert optimize_ops, "cold partition should still produce an optimize op"
    # Only the cold partition's predicate should appear in any optimize op.
    flat_predicates = [
        term.base_column
        for op in optimize_ops
        for predicate in (op.predicates or ())
        for term in predicate.terms
    ]
    assert "event_date" in flat_predicates
    # The hot partition should be excluded (predicates for 2025-05-19 should not be present).
    cold_present = any(
        any(
            term.base_column == "event_date" and term.value == "2025-05-20"
            for term in (predicate.terms if predicate else [])
        )
        for op in optimize_ops
        for predicate in (op.predicates or ())
    )
    hot_present = any(
        any(
            term.base_column == "event_date" and term.value == "2025-05-19"
            for term in (predicate.terms if predicate else [])
        )
        for op in optimize_ops
        for predicate in (op.predicates or ())
    )
    assert cold_present, "expected cold partition 2025-05-20 in optimize predicates"
    assert not hot_present, "hot partition 2025-05-19 must be skipped (CommitFailedException risk)"
    assert any("Skipped hot partition" in note for note in plan.notes)


def test_planner_includes_partitions_without_last_write_at() -> None:
    """Move 14: partitions with unknown last_write_at are treated as cold (best-effort)."""
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 500,
            "total_size_gb": 250,
            "partition_fields": [
                {
                    "base_column": "event_date",
                    "partition_field": "event_date_day",
                    "transform": "day",
                    "col_type": "date",
                }
            ],
        },
        [
            {
                "partition_value": "2025-05-20",
                "small_files_in_partition_count": 250,
                "partition_size_gb": 60,
            },
        ],
        Mode.STANDARD,
        {
            "min_small_files": 128,
            "chunk_size_gb": 100,
            "max_partitions_per_chunk": 100,
            "partition_write_idle_hours": 6,
        },
    )
    optimize_ops = [op for op in plan.operations if op.type.value == "optimize"]
    assert optimize_ops, "partition with unknown last_write_at should be eligible"
    assert not any("Skipped hot partition" in note for note in plan.notes)


def test_planner_hot_cold_filter_off_by_default() -> None:
    """Move 14: filter only kicks in when partition_write_idle_hours > 0."""
    from datetime import UTC, datetime, timedelta

    hot_write = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 500,
            "total_size_gb": 250,
            "partition_fields": [
                {
                    "base_column": "event_date",
                    "partition_field": "event_date_day",
                    "transform": "day",
                    "col_type": "date",
                }
            ],
        },
        [
            {
                "partition_value": "2025-05-19",
                "small_files_in_partition_count": 500,
                "partition_size_gb": 60,
                "last_write_at": hot_write,
            },
        ],
        Mode.STANDARD,
        {"min_small_files": 128, "chunk_size_gb": 100, "max_partitions_per_chunk": 100},
    )
    assert not any("Skipped hot partition" in note for note in plan.notes)
    optimize_ops = [op for op in plan.operations if op.type.value == "optimize"]
    assert optimize_ops
