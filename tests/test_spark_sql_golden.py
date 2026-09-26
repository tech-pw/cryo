from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from cryo.domain.enums import Mode, OperationType, PartitionTransform
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    PartitionPredicate,
    PartitionPredicateTerm,
)
from cryo.engines.spark.sql import render, render_predicate, render_wair_recluster


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
            "older_than => TIMESTAMP '2025-05-01 00:00:00', retain_last => 3, "
            "stream_results => true)"
        ),
        (
            "CALL iceberg.system.remove_orphan_files(table => 'iceberg.demo.events', "
            "older_than => TIMESTAMP '2025-05-01 00:00:00')"
        ),
        (
            "ALTER TABLE iceberg.demo.events SET TBLPROPERTIES ("
            "'write.target-file-size-bytes' = '268435456', "
            "'write.distribution-mode' = 'hash');\n"
            "CALL iceberg.system.rewrite_data_files(table => 'iceberg.demo.events', "
            "strategy => 'binpack', "
            "where => '(event_date >= DATE ''2025-05-18'' AND event_date < DATE ''2025-05-19'' "
            "AND region = ''us'')', options => map('target-file-size-bytes', '268435456', "
            "'min-input-files', '10', 'partial-progress.enabled', 'true', "
            "'partial-progress.max-commits', '50', "
            "'max-concurrent-file-group-rewrites', '5', 'rewrite-job-order', 'files-desc'))"
        ),
        "CALL iceberg.system.rewrite_manifests(table => 'iceberg.demo.events')",
    ]


def test_spark_renders_optimize_persists_target_file_size_and_distribution_mode() -> None:
    """target-file-size-bytes previously only governed the one rewrite it was passed to --
    nothing told the table itself, so the next writer that isn't Cryo has no idea what size
    this table was tuned to and can reintroduce the exact fragmentation optimize just fixed.
    Persisting it (and a sensible write.distribution-mode default) as table properties closes
    that gap."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, {"file_size_mb": 128}),),
    )
    sql = render(plan)[0].sql
    statements = sql.split(";\n")
    assert len(statements) == 2
    assert statements[0] == (
        "ALTER TABLE iceberg.demo.events SET TBLPROPERTIES ("
        "'write.target-file-size-bytes' = '134217728', "
        "'write.distribution-mode' = 'hash')"
    )
    assert statements[1].startswith("CALL iceberg.system.rewrite_data_files(")
    assert "'target-file-size-bytes', '134217728'" in statements[1]


def test_spark_renders_linear_sort_as_write_ordered_by_before_rewrite() -> None:
    """Regression, 2026-08-11: rewrite_data_files(strategy => 'sort') does NOT read a linear
    sort order from its options map -- confirmed live on lsq_pw.silver_prospect_activity_base,
    which failed with "Cannot sort data without a valid sort order, table ... is unsorted and
    no sort order is provided" even with 'sort-order' set in options. Iceberg's rewrite
    procedure only honors a linear sort against the table's own persisted sort-order metadata,
    so ALTER TABLE ... WRITE ORDERED BY must run first, in the same dispatched statement."""
    plan = MaintenancePlan(
        table_ref="cdp.lsq_pw.silver_prospect_activity_base",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {
                    "strategy": "sort",
                    "sort_columns": ["ProspectActivityAutoId", "activityevent"],
                    "rewrite_all": True,
                },
            ),
        ),
    )
    sql = render(plan)[0].sql
    statements = sql.split(";\n")
    assert len(statements) == 3
    # Both ALTER statements are metadata-only and independently committed before the rewrite
    # runs, so their relative order doesn't matter -- what matters is that WRITE ORDERED BY
    # lands before the rewrite_data_files call, which the last assertion below confirms.
    assert statements[0].startswith(
        "ALTER TABLE cdp.lsq_pw.silver_prospect_activity_base SET TBLPROPERTIES ("
    )
    assert statements[1] == (
        "ALTER TABLE cdp.lsq_pw.silver_prospect_activity_base WRITE ORDERED BY "
        "`ProspectActivityAutoId` ASC, `activityevent` ASC"
    )
    assert statements[2].startswith("CALL cdp.system.rewrite_data_files(")
    assert "strategy => 'sort'" in statements[2]
    # The bogus inline 'sort-order' options-map entry is gone -- Iceberg never read it for the
    # linear-sort strategy, only for zorder (see the zorder test below).
    assert "'sort-order'" not in statements[2]


def test_spark_renders_zorder_inline_without_write_ordered_by() -> None:
    """Z-order is a genuinely different Iceberg rewrite argument -- it's passed inline via the
    options map and does NOT require table-level sort-order metadata first, unlike linear sort
    (see test_spark_renders_linear_sort_as_write_ordered_by_before_rewrite above)."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {
                    "strategy": "zorder",
                    "zorder_columns": ["tenant_id", "event_date"],
                    "persist_target_file_size": False,
                },
            ),
        ),
    )
    sql = render(plan)[0].sql
    assert not sql.startswith("ALTER TABLE")
    assert sql.startswith("CALL iceberg.system.rewrite_data_files(")
    assert "'sort-order', 'zorder(`tenant_id`, `event_date`)'" in sql


def test_spark_can_render_scoped_rewrite_without_persisting_table_properties() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {
                    "file_size_mb": 128,
                    "persist_target_file_size": False,
                },
                (
                    _predicate(
                        PartitionPredicateTerm(
                            "event_date",
                            PartitionTransform.DAY,
                            "2026-08-04",
                            "date",
                        )
                    ),
                ),
            ),
        ),
    )

    sql = render(plan)[0].sql

    assert not sql.startswith("ALTER TABLE")
    assert sql.startswith("CALL iceberg.system.rewrite_data_files(")
    assert "where => '(event_date >= DATE ''2026-08-04''" in sql


def test_spark_renders_expire_snapshots_default_retention_as_literal_timestamp() -> None:
    """Regression, 2026-07-24: with no explicit older_than/cutoff_timestamp, the retention
    fallback used to render `current_timestamp() - INTERVAL 7 DAYS` -- a SQL expression, not
    a literal. Spark 4.1.2's stricter CALL-argument binder rejects that form outright with
    IllegalArgumentException: number of args and params must match after binding (confirmed
    live against lake.analytics.load_test_basefact). A literal TIMESTAMP computed at render time
    sidesteps it."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.LIGHT,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7, "retain_last": 1}),
        ),
    )
    sql = render(plan)[0].sql
    assert "current_timestamp()" not in sql
    match = re.search(r"older_than => TIMESTAMP '(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})'", sql)
    assert match is not None, sql
    rendered_cutoff = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    expected_cutoff = datetime.now(UTC) - timedelta(days=7)
    assert abs((rendered_cutoff - expected_cutoff).total_seconds()) < 30


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


def test_spark_compute_statistics_without_ndv_renders_only_bloom_filter_properties() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.COMPUTE_STATISTICS,
                {"columns": [], "bloom_filter_columns": ["user_id"], "compute_ndv": False},
            ),
        ),
    )
    assert [s.sql for s in render(plan)] == [
        "ALTER TABLE iceberg.demo.events SET TBLPROPERTIES "
        "('write.parquet.bloom-filter-enabled.column.user_id' = 'true')"
    ]


def test_spark_compute_statistics_with_nothing_to_compute_renders_no_statement() -> None:
    """compute_table_stats scans every column of the table. With NDV off and no bloom-filter
    columns there is nothing to do, and a plan stored before the planner learned that must
    not turn into a full scan either."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"older_than": "2025-05-01 00:00:00"}),
            Operation(
                OperationType.COMPUTE_STATISTICS,
                {"columns": [], "bloom_filter_columns": [], "compute_ndv": False},
            ),
        ),
    )
    statements = render(plan)
    assert [s.action for s in statements] == [OperationType.EXPIRE_SNAPSHOTS]


def test_spark_optimize_pins_min_file_size_to_the_planner_threshold() -> None:
    """Iceberg's default candidate threshold is 75% of the target (192 MB at 256 MB); the
    planner counts small files under the scanner's 89.6 MB. On 2026-09-24 that gap rewrote
    206 files / 23 GB of a table the planner had picked for its 11 small files."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {"file_size_mb": 256, "min_file_size_mb": 89.6, "persist_target_file_size": False},
            ),
        ),
    )
    sql = render(plan)[0].sql
    assert f"'min-file-size-bytes', '{int(89.6 * 1024 * 1024)}'" in sql
    assert sql.index("'min-input-files'") < sql.index("'min-file-size-bytes'")


def test_spark_optimize_min_file_size_applies_to_binpack_only_and_below_target() -> None:
    def optimize_sql(params: dict[str, object]) -> str:
        plan = MaintenancePlan(
            table_ref="iceberg.demo.events",
            mode=Mode.STANDARD,
            operations=(Operation(OperationType.OPTIMIZE, params),),
        )
        return render(plan)[0].sql

    base = {"file_size_mb": 256, "persist_target_file_size": False}
    assert "min-file-size-bytes" not in optimize_sql(base)
    # A sort rewrite is about layout, not small files: every file stays a candidate.
    assert "min-file-size-bytes" not in optimize_sql(
        {**base, "strategy": "sort", "min_file_size_mb": 64}
    )
    # Iceberg rejects min-file-size-bytes >= target-file-size-bytes; drop it instead.
    assert "min-file-size-bytes" not in optimize_sql({**base, "min_file_size_mb": 256})
    assert "'min-file-size-bytes', '1048576'" in optimize_sql(
        {**base, "min_file_size_bytes": 1048576}
    )


def test_spark_optimize_can_leave_large_files_unsplit() -> None:
    """Iceberg rewrites any file over 180% of the target on its own just to split it;
    OpenHouse raises that bound to 10x target because a reader splits large files anyway."""

    def optimize_sql(params: dict[str, object]) -> str:
        plan = MaintenancePlan(
            table_ref="iceberg.demo.events",
            mode=Mode.STANDARD,
            operations=(Operation(OperationType.OPTIMIZE, params),),
        )
        return render(plan)[0].sql

    base = {"file_size_mb": 256, "persist_target_file_size": False}
    assert f"'max-file-size-bytes', '{10 * 256 * 1024 * 1024}'" in optimize_sql(
        {**base, "max_file_size_ratio": 10}
    )
    assert "max-file-size-bytes" not in optimize_sql(base)
    # At or under Iceberg's own 1.8x it would only split more; not a valid override here.
    assert "max-file-size-bytes" not in optimize_sql({**base, "max_file_size_ratio": 1.5})
    assert "max-file-size-bytes" not in optimize_sql(
        {**base, "strategy": "sort", "max_file_size_ratio": 10}
    )


def test_spark_rewrite_job_order_defaults_to_files_desc_and_rejects_unknown_values() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {"rewrite_job_order": "largest-first", "persist_target_file_size": False},
            ),
        ),
    )
    assert "'rewrite-job-order', 'files-desc'" in render(plan)[0].sql


def test_spark_expire_snapshots_streaming_can_be_turned_off() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.LIGHT,
        operations=(
            Operation(
                OperationType.EXPIRE_SNAPSHOTS,
                {"older_than": "2025-05-01 00:00:00", "retain_last": 2, "stream_results": False},
            ),
        ),
    )
    assert render(plan)[0].sql == (
        "CALL iceberg.system.expire_snapshots(table => 'iceberg.demo.events', "
        "older_than => TIMESTAMP '2025-05-01 00:00:00', retain_last => 2)"
    )


def test_spark_expire_snapshots_streams_results_after_setting_the_protected_ref() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.LIGHT,
        operations=(
            Operation(
                OperationType.EXPIRE_SNAPSHOTS,
                {
                    "older_than": "2025-05-01 00:00:00",
                    "retain_last": 2,
                    "protected_snapshot_ids": [11, 7],
                },
            ),
        ),
    )
    assert render(plan)[0].sql == (
        "ALTER TABLE iceberg.demo.events CREATE OR REPLACE TAG `cryo-protected-latest` "
        "AS OF VERSION 11;\n"
        "CALL iceberg.system.expire_snapshots(table => 'iceberg.demo.events', "
        "older_than => TIMESTAMP '2025-05-01 00:00:00', retain_last => 2, "
        "stream_results => true)"
    )


def test_spark_renders_convert_deletes_operation() -> None:
    """convert_deletes carries commit/parallelism tuning whenever partial progress is on, and
    asks Iceberg to drop the delete files its rewrite left dangling when the caller opts in:
    on 2026-09-24 all 59 convert_deletes statements reported removed_delete_files_count=0."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.CONVERT_DELETES,
                {
                    "delete_file_threshold": 2,
                    "partial_progress_enabled": True,
                    "remove_dangling_deletes": True,
                },
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
        "'max-concurrent-file-group-rewrites', '5', "
        "'remove-dangling-deletes', 'true', 'rewrite-job-order', 'files-desc'))"
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


def test_spark_renders_convert_deletes_scoped_to_partition_predicates() -> None:
    """Regression, 2026-07-22: CONVERT_DELETES ignored operation.predicates entirely, so
    the planner's skewed-partition scoping (cryo/planner/planner.py's
    _skewed_partition_predicates) had nowhere to land -- a global call self-selects file
    groups across the whole table and can silently starve heavily-skewed partitions run
    after run. Mirrors the where-clause mechanism OPTIMIZE already had."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.CONVERT_DELETES,
                {"delete_file_threshold": 1, "partial_progress_enabled": True},
                predicates=(
                    _predicate(
                        PartitionPredicateTerm(
                            "_date", PartitionTransform.IDENTITY, "20485", "date"
                        )
                    ),
                    _predicate(
                        PartitionPredicateTerm(
                            "_date", PartitionTransform.IDENTITY, "20486", "date"
                        )
                    ),
                ),
            ),
        ),
    )
    statements = render(plan)
    assert statements[0].sql == (
        "CALL iceberg.system.rewrite_data_files(table => 'iceberg.demo.events', "
        "where => '(_date = DATE ''2026-02-01'') OR (_date = DATE ''2026-02-02'')', "
        "options => map('delete-file-threshold', '1', 'partial-progress.enabled', 'true', "
        "'partial-progress.max-commits', '50', 'max-concurrent-file-group-rewrites', '5', "
        "'rewrite-job-order', 'files-desc'))"
    )


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
        "'delete-file-threshold', '1', 'partial-progress.enabled', 'false', "
        "'rewrite-job-order', 'files-desc'))"
    )


def test_spark_convert_deletes_leaves_dangling_deletes_unless_asked() -> None:
    """remove-dangling-deletes exists only from Iceberg 1.7 and unknown options fail the
    procedure, so the shared renderer never adds it on its own (EMR/local runtimes vary)."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.CONVERT_DELETES, {"delete_file_threshold": 1}),),
    )
    assert "remove-dangling-deletes" not in render(plan)[0].sql


def test_spark_renders_rewrite_position_deletes_operation() -> None:
    """Okolnychyi (VLDB'24) minor delete compaction -- distinct from CONVERT_DELETES' full
    rewrite_data_files call above. The planner always asked for partial progress here, but
    the renderer dropped every param until 2026-09-25; Iceberg's default is an all-or-nothing
    rewrite, so one failed group threw away hours of work on 80k-delete-file tables."""
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.REWRITE_POSITION_DELETES,
                {"delete_file_threshold": 1, "partial_progress_enabled": True},
            ),
        ),
    )
    statements = render(plan)
    assert len(statements) == 1
    assert statements[0].sql == (
        "CALL iceberg.system.rewrite_position_delete_files(table => 'iceberg.demo.events', "
        "options => map('partial-progress.enabled', 'true', "
        "'partial-progress.max-commits', '10', 'max-concurrent-file-group-rewrites', '5', "
        "'rewrite-job-order', 'files-desc'))"
    )


def test_spark_rewrite_position_deletes_respects_explicit_tuning() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.REWRITE_POSITION_DELETES,
                {
                    "partial_progress_enabled": False,
                    "rewrite_position_deletes_max_concurrent_rewrites": 8,
                    "rewrite_job_order": "bytes-desc",
                },
            ),
        ),
    )
    assert render(plan)[0].sql == (
        "CALL iceberg.system.rewrite_position_delete_files(table => 'iceberg.demo.events', "
        "options => map('max-concurrent-file-group-rewrites', '8', "
        "'rewrite-job-order', 'bytes-desc'))"
    )


def test_spark_wair_recluster_sorts_only_files_straddling_query_endpoints() -> None:
    assert render_wair_recluster(
        "ice",
        "analytics.events",
        "event_date",
        ["2026-06-01", 20619],
        column_type="date",
    ) == (
        "CALL ice.system.rewrite_data_files(table => 'ice.analytics.events', "
        "strategy => 'sort', sort_order => 'event_date ASC NULLS LAST', "
        "where => 'event_date = DATE ''2026-06-01'' OR event_date = DATE ''2026-06-15''', "
        "options => map('rewrite-all', 'true', 'target-file-size-bytes', '268435456', "
        "'partial-progress.enabled', 'true', 'max-concurrent-file-group-rewrites', '5'))"
    )


def test_spark_wair_recluster_quotes_odd_identifiers_and_keeps_numbers_bare() -> None:
    sql = render_wair_recluster("ice", "analytics.events", "user-id", [10, 20], column_type="long")
    assert "sort_order => '`user-id` ASC NULLS LAST'" in sql
    assert "where => '`user-id` = 10 OR `user-id` = 20'" in sql
