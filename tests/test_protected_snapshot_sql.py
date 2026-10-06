from __future__ import annotations

from cryo.domain.enums import Mode, OperationType
from cryo.engines.base import EngineType
from cryo.engines.spark.sql import render as render_spark
from cryo.engines.trino.sql import render as render_trino
from cryo.planner.planner import build_plan


def _stats(schema: str = "s", table: str = "t") -> dict[str, object]:
    return {"schema_name": schema, "table_name": table, "files_per_partition": 0}


def test_expire_emits_keep_sql_when_protected_ids_provided() -> None:
    plan = build_plan(
        _stats(),
        [],
        Mode.STANDARD,
        {"retention_days": 7, "retain_last": 1},
        protected_snapshot_ids=[4242, 5151, 4242],
    )
    expire_op = next(op for op in plan.operations if op.type is OperationType.EXPIRE_SNAPSHOTS)
    assert expire_op.params["protected_snapshot_ids"] == [4242, 5151]


def test_expire_omits_keep_param_when_no_protected_ids() -> None:
    plan = build_plan(_stats(), [], Mode.STANDARD, {"retention_days": 7, "retain_last": 1})
    expire_op = next(op for op in plan.operations if op.type is OperationType.EXPIRE_SNAPSHOTS)
    assert "protected_snapshot_ids" not in expire_op.params


def test_trino_renderer_does_not_emit_set_snapshots() -> None:
    plan = build_plan(
        _stats(),
        [],
        Mode.STANDARD,
        {"retention_days": 7, "retain_last": 1},
        protected_snapshot_ids=[9, 7],
    )
    rendered = render_trino(plan)
    expire = next(s for s in rendered if str(s.action).endswith("expire_snapshots"))
    assert "set_snapshots" not in expire.sql
    assert "retention_threshold => '7d'" in expire.sql
    assert "retain_last => 1" in expire.sql


def test_trino_renderer_skips_set_when_no_protected_ids() -> None:
    plan = build_plan(_stats(), [], Mode.STANDARD, {"retention_days": 7, "retain_last": 1})
    rendered = render_trino(plan)
    expire = next(s for s in rendered if str(s.action).endswith("expire_snapshots"))
    assert "set_snapshots" not in expire.sql


def test_spark_renderer_tags_the_protected_snapshot_before_expire() -> None:
    """Iceberg has no set_snapshot_ref procedure; a tag is what expire_snapshots respects.
    The tag DDL and the CALL must be separate ";\\n"-joined statements, the only separator
    cryo_spark_runner.py splits on before handing each one to spark.sql()."""
    plan = build_plan(
        _stats(),
        [],
        Mode.STANDARD,
        {"retention_days": 7, "retain_last": 1},
        protected_snapshot_ids=[100, 200],
    )
    rendered = render_spark(plan)
    expire = next(s for s in rendered if str(s.action).endswith("expire_snapshots"))
    assert "set_snapshot_ref" not in expire.sql
    first, second, expire_stmt = expire.sql.split(";\n")
    assert first == (
        "ALTER TABLE iceberg.s.t CREATE OR REPLACE TAG `cryo-protect-100` AS OF VERSION 100"
    )
    assert second == (
        "ALTER TABLE iceberg.s.t CREATE OR REPLACE TAG `cryo-protect-200` AS OF VERSION 200"
    )
    assert expire_stmt.startswith("CALL iceberg.system.expire_snapshots(")


def test_protected_ids_are_sorted_deduped() -> None:
    plan = build_plan(
        _stats(),
        [],
        Mode.STANDARD,
        {"retention_days": 7, "retain_last": 1},
        protected_snapshot_ids=[9, 7, 9, 3],
    )
    expire_op = next(op for op in plan.operations if op.type is OperationType.EXPIRE_SNAPSHOTS)
    assert expire_op.params["protected_snapshot_ids"] == [3, 7, 9]


def test_engine_marker_trino_vs_spark() -> None:
    plan = build_plan(_stats(), [], Mode.STANDARD, {})
    rendered_trino = render_trino(plan)
    rendered_spark = render_spark(plan)
    assert all(s.engine is EngineType.TRINO for s in rendered_trino)
    assert all(s.engine is EngineType.SPARK_EMR for s in rendered_spark)
