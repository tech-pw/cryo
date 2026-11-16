"""A table with a regular overwrite/delete writer gets small, partition-scoped rewrites.

2026-11-14/15: two ~35 GB tables written by an hourly copy-on-write MERGE fell under the
small-table bypass, got one full-table rewrite_data_files each (66-112 min of partial-progress
commits), and every ingestion attempt that overlapped one failed.
"""

from __future__ import annotations

from typing import Any

from cryo.domain.enums import Mode, OperationType
from cryo.planner.planner import build_plan, effective_optimize_chunk_size_gb

HOURLY_WRITER = {"writer_cadence": {"runs": 24, "interval_minutes": 60.0, "run_span_minutes": 0}}


def _stats(signals: dict[str, Any] | None) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "schema_name": "cdp_revenue",
        "table_name": "gold_student_fee_mappings",
        "small_files_count": 43,
        "total_size_gb": 34.3,
        "partition_fields": [
            {
                "base_column": "createdat",
                "partition_field": "createdat_day",
                "transform": "day",
                "col_type": "timestamptz",
            }
        ],
    }
    if signals is not None:
        stats["raw"] = {"commit_signals": signals}
    return stats


PARTITIONS = [
    {
        "partition_value": f"2025-05-{day:02d}",
        "small_files_in_partition_count": 12,
        "partition_size_gb": 1.5,
    }
    for day in range(1, 11)
]
PARAMS = {"min_small_files": 10, "chunk_size_gb": 100, "max_partitions_per_chunk": 100}


def _optimize_ops(plan: Any) -> list[Any]:
    return [op for op in plan.operations if op.type is OperationType.OPTIMIZE]


def test_a_table_without_a_regular_writer_keeps_the_small_table_bypass() -> None:
    plan = build_plan(_stats(None), PARTITIONS, Mode.STANDARD, PARAMS)

    ops = _optimize_ops(plan)
    assert len(ops) == 1 and not ops[0].predicates
    assert any("Planned full-table optimize" in note for note in plan.notes)


def test_a_writer_contended_table_is_rewritten_in_small_partition_chunks() -> None:
    plan = build_plan(_stats(HOURLY_WRITER), PARTITIONS, Mode.STANDARD, PARAMS)

    ops = _optimize_ops(plan)
    # 10 partitions x 1.5 GB under a 4 GB cap: two per chunk.
    assert len(ops) == 5
    assert all(len(op.predicates) == 2 for op in ops)
    assert not any("Planned full-table optimize" in note for note in plan.notes)
    assert any(
        "Writer-contended table" in note and "capped at 4 GB (was 100 GB)" in note
        for note in plan.notes
    )


def test_an_older_scan_without_writer_cadence_still_gets_the_cap() -> None:
    signals = {"data_commits": 72, "arrival_gap_p50_hours": 1.0, "hours_since_data_commit": 0.2}

    plan = build_plan(_stats(signals), PARTITIONS, Mode.STANDARD, PARAMS)

    assert len(_optimize_ops(plan)) == 5


def test_the_preset_can_opt_out_or_resize_the_cap() -> None:
    opted_out = build_plan(
        _stats(HOURLY_WRITER), PARTITIONS, Mode.STANDARD, {**PARAMS, "writer_window": False}
    )
    resized = build_plan(
        _stats(HOURLY_WRITER),
        PARTITIONS,
        Mode.STANDARD,
        {**PARAMS, "writer_window": {"contended_chunk_size_gb": 8}},
    )

    assert len(_optimize_ops(opted_out)) == 1
    assert len(_optimize_ops(resized)) == 2


def test_the_cap_never_raises_a_smaller_preset_chunk() -> None:
    plan = build_plan(
        _stats(HOURLY_WRITER), PARTITIONS, Mode.STANDARD, {**PARAMS, "chunk_size_gb": 1.5}
    )

    assert len(_optimize_ops(plan)) == 10
    assert not any("Writer-contended table" in note for note in plan.notes)


def test_full_table_guards_see_the_same_cap() -> None:
    assert effective_optimize_chunk_size_gb(_stats(HOURLY_WRITER), PARAMS) == 4.0
    assert effective_optimize_chunk_size_gb(_stats(None), PARAMS) == 100.0
