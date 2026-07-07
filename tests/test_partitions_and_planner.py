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
