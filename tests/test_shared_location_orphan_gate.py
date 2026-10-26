"""remove_orphan_files must not run on a table that shares an S3 location with another.

The procedure deletes everything under the table root that this table's metadata does not
reference -- which is exactly what a co-located table's live data files look like. Overlap
detection already existed (cryo/anomalies/paths.py) but only produced a report; nothing
consulted it before deleting. See a shared-location incident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.planner.execution import (
    SHARED_LOCATION_UNVERIFIED_NOTE,
    build_execution_plan,
    gate_orphan_removal_on_shared_location,
)
from cryo.worker.worker import Worker

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
        table_ref="sample.demo.events",
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

    assert any(operation.type is OperationType.REMOVE_ORPHAN_FILES for operation in plan.operations)


def test_gate_drops_orphan_removal_when_the_overlap_check_failed() -> None:
    """#197: an overlap lookup that failed is treated as shared, with its own note."""
    gated = gate_orphan_removal_on_shared_location(
        _plan_with(*_ALL_FOUR),
        has_shared_location=False,
        unverified=True,
    )

    assert OperationType.REMOVE_ORPHAN_FILES not in [op.type for op in gated.operations]
    assert [op.type for op in gated.operations] == list(_ALL_FOUR[:3])
    assert SHARED_LOCATION_UNVERIFIED_NOTE in gated.notes


def test_build_execution_plan_skips_orphan_removal_when_unverified() -> None:
    plan = build_execution_plan(
        stats=_STATS,
        partitions=[],
        mode=Mode.DEEP,
        params={"min_small_files": 10},
        destination=_DESTINATION,
        operations=_ALL_FOUR,
        shared_location_unverified=True,
    )

    assert all(op.type is not OperationType.REMOVE_ORPHAN_FILES for op in plan.operations)
    assert SHARED_LOCATION_UNVERIFIED_NOTE in plan.notes


class _RaisingStore:
    def has_shared_s3_location(self, table_id: str) -> bool:
        raise RuntimeError("database unavailable")


class _AnsweringStore:
    def __init__(self, answer: bool) -> None:
        self.answer = answer

    def has_shared_s3_location(self, table_id: str) -> bool:
        return self.answer


def _lookup(store: object) -> bool | None:
    return Worker._has_shared_location(SimpleNamespace(_store=store), "table-1")  # type: ignore[arg-type]


def test_worker_reports_a_failed_overlap_lookup_as_unknown_not_as_no_overlap() -> None:
    """A DB error used to come back as "no overlap", so the plan kept remove_orphan_files."""
    assert _lookup(_RaisingStore()) is None


def test_worker_keeps_no_overlap_default_for_stores_without_the_lookup() -> None:
    """In-memory/test wiring has no lookup at all -- distinct from a lookup that failed."""
    assert _lookup(object()) is False


def test_worker_passes_through_a_successful_lookup() -> None:
    assert _lookup(_AnsweringStore(True)) is True
    assert _lookup(_AnsweringStore(False)) is False
