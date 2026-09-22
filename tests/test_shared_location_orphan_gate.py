"""remove_orphan_files must not run on a table that shares an S3 location with another.

The procedure deletes everything under the table root that this table's metadata does not
reference -- which is exactly what a co-located table's live data files look like. Overlap
detection already existed (cryo/anomalies/paths.py) but only produced a report; nothing
consulted it before deleting. See docs/incidents/rca-cdp-catalog-evolving-shared-s3-drop.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.planner.execution import (
    build_execution_plan,
    gate_orphan_removal_on_shared_location,
)

_STATS = {
    "schema_name": "demo",
    "table_name": "events",
    "small_files_count": 500,
    "total_size_gb": 10,
}


@dataclass(frozen=True)
class _Destination:
    engine: EngineType = EngineType.SPARK_EMR
    config: dict[str, Any] = field(default_factory=dict)


_DESTINATION = _Destination()
_ALL_FOUR = (
    OperationType.OPTIMIZE,
    OperationType.OPTIMIZE_MANIFESTS,
    OperationType.EXPIRE_SNAPSHOTS,
    OperationType.REMOVE_ORPHAN_FILES,
)


def _plan_with(*operation_types: OperationType) -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="cdp.demo.events",
        mode=Mode.DEEP,
        operations=tuple(Operation(operation_type, {}) for operation_type in operation_types),
        notes=(),
    )


def test_gate_drops_orphan_removal_when_the_location_is_shared() -> None:
    gated = gate_orphan_removal_on_shared_location(
        _plan_with(OperationType.OPTIMIZE, OperationType.REMOVE_ORPHAN_FILES),
        has_shared_location=True,
    )

    assert [operation.type for operation in gated.operations] == [OperationType.OPTIMIZE]
    assert any("overlaps another table" in note for note in gated.notes)


def test_gate_leaves_every_other_operation_in_place() -> None:
    """Only orphan removal deletes by listing. The other three go through table metadata and
    cannot touch a co-located table's files, so gating them would cost coverage for nothing."""
    gated = gate_orphan_removal_on_shared_location(
        _plan_with(*_ALL_FOUR),
        has_shared_location=True,
    )

    assert [operation.type for operation in gated.operations] == [
        OperationType.OPTIMIZE,
        OperationType.OPTIMIZE_MANIFESTS,
        OperationType.EXPIRE_SNAPSHOTS,
    ]


def test_gate_is_a_no_op_for_a_table_with_its_own_location() -> None:
    plan = _plan_with(*_ALL_FOUR)

    assert gate_orphan_removal_on_shared_location(plan, has_shared_location=False) is plan


def test_build_execution_plan_applies_the_gate() -> None:
    plan = build_execution_plan(
        stats=_STATS,
        partitions=[],
        mode=Mode.DEEP,
        params={"min_small_files": 10},
        destination=_DESTINATION,
        operations=_ALL_FOUR,
        has_shared_location=True,
    )

    assert all(
        operation.type is not OperationType.REMOVE_ORPHAN_FILES for operation in plan.operations
    )


def test_build_execution_plan_plans_orphan_removal_without_an_overlap() -> None:
    plan = build_execution_plan(
        stats=_STATS,
        partitions=[],
        mode=Mode.DEEP,
        params={"min_small_files": 10},
        destination=_DESTINATION,
        operations=_ALL_FOUR,
        has_shared_location=False,
    )

    assert any(
        operation.type is OperationType.REMOVE_ORPHAN_FILES for operation in plan.operations
    )
