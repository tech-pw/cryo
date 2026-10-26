"""Trino plans say what Trino actually runs (issues #198 and #139)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cryo.domain.enums import EngineType, Mode, OperationType, PartitionTransform
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    PartitionPredicate,
    PartitionPredicateTerm,
)
from cryo.engines.base import RenderedStatement
from cryo.engines.trino.engine import TrinoEngine
from cryo.engines.trino.sql import render
from cryo.planner.execution import gate_trino_semantics


@dataclass
class _Destination:
    engine: EngineType | str
    config: dict[str, Any] = field(default_factory=dict)


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.stats = {"queryId": "q1"}

    def execute(self, sql: str) -> None:
        self.executed.append(sql)

    def fetchone(self) -> tuple[object, ...] | None:
        return (0,)

    def fetchall(self) -> list[tuple[object, ...]]:
        return []

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor

    def close(self) -> None:
        pass


def _plan(*operations: Operation, notes: tuple[str, ...] = ()) -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="iceberg.demo.events", mode=Mode.STANDARD, operations=operations, notes=notes
    )


def _predicate() -> PartitionPredicate:
    return PartitionPredicate(
        terms=(PartitionPredicateTerm("event_date", PartitionTransform.DAY, "2026-10-01", "date"),)
    )


# --- #198: protected snapshots -------------------------------------------------------------


def test_trino_drops_expire_when_snapshots_are_protected_and_notes_why() -> None:
    plan = _plan(
        Operation(
            OperationType.EXPIRE_SNAPSHOTS,
            {"retention_days": 7, "retain_last": 1, "protected_snapshot_ids": [11, 22]},
        ),
        Operation(OperationType.OPTIMIZE, {"file_size_mb": 256}),
        notes=("earlier note",),
    )

    gated = gate_trino_semantics(plan, destination=_Destination(EngineType.TRINO))

    assert [op.type for op in gated.operations] == [OperationType.OPTIMIZE]
    assert gated.notes[0] == "earlier note"
    assert "Skipped expire_snapshots: 2 snapshot(s)" in gated.notes[1]
    assert "Spark" in gated.notes[1]
    # The rest of the plan still renders.
    assert [s.sql for s in render(gated)] == [
        "ALTER TABLE iceberg.demo.events EXECUTE optimize(file_size_threshold => '256MB')"
    ]


def test_trino_keeps_expire_without_protected_snapshots() -> None:
    plan = _plan(Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}))
    assert gate_trino_semantics(plan, destination=_Destination(EngineType.TRINO)) is plan


def test_spark_plan_keeps_protected_expire() -> None:
    plan = _plan(
        Operation(OperationType.EXPIRE_SNAPSHOTS, {"protected_snapshot_ids": [1]}),
        Operation(OperationType.CONVERT_DELETES, {}),
    )
    assert gate_trino_semantics(plan, destination=_Destination(EngineType.SPARK_EMR)) is plan


# --- #198: convert_deletes is an OPTIMIZE on Trino ----------------------------------------


def test_trino_relabels_convert_deletes_as_optimize() -> None:
    plan = _plan(Operation(OperationType.CONVERT_DELETES, {"file_size_mb": 128}))

    gated = gate_trino_semantics(plan, destination=_Destination("trino"))

    assert [op.type for op in gated.operations] == [OperationType.OPTIMIZE]
    assert gated.operations[0].params["planned_as"] == "convert_deletes"
    assert "convert_deletes runs as OPTIMIZE on Trino" in gated.notes[0]
    assert "the whole table" in gated.notes[0]
    statements = render(gated)
    assert [s.action for s in statements] == [OperationType.OPTIMIZE]
    assert statements[0].sql == (
        "ALTER TABLE iceberg.demo.events EXECUTE optimize(file_size_threshold => '128MB')"
    )


def test_trino_relabels_partition_scoped_convert_deletes_and_keeps_predicates() -> None:
    predicate = _predicate()
    plan = _plan(Operation(OperationType.CONVERT_DELETES, {}, (predicate,)))

    gated = gate_trino_semantics(plan, destination=_Destination(EngineType.TRINO))

    assert gated.operations[0].type is OperationType.OPTIMIZE
    assert gated.operations[0].predicates == (predicate,)
    assert "the selected partitions" in gated.notes[0]


def test_trino_drops_convert_deletes_already_covered_by_table_wide_optimize() -> None:
    plan = _plan(
        Operation(OperationType.OPTIMIZE, {"file_size_mb": 256}),
        Operation(OperationType.CONVERT_DELETES, {"file_size_mb": 256}),
    )

    gated = gate_trino_semantics(plan, destination=_Destination(EngineType.TRINO))

    assert [op.type for op in gated.operations] == [OperationType.OPTIMIZE]
    assert "Dropped convert_deletes" in gated.notes[0]


def test_trino_renderer_marks_direct_convert_deletes_as_optimize() -> None:
    statement = render(_plan(Operation(OperationType.CONVERT_DELETES, {})))[0]
    assert statement.metadata["trino_procedure"] == "optimize"


# --- #139: retention floors ---------------------------------------------------------------


def test_trino_lowers_retention_floor_only_to_the_planned_retention() -> None:
    statements = render(
        _plan(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 1, "retain_last": 1}),
            Operation(OperationType.REMOVE_ORPHAN_FILES, {"retention_days": 3}),
        )
    )
    assert statements[0].metadata["catalog_session_properties"] == {
        "expire_snapshots_min_retention": "1d"
    }
    assert statements[1].metadata["catalog_session_properties"] == {
        "remove_orphan_files_min_retention": "3d"
    }


def test_trino_leaves_floor_alone_at_or_above_default() -> None:
    statements = render(
        _plan(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),
            Operation(OperationType.REMOVE_ORPHAN_FILES, {"retention_days": 14}),
            Operation(OperationType.OPTIMIZE, {}),
        )
    )
    assert all("catalog_session_properties" not in s.metadata for s in statements)


def _run(engine_config: dict[str, Any], statement: RenderedStatement) -> list[str]:
    cursor = _Cursor()
    engine = TrinoEngine(engine_config, connector=lambda _c: _Connection(cursor))
    result = engine.execute(statement)
    assert result.ok is True
    return cursor.executed


def test_trino_engine_sets_floor_on_the_statement_catalog_before_the_procedure() -> None:
    statement = render(
        MaintenancePlan(
            table_ref="lake.demo.events",
            mode=Mode.DEEP,
            operations=(Operation(OperationType.REMOVE_ORPHAN_FILES, {"retention_days": 2}),),
        )
    )[0]

    executed = _run({"host": "localhost", "catalog": "iceberg"}, statement)

    assert executed == [
        "SET SESSION task_min_writer_count = 8",
        "SET SESSION writer_scaling_min_data_processed = '32MB'",
        "SET SESSION lake.remove_orphan_files_min_retention = '2d'",
        "ALTER TABLE lake.demo.events EXECUTE remove_orphan_files(retention_threshold => '2d')",
    ]


def test_trino_engine_uses_coordinator_catalog_for_two_part_refs_and_quotes_odd_names() -> None:
    statement = RenderedStatement(
        action=OperationType.EXPIRE_SNAPSHOTS,
        sql="ALTER TABLE demo.events EXECUTE expire_snapshots(retention_threshold => '1d')",
        engine=EngineType.TRINO,
        metadata={
            "table_ref": "demo.events",
            "catalog_session_properties": {"expire_snapshots_min_retention": "1d"},
        },
    )

    executed = _run({"host": "localhost", "catalog": "my-lake"}, statement)

    assert executed[2] == "SET SESSION \"my-lake\".expire_snapshots_min_retention = '1d'"


def test_trino_engine_reads_catalog_from_quoted_table_ref() -> None:
    statement = RenderedStatement(
        action=OperationType.EXPIRE_SNAPSHOTS,
        sql="ALTER TABLE x EXECUTE expire_snapshots(retention_threshold => '1d')",
        engine=EngineType.TRINO,
        metadata={
            "table_ref": '"ice.berg"."demo"."events"',
            "catalog_session_properties": {"expire_snapshots_min_retention": "1d"},
        },
    )

    executed = _run({"host": "localhost"}, statement)

    assert executed[2] == "SET SESSION \"ice.berg\".expire_snapshots_min_retention = '1d'"


def test_trino_engine_skips_floor_without_any_catalog() -> None:
    statement = RenderedStatement(
        action=OperationType.EXPIRE_SNAPSHOTS,
        sql="ALTER TABLE demo.events EXECUTE expire_snapshots(retention_threshold => '1d')",
        engine=EngineType.TRINO,
        metadata={
            "table_ref": "demo.events",
            "catalog_session_properties": {"expire_snapshots_min_retention": "1d"},
        },
    )

    executed = _run({"host": "localhost"}, statement)

    assert not any("min_retention" in sql for sql in executed)
