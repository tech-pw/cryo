from __future__ import annotations

import pytest

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.spark.sql import UnsupportedPredicateError as SparkUnsupported
from cryo.engines.spark.sql import render as render_spark
from cryo.engines.trino.sql import UnsupportedPredicateError as TrinoUnsupported
from cryo.engines.trino.sql import render as render_trino
from cryo.planner.equality_deletes import partitions_with_equality_deletes
from cryo.planner.guardrails import CORRECT_ORDER
from cryo.planner.planner import build_plan
from cryo.scanner.health import compute_health_score


def test_only_partitions_with_equality_deletes_are_selected() -> None:
    selected = partitions_with_equality_deletes(
        (
            {"partition_value": "a", "raw": {"equality_delete_files": 2}},
            {"partition_value": "b", "raw": {"equality_delete_files": 0}},
            {"partition_value": "c"},
        )
    )
    assert [row["partition_value"] for row in selected] == ["a"]


def test_planner_emits_conversion_before_optimize() -> None:
    plan = build_plan(
        {"table_ref": "cat.db.events", "equality_delete_files": 3},
        ({"partition_value": "2026-10-01", "raw": {"equality_delete_files": 3}},),
        Mode.STANDARD,
        {"convert_equality_deletes": True, "min_small_files": 1},
    )
    types = [op.type for op in plan.operations]
    assert OperationType.CONVERT_EQUALITY_DELETES in types
    assert CORRECT_ORDER.index(OperationType.CONVERT_EQUALITY_DELETES) < CORRECT_ORDER.index(
        OperationType.OPTIMIZE
    )
    conversion = next(
        op for op in plan.operations if op.type is OperationType.CONVERT_EQUALITY_DELETES
    )
    assert conversion.params["rewrite_data_files"] is False
    assert any("fails closed" in note for note in plan.notes)


def test_disabled_conversion_is_not_planned() -> None:
    plan = build_plan(
        {"table_ref": "cat.db.events", "equality_delete_files": 3},
        (),
        Mode.STANDARD,
        {},
    )
    assert OperationType.CONVERT_EQUALITY_DELETES not in {op.type for op in plan.operations}


def _plan() -> MaintenancePlan:
    return MaintenancePlan(
        "catalog.db.events",
        Mode.STANDARD,
        (Operation(OperationType.CONVERT_EQUALITY_DELETES, {"rewrite_data_files": False}),),
    )


def test_trino_refuses_to_rewrite_data_files() -> None:
    with pytest.raises(TrinoUnsupported, match="must not be rewritten as OPTIMIZE"):
        render_trino(_plan())


def test_spark_refuses_to_rewrite_data_files() -> None:
    with pytest.raises(SparkUnsupported, match="rewrite_data_files"):
        render_spark(_plan())


def test_equality_delete_buildup_deducts_and_names_the_fix() -> None:
    _score, _grade, deductions = compute_health_score(
        {"equality_delete_files": 2, "total_files": 10},
        health_small_file_count=0,
    )
    match = next(item for item in deductions if item["label"] == "Equality delete buildup")
    assert match["deduction"] == 8
    assert "convert_equality_deletes" in match["reason"]
    assert "without rewriting data files" in match["reason"]
