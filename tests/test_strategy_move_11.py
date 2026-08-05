"""Move 11 — standalone manifest-rewrite quick action.

The operation type, both SQL dialects, the REST endpoint, the planner, and the UI buttons
already exist. These tests pin the publicly-observable contracts so none of them can
regress silently.
"""

from __future__ import annotations

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.spark.sql import render as render_spark
from cryo.engines.trino.sql import render as render_trino
from cryo.planner.planner import build_plan


def test_standalone_manifest_rewrite_renders_for_trino() -> None:
    """Mode.MANIFEST_ONLY emits exactly one OPTIMIZE_MANIFESTS op per table,
    rendered as the Trino `EXECUTE optimize_manifests` procedure call — no
    expire/orphan/data-compaction side effects, which is the whole point of
    the standalone quick action.
    """
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.MANIFEST_ONLY,
        operations=(Operation(OperationType.OPTIMIZE_MANIFESTS, {}),),
    )

    statements = render_trino(plan)
    assert len(statements) == 1
    assert statements[0].action is OperationType.OPTIMIZE_MANIFESTS
    assert statements[0].sql == "ALTER TABLE iceberg.demo.events EXECUTE optimize_manifests"


def test_standalone_manifest_rewrite_renders_for_spark() -> None:
    plan = MaintenancePlan(
        table_ref="iceberg.demo.events",
        mode=Mode.MANIFEST_ONLY,
        operations=(Operation(OperationType.OPTIMIZE_MANIFESTS, {}),),
    )

    statements = render_spark(plan)
    assert len(statements) == 1
    assert statements[0].action is OperationType.OPTIMIZE_MANIFESTS
    assert (
        statements[0].sql == "CALL iceberg.system.rewrite_manifests(table => 'iceberg.demo.events')"
    )


def test_manifest_only_planner_emits_only_manifest_rewrite_operation() -> None:
    """Pins down the planner contract: MANIFEST_ONLY mode produces no expire,
    orphan-removal, or OPTIMIZE alongside the manifest rewrite — matching what
    the quick action advertises to operators (no data-file compaction).
    """
    plan = build_plan(
        {"schema_name": "demo", "table_name": "events"},
        [],
        Mode.MANIFEST_ONLY,
        {},
    )

    assert [op.type for op in plan.operations] == [OperationType.OPTIMIZE_MANIFESTS]
