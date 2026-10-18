"""Operations a destination engine cannot run are skipped with a note, not left to fail."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.trino.sql import render
from cryo.planner.execution import gate_engine_unsupported


@dataclass(frozen=True)
class _Destination:
    engine: EngineType | str
    config: dict[str, Any] = field(default_factory=dict)


def _plan(*types: OperationType) -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="lakehouse.demo.events",
        mode=Mode.DEEP,
        operations=tuple(Operation(t, {}) for t in types),
        notes=("existing note",),
    )


def test_trino_drops_position_delete_rewrite_and_says_why() -> None:
    gated = gate_engine_unsupported(
        _plan(
            OperationType.REWRITE_POSITION_DELETES,
            OperationType.OPTIMIZE,
            OperationType.EXPIRE_SNAPSHOTS,
        ),
        destination=_Destination(EngineType.TRINO),
    )

    assert [op.type for op in gated.operations] == [
        OperationType.OPTIMIZE,
        OperationType.EXPIRE_SNAPSHOTS,
    ]
    assert gated.notes[0] == "existing note"
    assert "Skipped rewrite_position_deletes" in gated.notes[1]
    render(gated)


def test_engine_given_as_string_is_matched() -> None:
    gated = gate_engine_unsupported(
        _plan(OperationType.REWRITE_POSITION_DELETES),
        destination=_Destination("trino"),
    )

    assert gated.operations == ()


def test_spark_keeps_position_delete_rewrite() -> None:
    plan = _plan(OperationType.REWRITE_POSITION_DELETES, OperationType.OPTIMIZE)

    assert gate_engine_unsupported(plan, destination=_Destination(EngineType.SPARK_EMR)) is plan


def test_trino_plan_without_unsupported_ops_is_unchanged() -> None:
    plan = _plan(OperationType.OPTIMIZE)

    assert gate_engine_unsupported(plan, destination=_Destination(EngineType.TRINO)) is plan
