from __future__ import annotations

from cryo.domain.enums import Mode, OperationType, PartitionTransform, PredicateCapability
from cryo.engines.trino.sql import UnsupportedPredicateError, render_predicate
from cryo.planner.partitions import PartitionFieldSpec, build_predicate, partition_fields_from_stats
from cryo.planner.planner import build_plan


def test_unpartitioned_table_produces_no_partition_fields() -> None:
    """Real production panic (2026-07-14): the scanner used to store partition_col as the
    literal string "_unpartitioned" for genuinely unpartitioned tables (a display-label
    sentinel, not a real column). Treating that as a bindable column name flowed all the
    way to the Go engine's row-filter binder, which panicked trying to resolve
    '_unpartitioned' against the table schema. None and the sentinel must both yield
    zero partition fields (full-table optimize, no row filter)."""
    assert partition_fields_from_stats({"partition_col": None}) == ()
    assert partition_fields_from_stats({"partition_col": "_unpartitioned"}) == ()
    assert partition_fields_from_stats({"partition_col": ""}) == ()


def test_unpartitioned_sentinel_in_partition_fields_is_filtered() -> None:
    fields = partition_fields_from_stats(
        {
            "partition_fields": [
                {
                    "partition_field": "_unpartitioned",
                    "base_column": "_unpartitioned",
                }
            ]
        }
    )
    assert fields == ()


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


def test_build_plan_orders_optimize_chunks_by_moop_score_over_small_files() -> None:
    """AutoComp §A2: when autocomp_traits.s_c is present, it must win over both small_files
    and the plain benefit_score -- a partition with fewer small files but a much higher MOOP
    score (e.g. it's cheap to rewrite and heavily queried) should chunk first."""
    stats = {
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
    }
    partitions = [
        {
            "partition_value": "2025-05-20",
            "small_files_in_partition_count": 900,  # most files, but...
            "partition_size_gb": 10,
            "compaction_benefit_score": 500.0,  # ...and the higher plain benefit_score too
            "autocomp_traits": {"s_c": 0.1},  # ...MOOP ranks it last
        },
        {
            "partition_value": "2025-05-19",
            "small_files_in_partition_count": 50,
            "partition_size_gb": 10,
            "compaction_benefit_score": 10.0,
            "autocomp_traits": {"s_c": 0.9},  # MOOP ranks it first
        },
    ]
    params = {
        "min_small_files": 10,
        "chunk_size_gb": 10,  # force each partition into its own chunk
        "max_partitions_per_chunk": 1,
        "retention_days": 14,
        "retain_last": 3,
    }

    plan = build_plan(stats, partitions, Mode.STANDARD, params)

    optimize_ops = [op for op in plan.operations if op.type is OperationType.OPTIMIZE]
    assert len(optimize_ops) == 2
    first_chunk_terms = optimize_ops[0].predicates[0].terms
    assert first_chunk_terms[0].value == "2025-05-19"  # MOOP-preferred partition chunked first


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


def test_planner_adds_convert_deletes_when_optimize_not_planned() -> None:
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 10,
            "total_delete_files": 50,
            "delete_file_pct": 25.0,
            "total_size_gb": 5,
        },
        [],
        Mode.STANDARD,
        {"min_small_files": 128, "convert_deletes_threshold_pct": 5.0},
    )
    assert [op.type for op in plan.operations] == [
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.CONVERT_DELETES,
    ]


def test_planner_prefers_minor_delete_compaction_below_major_threshold() -> None:
    """Okolnychyi (VLDB'24) §C2: many position-delete files but delete_file_pct still under
    the major-rewrite threshold -> cheap rewrite_position_deletes, not a full CONVERT_DELETES
    rewrite."""
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 10,
            "total_delete_files": 80,
            "position_delete_files": 80,
            "delete_file_pct": 3.0,
            "total_size_gb": 5,
        },
        [],
        Mode.STANDARD,
        {
            "min_small_files": 128,
            "delete_minor_file_threshold": 50,
            "delete_major_pct_threshold": 10.0,
        },
    )
    assert [op.type for op in plan.operations] == [
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REWRITE_POSITION_DELETES,
    ]
    assert any("rewrite_position_deletes (minor)" in note for note in plan.notes)


def test_planner_escalates_to_major_rewrite_above_threshold_even_with_few_position_deletes() -> (
    None
):
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 10,
            "total_delete_files": 5,
            "position_delete_files": 5,  # below the minor file-count threshold...
            "delete_file_pct": 15.0,  # ...but delete volume alone crosses the major threshold
            "total_size_gb": 5,
        },
        [],
        Mode.STANDARD,
        {
            "min_small_files": 128,
            "delete_minor_file_threshold": 50,
            "delete_major_pct_threshold": 10.0,
            "convert_deletes_threshold_pct": 5.0,
        },
    )
    assert [op.type for op in plan.operations] == [
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.CONVERT_DELETES,
    ]


def test_convert_deletes_scopes_to_skewed_partitions() -> None:
    """Regression, 2026-07-22: rewrite_data_files with no `where` clause self-selects file
    groups across the whole table using its own binpack heuristics. On a table with a small
    number of wildly over-fragmented partitions among many normal ones, that silently starved
    the skewed partitions run after run -- confirmed live: a 200-partition table averaging
    ~19 small files/partition but with 10+ partitions holding 700-791 files each had
    convert_deletes touch the same 46 files and remove zero deletes across three attempts,
    including one with 4x the Spark executors. The skewed partitions were never reached."""
    partitions = [
        {"partition_value": str(20485 + i), "small_files_in_partition_count": 750}
        for i in range(3)
    ] + [{"partition_value": str(20500 + i), "small_files_in_partition_count": 19} for i in range(50)]
    plan = build_plan(
        {
            "schema_name": "cdp",
            "table_name": "load_test_basefact",
            "small_files_count": 12710,
            "total_delete_files": 12710,
            "delete_file_pct": 83.82,
            "total_size_gb": 5,
            "partition_fields": [
                {
                    "base_column": "_date",
                    "partition_field": "_date",
                    "transform": "identity",
                    "col_type": "date",
                }
            ],
        },
        partitions,
        Mode.STANDARD,
        {
            "min_small_files": 10,
            "convert_deletes_threshold_pct": 5.0,
            "force_convert_deletes": True,
        },
    )
    convert_op = next(op for op in plan.operations if op.type is OperationType.CONVERT_DELETES)
    assert convert_op.predicates is not None
    assert len(convert_op.predicates) == 3
    scoped_values = {term.value for predicate in convert_op.predicates for term in predicate.terms}
    assert scoped_values == {"20485", "20486", "20487"}
    assert any("Scoped convert_deletes to 3 skewed partition" in note for note in plan.notes)


def test_convert_deletes_stays_table_wide_without_skew() -> None:
    """No predicates when partitions are roughly even -- must not regress the common case."""
    partitions = [
        {"partition_value": str(20500 + i), "small_files_in_partition_count": 20} for i in range(20)
    ]
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 400,
            "total_delete_files": 100,
            "delete_file_pct": 25.0,
            "total_size_gb": 5,
            "partition_fields": [
                {
                    "base_column": "_date",
                    "partition_field": "_date",
                    "transform": "identity",
                    "col_type": "date",
                }
            ],
        },
        partitions,
        Mode.STANDARD,
        {
            "min_small_files": 128,
            "convert_deletes_threshold_pct": 5.0,
            "force_convert_deletes": True,
        },
    )
    convert_op = next(op for op in plan.operations if op.type is OperationType.CONVERT_DELETES)
    assert convert_op.predicates is None


def test_planner_skips_v2_delete_policy_for_v3_deletion_vector_tables() -> None:
    """A format-version-3 table's position-delete file count shouldn't drive the V2-shaped
    minor/major policy -- it's not implemented for DV-aware maintenance yet, and must not be
    misapplied."""
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 10,
            "total_delete_files": 0,
            "position_delete_files": 80,  # would trigger "minor" if format_version were ignored
            "delete_file_pct": 1.0,
            "format_version": 3,
            "total_size_gb": 5,
        },
        [],
        Mode.STANDARD,
        {"min_small_files": 128, "delete_minor_file_threshold": 50},
    )
    assert OperationType.REWRITE_POSITION_DELETES not in [op.type for op in plan.operations]
    assert OperationType.CONVERT_DELETES not in [op.type for op in plan.operations]
