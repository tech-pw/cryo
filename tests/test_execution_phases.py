from __future__ import annotations

from datetime import UTC, datetime

from cryo.domain.enums import Mode
from cryo.planner.guardrails import (
    CORRECT_ORDER,
    sort_operations_canonically,
    validate_operation_order,
)
from cryo.planner.planner import build_plan
from cryo.scanner.signals import detect_streaming_from_history, metadata_to_data_ratio
from cryo.scheduler.cron import cron_is_due
from cryo.scheduler.routing import resolve_destination
from cryo.scheduler.rules import PresetConfig, TableCandidate


def test_cron_is_due_on_hour_boundary() -> None:
    now = datetime(2026, 7, 3, 14, 0, tzinfo=UTC)
    assert cron_is_due("0 14 * * *", now=now, last_run_at=now.replace(hour=13))


def test_routing_large_table_to_spark() -> None:
    preset = PresetConfig(
        id="large",
        name="large",
        destination_id="trino-default",
        guardrails={
            "routing_rules": [
                {
                    "predicate": {"field": "total_size_gb", "op": ">", "value": 100},
                    "destination": "spark-large",
                }
            ]
        },
    )
    table = TableCandidate("t1", "raw", "events", {"total_size_gb": 250})
    destinations = {
        "trino-default": {"id": "trino-default", "name": "trino-default"},
        "spark-large": {"id": "spark-large", "name": "spark-large"},
    }
    route = resolve_destination(table, preset, destinations)
    assert route.destination_id == "spark-large"


def test_guardrails_warn_on_out_of_order_ops() -> None:
    # Cleanup now runs last, so optimize-before-expire is canonical...
    assert validate_operation_order(["optimize", "expire_snapshots"]) == []
    # ...and the reverse is what's out of order.
    assert validate_operation_order(["expire_snapshots", "optimize"])


def test_guardrails_accept_full_canonical_order() -> None:
    assert validate_operation_order([op.value for op in CORRECT_ORDER]) == []


def test_sort_operations_canonically_reorders_and_is_stable() -> None:
    assert sort_operations_canonically(
        ["remove_orphan_files", "optimize", "expire_snapshots", "convert_deletes"]
    ) == ["convert_deletes", "optimize", "expire_snapshots", "remove_orphan_files"]

    # Already canonical input is left alone.
    canonical = [op.value for op in CORRECT_ORDER]
    assert sort_operations_canonically(canonical) == canonical

    # Equal-rank entries keep input order -- build_plan relies on this so the per-chunk
    # OPTIMIZE operations retain their benefit-score ranking.
    tagged = [("remove_orphan_files", "orphan")] + [("optimize", i) for i in range(5)]
    assert [item[1] for item in sort_operations_canonically(tagged, key=lambda t: t[0])] == [
        0,
        1,
        2,
        3,
        4,
        "orphan",
    ]


def test_sort_operations_canonically_sinks_unknown_ops() -> None:
    assert sort_operations_canonically(["mystery_op", "optimize", "expire_snapshots"]) == [
        "optimize",
        "expire_snapshots",
        "mystery_op",
    ]


def test_build_plan_emits_canonical_order() -> None:
    plan = build_plan(
        {
            "schema_name": "s",
            "table_name": "t",
            "small_files_count": 5000,
            "total_size_gb": 500,
        },
        [],
        Mode.DEEP,
        {"min_small_files": 10, "compute_statistics": True},
    )
    ranks = [CORRECT_ORDER.index(op.type) for op in plan.operations]
    assert ranks == sorted(ranks)
    assert [op.type.value for op in plan.operations][-2:] == [
        "expire_snapshots",
        "remove_orphan_files",
    ]


def test_manifest_only_mode() -> None:
    plan = build_plan({"schema_name": "s", "table_name": "t"}, [], Mode.MANIFEST_ONLY, {})
    assert len(plan.operations) == 1
    assert plan.operations[0].type.value == "optimize_manifests"


def test_delete_ratio_trigger_plans_optimize() -> None:
    plan = build_plan(
        {
            "schema_name": "s",
            "table_name": "t",
            "small_files_count": 10,
            "delete_file_pct": 25,
            "total_size_gb": 10,
        },
        [],
        Mode.STANDARD,
        {"min_small_files": 128, "delete_ratio_trigger": 20},
    )
    assert any(op.type.value == "optimize" for op in plan.operations)


def test_streaming_detection_from_history() -> None:
    history = [
        {"scan_at": "2026-07-02T00:00:00+00:00", "total_snapshots": 10},
        {"scan_at": "2026-07-03T00:00:00+00:00", "total_snapshots": 40},
    ]
    is_streaming, commits_per_day = detect_streaming_from_history(history)
    assert is_streaming
    assert commits_per_day > 20


def test_streaming_detection_skips_deltas_explained_by_maintenance_jobs() -> None:
    """Regression, 2026-07-27: a deep run's own expire_snapshots/optimize/optimize_manifests
    commits inflated analytics.silver_launch_events's snapshot count 8 -> 36 in minutes, which the
    unadjusted heuristic read as 80 commits/day of streaming writes. stats_history_for_signals
    marks the latest row when a Cryo job explains the delta; detect_streaming_from_history must
    then report no streaming rather than a false positive."""
    history = [
        {"scan_at": "2026-07-27T02:00:00+00:00", "total_snapshots": 8},
        {
            "scan_at": "2026-07-27T10:00:00+00:00",
            "total_snapshots": 36,
            "maintenance_job_since_previous": True,
        },
    ]
    is_streaming, commits_per_day = detect_streaming_from_history(history)
    assert not is_streaming
    assert commits_per_day == 0.0


def test_metadata_ratio() -> None:
    assert metadata_to_data_ratio(50, 1000) == 0.05


def test_streaming_aware_cadence_tightens_chunk_and_threshold() -> None:
    """Move 12: high-velocity tables compact with smaller chunks + lower small-files bar."""
    base_stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 200,
        "total_size_gb": 250,
        "partition_fields": [
            {
                "base_column": "event_date",
                "partition_field": "event_date_day",
                "transform": "day",
                "col_type": "date",
            }
        ],
        "is_streaming": True,
        "commits_per_day": 96.0,
    }
    partitions = [
        {
            "partition_value": "2025-05-20",
            "small_files_in_partition_count": 200,
            "partition_size_gb": 60,
        },
        {
            "partition_value": "2025-05-19",
            "small_files_in_partition_count": 200,
            "partition_size_gb": 60,
        },
    ]
    plan = build_plan(
        base_stats,
        partitions,
        Mode.STANDARD,
        {"min_small_files": 128, "chunk_size_gb": 100, "max_partitions_per_chunk": 1},
    )
    optimize_ops = [op for op in plan.operations if op.type.value == "optimize"]
    assert optimize_ops, "expected optimize ops with streaming cadence"
    assert len(optimize_ops) == 2
    assert any("Streaming-aware cadence" in note for note in plan.notes)
    assert any("96.0 commits/day" in note for note in plan.notes)


def test_streaming_aware_cadence_opt_out() -> None:
    """Caller can disable streaming-aware cadence via streaming_aware_cadence=false."""
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 200,
        "total_size_gb": 40,
        "partition_fields": [
            {
                "base_column": "event_date",
                "partition_field": "event_date_day",
                "transform": "day",
                "col_type": "date",
            }
        ],
        "is_streaming": True,
        "commits_per_day": 96.0,
    }
    partitions = [
        {
            "partition_value": "2025-05-20",
            "small_files_in_partition_count": 200,
            "partition_size_gb": 20,
        },
        {
            "partition_value": "2025-05-19",
            "small_files_in_partition_count": 200,
            "partition_size_gb": 20,
        },
    ]
    plan = build_plan(
        stats,
        partitions,
        Mode.STANDARD,
        {
            "min_small_files": 128,
            "chunk_size_gb": 100,
            "max_partitions_per_chunk": 100,
            "streaming_aware_cadence": False,
        },
    )
    assert not any("Streaming-aware cadence" in note for note in plan.notes)
    optimize_ops = [op for op in plan.operations if op.type.value == "optimize"]
    assert len(optimize_ops) == 1


def test_extra_care_cadence_tightens_chunks_just_under_streaming_threshold() -> None:
    """: crm.gold_prospect_activity_extension_base writes ~14 commits/day
    (is_streaming False, cutoff 24) and loses rewrite_data_files to
    branch "main" has changed. Extra-care band (5-24) uses smaller chunks so each
    commit races a shorter rewrite window.
    """
    stats = {
        "schema_name": "crm",
        "table_name": "gold_prospect_activity_extension_base",
        "small_files_count": 200,
        "total_size_gb": 80,
        "partition_fields": [
            {
                "base_column": "event_date",
                "partition_field": "event_date_day",
                "transform": "day",
                "col_type": "date",
            }
        ],
        "is_streaming": False,
        "commits_per_day": 14.0,
    }
    partitions = [
        {
            "partition_value": "2026-08-17",
            "small_files_in_partition_count": 200,
            "partition_size_gb": 40,
        },
        {
            "partition_value": "2026-08-16",
            "small_files_in_partition_count": 200,
            "partition_size_gb": 40,
        },
    ]
    baseline = build_plan(
        {**stats, "commits_per_day": 1.0},
        partitions,
        Mode.STANDARD,
        {"min_small_files": 128, "chunk_size_gb": 100, "max_partitions_per_chunk": 10},
    )
    extra_care = build_plan(
        stats,
        partitions,
        Mode.STANDARD,
        {"min_small_files": 128, "chunk_size_gb": 100, "max_partitions_per_chunk": 10},
    )
    baseline_opt = [op for op in baseline.operations if op.type.value == "optimize"]
    extra_opt = [op for op in extra_care.operations if op.type.value == "optimize"]
    assert len(baseline_opt) == 1
    assert len(extra_opt) == 2
    assert any("moderate-write extra-care" in note for note in extra_care.notes)
    assert any("14.0 commits/day" in note for note in extra_care.notes)
    expire_ops = [op for op in extra_care.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 3


def test_streaming_aware_retention_tightens_expire_snapshots_window() -> None:
    """Regression, 2026-08-03: warehouse.gold_events went from 666 to 2,137 snapshots
    *during* a single 2.5-day maintenance run -- expire_snapshots can only remove snapshots
    older than its retention window, not ones created while the job itself is still running.
    A high-velocity table should get a tighter window than the 7-day default so repeated
    runs actually keep pace, mirroring the existing streaming-aware chunk-size cadence."""
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 0,
        "total_size_gb": 100,
        "is_streaming": True,
        "commits_per_day": 588.0,
    }
    plan = build_plan(stats, [], Mode.STANDARD, {})
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert len(expire_ops) == 1
    assert expire_ops[0].params["retention_days"] == 1
    assert any("Streaming-aware retention" in note for note in plan.notes)
    assert any("588.0 commits/day" in note for note in plan.notes)


def test_streaming_aware_retention_moderate_velocity_caps_at_three_days() -> None:
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 0,
        "total_size_gb": 100,
        "is_streaming": True,
        "commits_per_day": 25.0,
    }
    plan = build_plan(stats, [], Mode.STANDARD, {"retention_days": 7})
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 3


def test_streaming_aware_retention_never_tightens_below_an_explicit_shorter_value() -> None:
    """A caller-configured retention_days shorter than what streaming-awareness would pick
    must not get *widened* back up -- min() with the configured value, not a flat override."""
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 0,
        "total_size_gb": 100,
        "is_streaming": True,
        "commits_per_day": 25.0,
    }
    plan = build_plan(stats, [], Mode.STANDARD, {"retention_days": 2})
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 2


def test_streaming_aware_retention_opt_out() -> None:
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 0,
        "total_size_gb": 100,
        "is_streaming": True,
        "commits_per_day": 588.0,
    }
    plan = build_plan(
        stats, [], Mode.STANDARD, {"retention_days": 7, "streaming_aware_cadence": False}
    )
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 7
    assert not any("Streaming-aware retention" in note for note in plan.notes)


def test_skip_operations_drops_named_operations_and_notes_why() -> None:
    """Regression, discovered 2026-07-31 and fixed 2026-08-03: Preset.operations has always
    been editable and looked like it controlled what actually runs, but build_plan() never
    consulted it -- an operator removing expire_snapshots from a preset's operations list had
    zero effect on the built plan, silently. skip_operations is the mechanism the worker now
    derives from Preset.operations (via job.operations) to actually honor it."""
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 200,
        "total_size_gb": 10,
    }
    plan = build_plan(
        stats, [], Mode.DEEP, {"skip_operations": ["expire_snapshots", "remove_orphan_files"]}
    )
    op_types = [op.type.value for op in plan.operations]
    assert "expire_snapshots" not in op_types
    assert "remove_orphan_files" not in op_types
    assert "optimize" in op_types
    assert "optimize_manifests" in op_types
    assert any("expire_snapshots" in note and "remove_orphan_files" in note for note in plan.notes)


def test_skip_operations_applies_to_manifest_only_mode_too() -> None:
    plan = build_plan(
        {"schema_name": "demo", "table_name": "events"},
        [],
        Mode.MANIFEST_ONLY,
        {"skip_operations": ["optimize_manifests"]},
    )
    assert plan.operations == ()


def test_explicit_request_bounds_the_plan() -> None:
    """A request for optimize must not grow an expire_snapshots (issue #142, 2026-10-21).

    The preset's operations list does not bound the plan. The request does.
    """
    from cryo.domain.enums import EngineType, OperationType
    from cryo.planner.execution import build_execution_plan

    class _Destination:
        engine = EngineType.SPARK_EMR
        config = {"catalog": "bench"}

    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 200,
        "total_size_gb": 10,
        "snapshot_count": 40,
    }
    plan = build_execution_plan(
        stats=stats,
        partitions=[],
        mode=Mode.DEEP,
        params={"requested_operations": ["optimize"]},
        destination=_Destination(),
        operations=(OperationType.OPTIMIZE,),
    )
    op_types = [op.type.value for op in plan.operations]
    assert op_types == ["optimize"]
    assert any("not requested for this run" in note for note in plan.notes)


def test_skip_operations_empty_is_a_no_op() -> None:
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 200,
        "total_size_gb": 10,
    }
    plan = build_plan(stats, [], Mode.DEEP, {})
    op_types = [op.type.value for op in plan.operations]
    assert "expire_snapshots" in op_types
    assert not any("Skipped operation" in note for note in plan.notes)


def test_streaming_aware_retention_no_op_when_not_streaming() -> None:
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 0,
        "total_size_gb": 100,
        "is_streaming": False,
        "commits_per_day": 0.0,
    }
    plan = build_plan(stats, [], Mode.STANDARD, {"retention_days": 7})
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 7


def test_backlog_aware_retention_tightens_when_not_streaming() -> None:
    """Regression, 2026-08-10: admin.iceberg_maintenance_log sits at 22.15 commits/day --
    just under the 24.0/day is_streaming detection cutoff -- so streaming-aware retention
    never fires despite the table carrying 499 accumulated snapshots. commits_per_day only
    reacts to *current* velocity; this reacts to the backlog itself, which is why a table
    that's never flagged streaming still needs to be caught."""
    stats = {
        "schema_name": "admin",
        "table_name": "iceberg_maintenance_log",
        "small_files_count": 0,
        "total_size_gb": 0.0,
        "is_streaming": False,
        "commits_per_day": 22.15,
        "total_snapshots": 499,
    }
    plan = build_plan(stats, [], Mode.STANDARD, {"retention_days": 7})
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 1
    assert any("Backlog-aware retention" in note for note in plan.notes)
    assert any("499 snapshots" in note for note in plan.notes)


def test_backlog_aware_retention_moderate_backlog_caps_at_three_days() -> None:
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 0,
        "total_size_gb": 10,
        "is_streaming": False,
        "commits_per_day": 1.0,
        "total_snapshots": 150,
    }
    plan = build_plan(stats, [], Mode.STANDARD, {"retention_days": 7})
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 3


def test_backlog_aware_retention_no_op_below_threshold() -> None:
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 0,
        "total_size_gb": 10,
        "is_streaming": False,
        "commits_per_day": 1.0,
        "total_snapshots": 40,
    }
    plan = build_plan(stats, [], Mode.STANDARD, {"retention_days": 7})
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 7
    assert not any("Backlog-aware retention" in note for note in plan.notes)


def test_backlog_aware_retention_never_tightens_below_an_explicit_shorter_value() -> None:
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 0,
        "total_size_gb": 10,
        "is_streaming": False,
        "commits_per_day": 1.0,
        "total_snapshots": 500,
    }
    plan = build_plan(stats, [], Mode.STANDARD, {"retention_days": 1})
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 1


def test_backlog_aware_retention_opt_out() -> None:
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 0,
        "total_size_gb": 10,
        "is_streaming": False,
        "commits_per_day": 1.0,
        "total_snapshots": 500,
    }
    plan = build_plan(
        stats, [], Mode.STANDARD, {"retention_days": 7, "backlog_aware_cadence": False}
    )
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 7
    assert not any("Backlog-aware retention" in note for note in plan.notes)


def test_backlog_aware_retention_threshold_is_configurable() -> None:
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 0,
        "total_size_gb": 10,
        "is_streaming": False,
        "commits_per_day": 1.0,
        "total_snapshots": 40,
    }
    plan = build_plan(
        stats, [], Mode.STANDARD, {"retention_days": 7, "snapshot_backlog_threshold": 30}
    )
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 3


def test_streaming_and_backlog_signals_combine_via_min() -> None:
    """A table that's both currently streaming AND already sitting on a large backlog should
    get the more aggressive of the two tightened values, not have one signal silently
    override the other."""
    stats = {
        "schema_name": "demo",
        "table_name": "events",
        "small_files_count": 0,
        "total_size_gb": 10,
        "is_streaming": True,
        "commits_per_day": 25.0,  # streaming-aware alone would cap at 3
        "total_snapshots": 500,  # backlog-aware alone would tighten to 1
    }
    plan = build_plan(stats, [], Mode.STANDARD, {"retention_days": 7})
    expire_ops = [op for op in plan.operations if op.type.value == "expire_snapshots"]
    assert expire_ops[0].params["retention_days"] == 1
    assert any("Streaming-aware retention" in note for note in plan.notes)
    assert any("Backlog-aware retention" in note for note in plan.notes)
