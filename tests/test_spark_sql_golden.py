from __future__ import annotations

from datetime import date

from cryo.domain.enums import Mode, OperationType, PartitionTransform
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    PartitionPredicate,
    PartitionPredicateTerm,
)
from cryo.engines.spark.sql import render, render_predicate


def _predicate(*terms: PartitionPredicateTerm) -> PartitionPredicate:
    return PartitionPredicate(terms=terms)


def test_spark_renders_identity_date_epoch_day() -> None:
    """Identity date partitions store days-since-epoch; must not call fromisoformat."""
    epoch_day = (date(2026, 7, 9) - date(1970, 1, 1)).days
    assert epoch_day == 20643
    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm("event_date", PartitionTransform.IDENTITY, epoch_day, "date")
            )
        )
        == "(event_date = DATE '2026-07-09')"
    )
    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm(
                    "event_date", PartitionTransform.IDENTITY, str(epoch_day), "date"
                )
            )
        )
        == "(event_date = DATE '2026-07-09')"
    )
    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm(
                    "event_ts", PartitionTransform.IDENTITY, epoch_day, "timestamp"
                )
            )
        )
        == "(event_ts = TIMESTAMP '2026-07-09 00:00:00')"
    )
    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm(
                    "event_ts", PartitionTransform.IDENTITY, str(epoch_day), "timestamp"
                )
            )
        )
        == "(event_ts = TIMESTAMP '2026-07-09 00:00:00')"
    )


def test_spark_renders_date_timestamp_month_and_year_predicates() -> None:
    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm("event_date", PartitionTransform.DAY, "2025-05-18", "date")
            )
        )
        == "(event_date >= DATE '2025-05-18' AND event_date < DATE '2025-05-19')"
    )

    assert render_predicate(
        _predicate(
            PartitionPredicateTerm("event_ts", PartitionTransform.DAY, "2025-05-18", "timestamp")
        )
    ) == (
        "(event_ts >= TIMESTAMP '2025-05-18 00:00:00' "
        "AND event_ts < TIMESTAMP '2025-05-19 00:00:00')"
    )

    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm("event_date", PartitionTransform.MONTH, "2025-05", "date")
            )
        )
        == "(event_date >= DATE '2025-05-01' AND event_date < DATE '2025-06-01')"
    )

    assert (
        render_predicate(
            _predicate(
                PartitionPredicateTerm("event_date", PartitionTransform.YEAR, "2025", "date")
            )
        )
        == "(event_date >= DATE '2025-01-01' AND event_date < DATE '2026-01-01')"
    )


def test_spark_render_golden_operation_sql() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.EXPIRE_SNAPSHOTS,
                {"older_than": "2025-05-01 00:00:00", "retain_last": 3},
            ),
            Operation(
                OperationType.REMOVE_ORPHAN_FILES,
                {"older_than": "2025-05-01 00:00:00"},
            ),
            Operation(
                OperationType.OPTIMIZE,
                {"file_size_mb": 256, "min_input_files": 10},
                (
                    _predicate(
                        PartitionPredicateTerm(
                            "event_date", PartitionTransform.DAY, "2025-05-18", "date"
                        ),
                        PartitionPredicateTerm(
                            "region", PartitionTransform.IDENTITY, "us", "string"
                        ),
                    ),
                ),
            ),
            Operation(OperationType.OPTIMIZE_MANIFESTS, {}),
        ),
    )

    assert [statement.sql for statement in render(plan)] == [
        (
            "CALL iceberg.system.expire_snapshots(table => 'iceberg.demo.events', "
            "older_than => TIMESTAMP '2025-05-01 00:00:00', retain_last => 3)"
        ),
        (
            "CALL iceberg.system.remove_orphan_files(table => 'iceberg.demo.events', "
            "older_than => TIMESTAMP '2025-05-01 00:00:00')"
        ),
        (
            "CALL iceberg.system.rewrite_data_files(table => 'iceberg.demo.events', "
            "strategy => 'binpack', "
            "where => '(event_date >= DATE ''2025-05-18'' AND event_date < DATE ''2025-05-19'' "
            "AND region = ''us'')', options => map('target-file-size-bytes', '268435456', "
            "'min-input-files', '10', 'partial-progress.enabled', 'true', "
            "'partial-progress.max-commits', '50', "
            "'max-concurrent-file-group-rewrites', '5'))"
        ),
        "CALL iceberg.system.rewrite_manifests(table => 'iceberg.demo.events')",
    ]


def test_spark_renders_compute_statistics_all_columns() -> None:
    """compute_table_stats without column list — all columns."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.DEEP,
        operations=(Operation(OperationType.COMPUTE_STATISTICS, {"columns": []}),),
    )
    stmts = render(plan)
    assert len(stmts) == 1
    assert stmts[0].sql == "CALL iceberg.system.compute_table_stats(table => 'iceberg.demo.events')"


def test_spark_renders_compute_statistics_with_bloom_filters() -> None:
    """compute_table_stats with Bloom filter columns — expects two statements."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.COMPUTE_STATISTICS,
                {"columns": ["user_id"], "bloom_filter_columns": ["user_id"]},
            ),
        ),
    )
    stmts = render(plan)
    assert len(stmts) == 1
    # Multiple SQL statements joined by ";\n"
    sql = stmts[0].sql
    parts = sql.split(";\n")
    assert len(parts) == 2
    assert "compute_table_stats" in parts[0]
    assert "bloom-filter-enabled.column.user_id" in parts[1]


def test_spark_renders_convert_deletes_operation() -> None:
    """Regression, 2026-07-22: without explicit max-commits/max-concurrent-file-group-rewrites
    options, Iceberg's own defaults (10 commits, 1 concurrent rewrite) mean a single run on a
    large delete-heavy table barely dents it -- confirmed live: 46/13,661 files touched, 0
    deletes removed in one pass. Both must be present whenever partial-progress is enabled."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.CONVERT_DELETES,
                {"delete_file_threshold": 2, "partial_progress_enabled": True},
            ),
        ),
    )
    statements = render(plan)
    assert len(statements) == 1
    assert statements[0].sql == (
        "CALL iceberg.system.rewrite_data_files("
        "table => 'iceberg.demo.events', options => map("
        "'delete-file-threshold', '2', 'partial-progress.enabled', 'true', "
        "'partial-progress.max-commits', '50', "
        "'max-concurrent-file-group-rewrites', '5'))"
    )


def test_spark_renders_convert_deletes_respects_explicit_tuning() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.CONVERT_DELETES,
                {
                    "delete_file_threshold": 2,
                    "partial_progress_enabled": True,
                    "convert_deletes_max_commits": 200,
                    "convert_deletes_max_concurrent_rewrites": 20,
                },
            ),
        ),
    )
    statements = render(plan)
    assert "'partial-progress.max-commits', '200'" in statements[0].sql
    assert "'max-concurrent-file-group-rewrites', '20'" in statements[0].sql


def test_spark_renders_convert_deletes_without_partial_progress_omits_commit_tuning() -> None:
    """max-commits/max-concurrent-file-group-rewrites only mean anything when
    partial-progress is enabled -- a full atomic rewrite doesn't use them."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.CONVERT_DELETES,
                {"delete_file_threshold": 1, "partial_progress_enabled": False},
            ),
        ),
    )
    statements = render(plan)
    assert statements[0].sql == (
        "CALL iceberg.system.rewrite_data_files("
        "table => 'iceberg.demo.events', options => map("
        "'delete-file-threshold', '1', 'partial-progress.enabled', 'false'))"
    )


def test_spark_renders_rewrite_position_deletes_operation() -> None:
    """Okolnychyi (VLDB'24) minor delete compaction -- distinct from CONVERT_DELETES' full
    rewrite_data_files call above."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.REWRITE_POSITION_DELETES, {}),),
    )
    statements = render(plan)
    assert len(statements) == 1
    assert statements[0].sql == (
        "CALL iceberg.system.rewrite_position_delete_files(table => 'iceberg.demo.events')"
    )
