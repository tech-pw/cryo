"""#265: optimize rewrites to the table's own target size, and hot day partitions are skipped
by default."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from cryo.domain.enums import Mode
from cryo.engines.spark.sql import render
from cryo.maintenance.manifest import DEFAULT_PARAMS
from cryo.planner.planner import (
    DEFAULT_TIME_PARTITION_IDLE_HOURS,
    build_plan,
    table_target_file_size_mb,
)

MB = 1024 * 1024
DAY_FIELD = {
    "base_column": "event_date",
    "partition_field": "event_date_day",
    "transform": "day",
    "col_type": "date",
}


def _stats(**extra: Any) -> dict[str, Any]:
    return {
        "catalog": "demo",
        "schema_name": "mart",
        "table_name": "events",
        "small_files_count": 500,
        "total_size_gb": 10,
        **extra,
    }


def _params(**extra: Any) -> dict[str, Any]:
    return {"min_small_files": 10, "chunk_size_gb": 100, **extra}


def _optimize(plan):
    return [op for op in plan.operations if op.type.value == "optimize"]


def test_table_target_file_size_mb_reads_column_then_raw() -> None:
    assert table_target_file_size_mb({"target_file_size_bytes": 512 * MB}) == 512
    assert table_target_file_size_mb({"raw": {"target_file_size_bytes": 128 * MB}}) == 128
    assert table_target_file_size_mb({}) is None
    assert table_target_file_size_mb({"target_file_size_bytes": 0}) is None
    assert table_target_file_size_mb({"target_file_size_bytes": "junk"}) is None


def test_optimize_rewrites_to_the_tables_target_when_preset_is_silent() -> None:
    plan = build_plan(_stats(target_file_size_bytes=512 * MB), [], Mode.STANDARD, _params())
    (op,) = _optimize(plan)
    assert op.params["file_size_mb"] == 512
    sql = render(plan)[[r.action.value for r in render(plan)].index("optimize")].sql
    assert f"'target-file-size-bytes', '{512 * MB}'" in sql
    # Persisting the table's own value is a no-op, not an overwrite to 256 MB.
    assert f"'write.target-file-size-bytes' = '{512 * MB}'" in sql


def test_optimize_falls_back_to_256_without_a_table_target() -> None:
    plan = build_plan(_stats(), [], Mode.STANDARD, _params())
    (op,) = _optimize(plan)
    assert op.params["file_size_mb"] == 256


def test_explicit_preset_size_wins_and_notes_the_mismatch() -> None:
    plan = build_plan(
        _stats(target_file_size_bytes=512 * MB), [], Mode.STANDARD, _params(file_size_mb=256)
    )
    (op,) = _optimize(plan)
    assert op.params["file_size_mb"] == 256
    assert any("differs from the table's write.target-file-size-bytes" in n for n in plan.notes)


def test_matching_explicit_size_has_no_note() -> None:
    plan = build_plan(
        _stats(target_file_size_bytes=512 * MB), [], Mode.STANDARD, _params(file_size_mb=512)
    )
    assert not any("differs from" in n for n in plan.notes)


def test_batch_defaults_no_longer_pin_size_or_disable_the_idle_gate() -> None:
    assert "file_size_mb" not in DEFAULT_PARAMS
    assert "partition_write_idle_hours" not in DEFAULT_PARAMS


def _day_partitions() -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    return [
        {
            "partition_value": "2026-10-26",
            "small_files_in_partition_count": 900,
            "partition_size_gb": 80,
            "last_write_at": (now - timedelta(minutes=5)).isoformat(),
        },
        {
            "partition_value": "2026-10-20",
            "small_files_in_partition_count": 300,
            "partition_size_gb": 20,
            "last_write_at": (now - timedelta(days=6)).isoformat(),
        },
    ]


def test_hot_day_partition_is_skipped_by_default() -> None:
    plan = build_plan(
        _stats(total_size_gb=500, partition_fields=[DAY_FIELD]),
        _day_partitions(),
        Mode.STANDARD,
        _params(),
    )
    assert any(
        f"Skipped hot partition 2026-10-26: writes within last {DEFAULT_TIME_PARTITION_IDLE_HOURS}h"
        in n
        for n in plan.notes
    )
    (op,) = _optimize(plan)
    rendered = " ".join(str(p) for p in op.predicates or ())
    assert "2026-10-20" in rendered and "2026-10-26" not in rendered


def test_non_time_partitions_stay_ungated_by_default() -> None:
    field = {"base_column": "region", "transform": "identity", "col_type": "string"}
    partitions = [
        {**row, "partition_value": value}
        for row, value in zip(_day_partitions(), ("eu", "us"), strict=True)
    ]
    plan = build_plan(
        _stats(total_size_gb=500, partition_fields=[field]),
        partitions,
        Mode.STANDARD,
        _params(),
    )
    assert not any("Skipped hot partition" in n for n in plan.notes)


def test_explicit_zero_turns_the_gate_off() -> None:
    plan = build_plan(
        _stats(total_size_gb=500, partition_fields=[DAY_FIELD]),
        _day_partitions(),
        Mode.STANDARD,
        _params(partition_write_idle_hours=0),
    )
    assert not any("Skipped hot partition" in n for n in plan.notes)


def test_equal_ranked_partitions_prefer_the_oldest() -> None:
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    partitions = [
        {
            "partition_value": value,
            "small_files_in_partition_count": 100,
            "partition_size_gb": 10,
            "last_write_at": old,
        }
        for value in ("2026-09-03", "2026-09-01", "2026-09-02")
    ]
    plan = build_plan(
        _stats(total_size_gb=500, partition_fields=[DAY_FIELD]),
        partitions,
        Mode.STANDARD,
        _params(chunk_size_gb=10, max_chunks=1),
    )
    (op,) = _optimize(plan)
    rendered = " ".join(str(p) for p in op.predicates or ())
    assert "2026-09-01" in rendered
    assert "2026-09-03" not in rendered
