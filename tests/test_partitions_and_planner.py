from __future__ import annotations

from cryo.domain.enums import Mode, OperationType, PartitionTransform, PredicateCapability
from cryo.engines.trino.sql import UnsupportedPredicateError, render_predicate
from cryo.planner.partitions import PartitionFieldSpec, build_predicate, partition_fields_from_stats
from cryo.planner.planner import _small_files, build_plan


def test_excluded_optimize_does_not_suppress_delete_maintenance_or_emit_statistics() -> None:
    plan = build_plan(
        {
            "catalog": "cdp",
            "schema_name": "s",
            "table_name": "t",
            "total_size_gb": 1,
            "small_files_count": 200,
            "total_delete_files": 20,
            "delete_file_pct": 10,
        },
        [],
        Mode.STANDARD,
        {"skip_operations": ["optimize"], "compute_statistics": True},
    )
    types = {op.type for op in plan.operations}
    assert OperationType.CONVERT_DELETES in types
    assert OperationType.OPTIMIZE not in types
    assert OperationType.COMPUTE_STATISTICS not in types
    assert not any("planned optimize already" in note for note in plan.notes)


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
        OperationType.OPTIMIZE,
        OperationType.OPTIMIZE,
        OperationType.OPTIMIZE_MANIFESTS,
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
    ]
    assert [
        len(operation.predicates or ())
        for operation in plan.operations
        if operation.type is OperationType.OPTIMIZE
    ] == [1, 2]
    assert all(
        operation.predicates
        for operation in plan.operations
        if operation.type is OperationType.OPTIMIZE
    )


def test_thin_spread_partitions_skipped_by_default_min_small_files_gate() -> None:
    """Baseline, 2026-09-10: confirms today's exact behavior is unchanged when
    min_partition_small_files is absent -- min_small_files still gates per-partition.
    1,000 partitions x 4 small files each = 4,000 total (clears the table-level gate),
    but each individual partition sits far under min_small_files=128, so none qualify."""
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "thin_spread",
            "small_files_count": 4_000,
            "total_size_gb": 25_000,
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
                "partition_value": f"2025-01-{day:02d}",
                "small_files_in_partition_count": 4,
                "partition_size_gb": 1,
            }
            for day in range(1, 32)
        ],
        Mode.DEEP,
        {"min_small_files": 128, "chunk_size_gb": 100},
    )

    optimize_ops = [op for op in plan.operations if op.type is OperationType.OPTIMIZE]
    assert optimize_ops == []
    assert any("no renderable partition candidates" in note for note in plan.notes)


def test_min_partition_small_files_override_packs_thin_spread_partitions() -> None:
    """Live-verified, 2026-09-10: gold_dbt_pw_bq has ~5,954 small files (clears the
    table-level gate) spread across ~63k partitions at a median of 4/partition -- with
    min_small_files=128 applied per-partition, only the rare partition that happens to
    concentrate 128+ small files ever qualifies, so the table's total file count never
    meaningfully drops despite optimize 'succeeding' repeatedly. Setting
    min_partition_small_files low (opt-in, per-preset) lets every thin partition become a
    candidate, and greedy_pack groups them into real chunks within chunk_size_gb."""
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "thin_spread",
            "small_files_count": 4_000,
            "total_size_gb": 25_000,
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
                "partition_value": f"2025-01-{day:02d}",
                "small_files_in_partition_count": 4,
                "partition_size_gb": 1,
            }
            for day in range(1, 32)
        ],
        Mode.DEEP,
        {
            "min_small_files": 128,
            "min_partition_small_files": 1,
            "chunk_size_gb": 100,
            "max_partitions_per_chunk": 10,
        },
    )

    optimize_ops = [op for op in plan.operations if op.type is OperationType.OPTIMIZE]
    assert optimize_ops, "expected at least one OPTIMIZE chunk once thin partitions are eligible"
    total_predicates = sum(len(op.predicates or ()) for op in optimize_ops)
    assert total_predicates == 31, "every thin partition should become a candidate"
    # max_partitions_per_chunk=10 over 31 candidates -> 4 chunks (10,10,10,1)
    assert len(optimize_ops) == 4


def test_small_file_metrics_never_fall_back_to_total_file_count() -> None:
    assert _small_files({"files_per_partition": 25}) == 0
    assert _small_files({"small_files_in_partition_count": 0, "files_per_partition": 25}) == 0
    assert _small_files({"small_files": 0, "files_per_partition": 25}) == 0
    assert (
        _small_files(
            {"small_files_in_partition_count": 0, "small_files": 7, "files_per_partition": 25}
        )
        == 0
    )
    assert _small_files({"small_files": 7, "files_per_partition": 25}) == 7


def test_build_plan_skips_partitions_without_small_file_metrics() -> None:
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 2,
            "total_size_gb": 200,
            "partition_fields": [
                {
                    "base_column": "region",
                    "partition_field": "region",
                    "transform": "identity",
                    "col_type": "string",
                }
            ],
        },
        [
            {
                "partition_value": "us",
                "files_per_partition": 25,
                "partition_size_gb": 10,
            },
            {
                "partition_value": "in",
                "small_files_in_partition_count": 0,
                "files_per_partition": 25,
                "partition_size_gb": 10,
            },
        ],
        Mode.STANDARD,
        {"min_small_files": 1, "chunk_size_gb": 50},
    )

    assert OperationType.OPTIMIZE not in [operation.type for operation in plan.operations]
    assert any("no renderable partition candidates" in note for note in plan.notes)


def test_build_plan_allows_in_budget_unpartitioned_full_table_optimize() -> None:
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 2,
            "total_size_gb": 50,
        },
        [],
        Mode.STANDARD,
        {"min_small_files": 1, "chunk_size_gb": 50},
    )

    optimize = next(
        operation for operation in plan.operations if operation.type is OperationType.OPTIMIZE
    )
    assert optimize.predicates is None


def test_build_plan_skips_large_unpartitioned_full_table_optimize() -> None:
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 2,
            "total_size_gb": 50.1,
        },
        [],
        Mode.DEEP,
        {"min_small_files": 1, "chunk_size_gb": 50},
    )

    assert OperationType.OPTIMIZE not in [operation.type for operation in plan.operations]
    assert OperationType.OPTIMIZE_MANIFESTS in [operation.type for operation in plan.operations]
    assert any("unpartitioned table size" in note for note in plan.notes)


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


def test_standard_mode_notes_the_deep_only_operations_it_skipped() -> None:
    """Regression, 2026-07-27: a standard-mode run on a real 1.5 TB production table only
    planned expire_snapshots + optimize, silently dropping remove_orphan_files and
    optimize_manifests with zero explanation in plan.notes -- indistinguishable from the
    planner having failed to build them. Both are deep-only by design; the plan must say so."""
    stats = {"schema_name": "demo", "table_name": "events", "small_files_count": 0}

    standard_plan = build_plan(stats, [], Mode.STANDARD, {})
    light_plan = build_plan(stats, [], Mode.LIGHT, {})
    deep_plan = build_plan(stats, [], Mode.DEEP, {})

    assert any("deep-only" in note for note in standard_plan.notes)
    assert any("deep-only" in note for note in light_plan.notes)
    assert not any("deep-only" in note for note in deep_plan.notes)
    assert not any(op.type is OperationType.REMOVE_ORPHAN_FILES for op in standard_plan.operations)
    assert any(op.type is OperationType.REMOVE_ORPHAN_FILES for op in deep_plan.operations)


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
        OperationType.CONVERT_DELETES,
        OperationType.EXPIRE_SNAPSHOTS,
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
        OperationType.REWRITE_POSITION_DELETES,
        OperationType.EXPIRE_SNAPSHOTS,
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
        OperationType.CONVERT_DELETES,
        OperationType.EXPIRE_SNAPSHOTS,
    ]


def test_planner_escalates_repeated_minor_delete_compactions_to_major() -> None:
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 10,
            "total_delete_files": 80,
            "position_delete_files": 80,
            "delete_file_pct": 3.0,
            "delete_minor_runs_since_major": 5,
            "total_size_gb": 5,
        },
        [],
        Mode.STANDARD,
        {
            "min_small_files": 128,
            "delete_minor_file_threshold": 50,
            "delete_major_pct_threshold": 10.0,
            "delete_max_minor_runs_before_major": 5,
        },
    )

    assert [operation.type for operation in plan.operations] == [
        OperationType.CONVERT_DELETES,
        OperationType.EXPIRE_SNAPSHOTS,
    ]
    assert any("after 5 successful minor" in note for note in plan.notes)


def test_planner_routes_equality_deletes_to_major_rewrite() -> None:
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 10,
            "total_delete_files": 60,
            "position_delete_files": 0,
            "equality_delete_files": 60,
            "delete_file_pct": 2.0,
            "total_size_gb": 5,
            "format_version": 2,
        },
        [],
        Mode.STANDARD,
        {
            "min_small_files": 128,
            "delete_equality_file_threshold": 50,
        },
    )

    assert [operation.type for operation in plan.operations] == [
        OperationType.CONVERT_DELETES,
        OperationType.EXPIRE_SNAPSHOTS,
    ]


def test_planner_uses_opt_in_hot_table_minor_cycle() -> None:
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 10,
            "total_delete_files": 80,
            "position_delete_files": 80,
            "delete_file_pct": 3.0,
            "delete_minor_runs_since_major": 2,
            "query_count_30d": 10_000,
            "total_size_gb": 5,
        },
        [],
        Mode.STANDARD,
        {
            "min_small_files": 128,
            "delete_max_minor_runs_before_major": 5,
            "delete_hot_query_count_30d": 5_000,
            "delete_hot_max_minor_runs_before_major": 2,
        },
    )

    assert [operation.type for operation in plan.operations] == [
        OperationType.CONVERT_DELETES,
        OperationType.EXPIRE_SNAPSHOTS,
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
        {"partition_value": str(20485 + i), "small_files_in_partition_count": 750} for i in range(3)
    ] + [
        {"partition_value": str(20500 + i), "small_files_in_partition_count": 19} for i in range(50)
    ]
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


def test_convert_deletes_scopes_to_delete_skew_when_data_files_already_compacted() -> None:
    """Regression, 2026-07-28: a table's 34,176 position-delete files sat in 4 partitions that
    an earlier optimize pass had already reduced to 5-7 well-sized data files each -- looking
    "skewed" by small_files_in_partition_count alone, those partitions are indistinguishable
    from any other well-compacted partition, so _skewed_partition_predicates silently starved
    exactly the partitions holding the deletes across every convert_deletes attempt. Scoping
    must also consider delete_files_in_partition_count, independent of small-file skew.

    Superseded in part by test_planner_routes_pathological_delete_skew_to_minor_rewrite below
    (2026-07-29): this exact partition shape (well-sized data files, extreme delete-per-file
    ratio) turned out to make convert_deletes a no-op even once correctly scoped -- confirmed
    live, removed_delete_files_count=0 across every attempt -- so the planner now routes it to
    rewrite_position_delete_files instead. This test keeps a milder ratio purely to keep
    convert_deletes' own scoping-by-delete-skew logic covered independent of the minor-path
    escape hatch(es) -- it must land clearly *below* both _has_pathological_delete_skew's
    (ratio>=20.0) and _has_unreachable_delete_skew's (ratio>=7.0, added 2026-08-11 for the
    sub-pathological-but-still-unreachable case) trigger points, or this test would start
    exercising the wrong code path. Ratio here is 1500/500=3.0, comfortably under both.
    """
    partitions = [
        {
            "partition_value": str(20485 + i),
            "small_files_in_partition_count": 0,
            "files_per_partition": 500,
            "delete_files_in_partition_count": 1500,
        }
        for i in range(4)
    ] + [
        {
            "partition_value": str(20500 + i),
            "small_files_in_partition_count": 20,
            "delete_files_in_partition_count": 0,
        }
        for i in range(50)
    ]
    plan = build_plan(
        {
            "schema_name": "cdp",
            "table_name": "load_test_basefact",
            "small_files_count": 1000,
            "total_delete_files": 6000,
            "delete_file_pct": 96.0,
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
    scoped_values = {term.value for predicate in convert_op.predicates for term in predicate.terms}
    # The 4 delete-heavy partitions must be scoped in, even though they have zero small files.
    assert scoped_values == {"20485", "20486", "20487", "20488"}
    assert any("delete-file count" in note for note in plan.notes)


def test_planner_routes_sub_pathological_but_unreachable_delete_skew_to_minor_rewrite() -> None:
    """Regression, confirmed live 2026-08-11: mview.gold_dbt_test_results (483 queries/30d --
    a genuinely hot table) has delete_file_pct=100% (271 data files, 1,797 position-delete
    files, current live snapshot) and has had a "succeeded" job whose own plan notes read
    "Skipped convert_deletes: planned optimize already rewrites data files for this table" --
    yet the deletes are untouched, because optimize/convert_deletes selects rewrite candidates
    by file *size*, and this table's data files are already reasonably sized (not fragmented).

    This table's worst partition (40 data files, 315 position-deletes -> ratio ~7.9) sits
    *below* _has_pathological_delete_skew's default min_ratio=20.0, so the existing pathological
    escape hatch (test_planner_routes_pathological_delete_skew_to_minor_rewrite, ratio ~1450-1700)
    correctly does not fire -- but convert_deletes/optimize still provably won't reach these
    deletes, just less extremely than the ratio>=20 case. The gap: delete_file_pct alone
    (>= delete_major_pct_threshold) routes to convert_deletes/optimize with no check that doing
    so will actually touch the delete-heavy partitions when the ratio, while below "pathological",
    is still too low for rewrite_data_files' size-driven selection to reach them."""
    partitions = [
        {
            "partition_value": str(20670 + i),
            "small_files_in_partition_count": 0,
            "files_per_partition": [40, 39, 37, 34, 28, 44, 20, 29][i],
            "delete_files_in_partition_count": [315, 299, 295, 267, 214, 178, 155, 74][i],
        }
        for i in range(8)
    ]
    plan = build_plan(
        {
            "schema_name": "mview",
            "table_name": "gold_dbt_test_results",
            "small_files_count": 29,
            "total_delete_files": 1797,
            "position_delete_files": 1797,
            "delete_file_pct": 100.0,
            "total_size_gb": 66.8,
            "query_count_30d": 483,
            "partition_fields": [
                {
                    "base_column": "test_attempted_at",
                    "partition_field": "test_attempted_at_month",
                    "transform": "month",
                    "col_type": "timestamptz",
                }
            ],
        },
        partitions,
        Mode.STANDARD,
        {
            "min_small_files": 128,  # table isn't fragmented enough to trigger optimize's own gate
            "convert_deletes_threshold_pct": 5.0,
        },
    )
    assert OperationType.REWRITE_POSITION_DELETES in [op.type for op in plan.operations]
    assert OperationType.CONVERT_DELETES not in [op.type for op in plan.operations]


def test_delete_files_reads_the_real_scanner_key_not_just_the_test_fixture_key() -> None:
    """Regression, confirmed live 2026-08-18 while verifying the fix above against real
    production data: `_delete_files()` read `delete_files_in_partition_count`, a key that
    has never existed in production -- both scanner producers (`cryo/scanner/analyze.py`'s
    `raw={"partition_values": ..., "delete_files": accum.delete_files}` and
    `cryo/scanner/go_scan.py`'s identical shape) write the count under the plain key
    `delete_files`. `_delete_files()` therefore always read 0/None in prod, making both
    `_has_pathological_delete_skew` and `_has_unreachable_delete_skew` silently dead code
    despite every test above passing -- because every fixture here uses the same wrong key
    the function was reading, not the real one the scanners actually emit. Confirmed against
    a live pull of `mview.gold_dbt_test_results`'s real partition rows (the exact table
    the test above is modeled on): before this fix, `_has_unreachable_delete_skew` returned
    False against that table's real data (every partition's `delete_files` present and
    non-zero, up to 798 in a single partition) purely because of the key mismatch.

    This test reproduces the bug's exact shape: same partition data as the test above, but
    using the real `delete_files` key instead of the fixture-only `delete_files_in_partition_count`
    key, to prove the fix reads production data correctly and not just its own test fixtures.
    `_delete_files()` still falls back to the old key too, so the test above needs no change.
    """
    partitions = [
        {
            "partition_value": str(20670 + i),
            "small_files_in_partition_count": 0,
            "files_per_partition": [40, 39, 37, 34, 28, 44, 20, 29][i],
            "delete_files": [315, 299, 295, 267, 214, 178, 155, 74][i],
        }
        for i in range(8)
    ]
    plan = build_plan(
        {
            "schema_name": "mview",
            "table_name": "gold_dbt_test_results",
            "small_files_count": 29,
            "total_delete_files": 1797,
            "position_delete_files": 1797,
            "delete_file_pct": 100.0,
            "total_size_gb": 66.8,
            "query_count_30d": 483,
            "partition_fields": [
                {
                    "base_column": "test_attempted_at",
                    "partition_field": "test_attempted_at_month",
                    "transform": "month",
                    "col_type": "timestamptz",
                }
            ],
        },
        partitions,
        Mode.STANDARD,
        {
            "min_small_files": 128,
            "convert_deletes_threshold_pct": 5.0,
        },
    )
    assert OperationType.REWRITE_POSITION_DELETES in [op.type for op in plan.operations]
    assert OperationType.CONVERT_DELETES not in [op.type for op in plan.operations]


def test_planner_routes_pathological_delete_skew_to_minor_rewrite() -> None:
    """Live incident, 2026-07-29: partition 20400 had 7 data files (already well-sized, 0 small
    files) but 11,845 position-delete files. convert_deletes -> rewrite_data_files (BIN-PACK)
    selects candidates by file *size*; despite delete-file-threshold=1 and correct partition
    scoping, it formed zero rewrite groups for this partition three separate times
    (removed_delete_files_count=0). rewrite_position_delete_files, run directly against the same
    table, cleared all 34,176 dangling deletes in one pass. The planner must route a table with
    this partition shape to the minor path regardless of overall delete_file_pct (100% here,
    far above the major-rewrite threshold that would normally force convert_deletes)."""
    partitions = [
        {
            "partition_value": str(20399 + i),
            "small_files_in_partition_count": 0,
            "files_per_partition": 7,
            "delete_files_in_partition_count": 10000 + i,
        }
        for i in range(4)
    ]
    plan = build_plan(
        {
            "schema_name": "cdp",
            "table_name": "gold_dbt_video_stats_reports_unified",
            "small_files_count": 2,
            "total_delete_files": 34176,
            "position_delete_files": 34176,
            "delete_file_pct": 100.0,
            "total_size_gb": 1500,
            "partition_fields": [
                {
                    "base_column": "session_date",
                    "partition_field": "session_date",
                    "transform": "identity",
                    "col_type": "date",
                }
            ],
        },
        partitions,
        Mode.STANDARD,
        {
            "min_small_files": 20,
            "convert_deletes_threshold_pct": 5.0,
            "force_convert_deletes": True,
        },
    )
    assert OperationType.REWRITE_POSITION_DELETES in [op.type for op in plan.operations]
    assert OperationType.CONVERT_DELETES not in [op.type for op in plan.operations]
    assert any("extreme delete-per-file ratio" in note for note in plan.notes)


def test_planner_does_not_misfire_pathological_skew_when_data_still_fragmented() -> None:
    """A partition with many deletes but ALSO many small files still needs its data rewritten --
    convert_deletes can (and does) reach it via ordinary size-based selection, so the
    pathological-skew escape hatch must not steal it away from the major path."""
    partitions = [
        {
            "partition_value": "20485",
            "small_files_in_partition_count": 500,
            "files_per_partition": 500,
            "delete_files_in_partition_count": 10000,
        }
    ]
    plan = build_plan(
        {
            "schema_name": "demo",
            "table_name": "events",
            "small_files_count": 500,
            "total_delete_files": 10000,
            "position_delete_files": 10000,
            "delete_file_pct": 95.0,
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
    assert OperationType.CONVERT_DELETES in [op.type for op in plan.operations]
    assert OperationType.REWRITE_POSITION_DELETES not in [op.type for op in plan.operations]


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
