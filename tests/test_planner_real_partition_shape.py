"""Planner inputs in the shape production actually sends (SQLJobStore.get_table_data).

The stats dict carries ``partition_col`` at the top level and the real spec only under
``raw.partition_fields``; partition rows carry ``partition_values``. Every other planner test
passes ``partition_fields`` at the top level, which production never does -- so none of them
caught that the planner ignored the real spec and guessed identity from the column name.
Live 2026-09-24 that guess was wrong for ~2,500 tables (1,739 month, 686 day, 70 year, plus
bucketed and multi-field tables), and one spec-evolved table crashed planning on "<nil>".
"""

from __future__ import annotations

from cryo.domain.enums import Mode, OperationType, PartitionTransform
from cryo.engines.spark.sql import render as render_spark
from cryo.engines.trino.sql import render as render_trino
from cryo.planner.partitions import build_predicate, partition_fields_from_stats
from cryo.planner.planner import build_plan

MONTH_FIELD = {
    "col_type": "date",
    "transform": "month",
    "base_column": "joining_date",
    "partition_field": "joining_date_month",
    "transform_param": None,
}


def _stats(**overrides):
    stats = {
        "schema_name": "cdp",
        "table_name": "users",
        "small_files_count": 900,
        "total_size_gb": 300,
        "partition_col": "joining_date",
        "partition_col_type": "date",
        "raw": {"partition_fields": [MONTH_FIELD]},
    }
    stats.update(overrides)
    return stats


def _row(month: int | None, *, small: int = 300, gb: float = 20.0, display: str | None = None):
    return {
        "partition_value": display if display is not None else str(month),
        "partition_values": {"joining_date_month": month},
        "small_files_in_partition_count": small,
        "partition_size_gb": gb,
    }


def test_the_real_spec_under_raw_wins_over_the_column_name_guess():
    (field,) = partition_fields_from_stats(_stats())
    assert field.transform is PartitionTransform.MONTH
    assert field.partition_field == "joining_date_month"
    assert field.base_column == "joining_date"


def test_a_partition_from_an_older_spec_is_skipped_not_bound_to_its_display_string():
    fields = partition_fields_from_stats(
        _stats(raw={"partition_fields": [{**MONTH_FIELD, "partition_field": "_date_month"}]})
    )
    row = {"partition_value": "<nil>", "partition_values": {"_date_month": None}}

    predicate = build_predicate(fields, row)

    assert predicate.terms[0].value is None
    assert predicate.unsupported_reason is not None


def test_month_partitions_render_as_month_ranges_on_both_engines():
    # 671 months after 1970-01 is 2025-12.
    plan = build_plan(
        _stats(),
        [_row(671), _row(672), _row(None, display="<nil>")],
        Mode.STANDARD,
        {"min_small_files": 10, "chunk_size_gb": 100},
    )

    optimize = [op for op in plan.operations if op.type is OperationType.OPTIMIZE]
    assert optimize, plan.notes
    spark_sql = "\n".join(s.sql for s in render_spark(plan) if s.action is OperationType.OPTIMIZE)
    trino_sql = "\n".join(s.sql for s in render_trino(plan) if s.action is OperationType.OPTIMIZE)

    for sql in (spark_sql, trino_sql):
        for bound in ("2025-12-01", "2026-01-01", "2026-02-01"):
            assert bound in sql
        assert "joining_date >=" in sql and "joining_date <" in sql
        assert "1971" not in sql, "identity guess rendered epoch-day 671 as 1971-11-03"
        assert "<nil>" not in sql
    assert any("missing partition value" in note for note in plan.notes)


def test_convert_deletes_drops_bucket_partitions_it_cannot_render():
    bucket = {
        "col_type": "int",
        "transform": "bucket[16]",
        "base_column": "user_id",
        "partition_field": "user_id_bucket",
        "transform_param": 16,
    }
    partitions = [
        {
            "partition_value": str(i),
            "partition_values": {"user_id_bucket": i},
            "small_files_in_partition_count": 750 if i < 3 else 19,
        }
        for i in range(16)
    ]
    plan = build_plan(
        _stats(
            partition_col="user_id",
            partition_col_type="int",
            raw={"partition_fields": [bucket]},
            total_delete_files=12_000,
            delete_file_pct=80.0,
            total_size_gb=5,
        ),
        partitions,
        Mode.STANDARD,
        {
            "min_small_files": 10,
            "convert_deletes_threshold_pct": 5.0,
            "force_convert_deletes": True,
        },
    )

    convert = next(op for op in plan.operations if op.type is OperationType.CONVERT_DELETES)
    assert not convert.predicates
    render_spark(plan)
