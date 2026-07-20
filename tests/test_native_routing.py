"""Tests for native engine COW/MOR operation routing (Phase C/D)."""

from __future__ import annotations

import pytest

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.engine import NativeEngine, _supported_operations
from cryo.engines.native.routing import (
    NATIVE_DELETE_FILES_DETAIL,
    NATIVE_MANIFESTS_DETAIL,
    NATIVE_V3_PARTITIONED_DV_DETAIL,
    has_nonzero_delete_files,
    has_v3_partitioned_deletion_vectors,
    is_mor_table,
    native_cow_optimize_enabled,
    native_optimize_enabled,
    resolve_native_routing,
)


def _deep_plan() -> MaintenancePlan:
    return MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),
            Operation(OperationType.REMOVE_ORPHAN_FILES, {"orphan_min_age_hours": 72}),
            Operation(OperationType.OPTIMIZE, {"file_size_mb": 256}),
            Operation(OperationType.OPTIMIZE_MANIFESTS, {}),
        ),
    )


def test_is_mor_table_detects_delete_files() -> None:
    assert is_mor_table({"total_delete_files": 3})
    assert is_mor_table({"delete_file_pct": 0.1})
    assert not is_mor_table({"total_delete_files": 0, "delete_file_pct": 0})


def test_has_nonzero_delete_files() -> None:
    assert has_nonzero_delete_files({"total_delete_files": 2})
    assert has_nonzero_delete_files({"raw": {"total_delete_files": 1}})
    assert not has_nonzero_delete_files({"total_delete_files": 0})
    assert not has_nonzero_delete_files({"delete_file_pct": 0.5})


def test_native_optimize_enabled_blocks_delete_files_on_go_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    cfg = {"engine_impl": "go"}
    assert native_optimize_enabled({"total_delete_files": 0}, cfg)
    assert not native_optimize_enabled({"total_delete_files": 5}, cfg)


def test_cow_optimize_enabled_only_with_go_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRYO_ENGINE_IMPL", raising=False)
    stats = {"total_delete_files": 0}
    assert not native_cow_optimize_enabled(stats, {})
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    assert native_cow_optimize_enabled(stats, {})
    assert not native_cow_optimize_enabled({"total_delete_files": 2}, {"engine_impl": "go"})


def test_v3_partitioned_deletion_vectors_gate() -> None:
    gated_stats = {
        "format_version": 3,
        "partition_col": "event_date",
        "total_delete_files": 2,
        "raw": {
            "format_version": 3,
            "partition_col": "event_date",
            "delete_files": [
                {"file_path": "s3://bucket/metadata/deletion-vector-abc.puffin", "content": "POSITION_DELETES"}
            ],
        },
    }
    assert has_v3_partitioned_deletion_vectors(gated_stats)
    assert not native_optimize_enabled(gated_stats, {"engine_impl": "go"})

    mor_v3_pos = {
        "format_version": 3,
        "partition_col": "event_date",
        "total_delete_files": 2,
        "raw": {
            "format_version": 3,
            "partition_col": "event_date",
            "delete_files": [{"file_path": "s3://bucket/data/del.parquet", "content": "POSITION_DELETES"}],
        },
    }
    assert not has_v3_partitioned_deletion_vectors(mor_v3_pos)
    assert not native_optimize_enabled(mor_v3_pos, {"engine_impl": "go"})


def test_resolve_native_routing_cow_includes_optimize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    routing = resolve_native_routing(
        _deep_plan(),
        {"total_delete_files": 0},
        {"engine_impl": "go", "sql_fallback_destination_id": "trino-1"},
    )
    native_types = {op.type for op in routing.native_operations}
    assert native_types == {
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
        OperationType.OPTIMIZE,
    }
    # optimize_manifests routes to SQL fallback when configured (not skipped).
    fallback_types = {op.type for op, _ in routing.fallback_operations}
    assert fallback_types == {OperationType.OPTIMIZE_MANIFESTS}
    assert not routing.skipped_operations


def test_resolve_native_routing_delete_files_blocks_optimize_no_trino_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    routing = resolve_native_routing(
        _deep_plan(),
        {"total_delete_files": 5, "format_version": 2},
        {"engine_impl": "go", "sql_fallback_destination_id": "trino-1"},
    )
    native_types = {op.type for op in routing.native_operations}
    assert native_types == {
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
    }
    assert OperationType.OPTIMIZE not in native_types
    # Rewrite is hard-blocked — never routed to Trino even when sql_fallback is set.
    fallback_types = {op.type for op, _ in routing.fallback_operations}
    assert OperationType.OPTIMIZE not in fallback_types
    assert OperationType.OPTIMIZE_MANIFESTS in fallback_types
    skipped = {op.type: detail for op, detail in routing.skipped_operations}
    assert OperationType.OPTIMIZE in skipped
    assert NATIVE_DELETE_FILES_DETAIL in skipped[OperationType.OPTIMIZE]


def test_resolve_native_routing_v3_dv_routes_optimize_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    stats = {
        "format_version": 3,
        "partition_col": "event_date",
        "total_delete_files": 0,
        "raw": {
            "format_version": 3,
            "partition_col": "event_date",
            "delete_files": [
                {"file_path": "s3://bucket/metadata/deletion-vector-abc.puffin"}
            ],
        },
    }
    routing = resolve_native_routing(
        _deep_plan(),
        stats,
        {"engine_impl": "go", "sql_fallback_destination_id": "trino-1"},
    )
    native_types = {op.type for op in routing.native_operations}
    assert OperationType.OPTIMIZE not in native_types
    fallback_types = {op.type for op, _ in routing.fallback_operations}
    assert OperationType.OPTIMIZE in fallback_types
    optimize_detail = next(
        detail for op, detail in routing.fallback_operations if op.type is OperationType.OPTIMIZE
    )
    assert NATIVE_V3_PARTITIONED_DV_DETAIL in optimize_detail


def test_resolve_native_routing_skips_without_sql_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    routing = resolve_native_routing(_deep_plan(), {"total_delete_files": 0}, {"engine_impl": "go"})
    skipped_types = {op.type for op, _ in routing.skipped_operations}
    assert OperationType.OPTIMIZE_MANIFESTS in skipped_types
    manifests_detail = next(
        detail
        for op, detail in routing.skipped_operations
        if op.type is OperationType.OPTIMIZE_MANIFESTS
    )
    assert NATIVE_MANIFESTS_DETAIL in manifests_detail


def test_native_render_includes_cow_optimize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    engine = NativeEngine(
        config={"engine_impl": "go"},
        table_stats={"total_delete_files": 0},
    )
    statements = engine.render(_deep_plan())
    assert len(statements) == 1
    payload = statements[0].payload
    operations = payload.get("operations")
    assert isinstance(operations, list)
    rendered_types = {item["type"] for item in operations}
    assert rendered_types == {
        "expire_snapshots",
        "remove_orphan_files",
        "optimize",
    }


def test_native_render_excludes_optimize_when_delete_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    engine = NativeEngine(
        config={"engine_impl": "go"},
        table_stats={"total_delete_files": 3, "format_version": 2},
    )
    statements = engine.render(_deep_plan())
    assert len(statements) == 1
    operations = statements[0].payload.get("operations")
    assert isinstance(operations, list)
    rendered_types = {item["type"] for item in operations}
    assert rendered_types == {
        "expire_snapshots",
        "remove_orphan_files",
    }
    assert "optimize" not in rendered_types


def test_resolve_native_routing_zorder_routes_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {"file_size_mb": 256, "strategy": "zorder", "zorder_columns": ["user_id"]},
            ),
        ),
    )
    routing = resolve_native_routing(
        plan,
        {"total_delete_files": 0},
        {"engine_impl": "go", "sql_fallback_destination_id": "trino-1"},
    )
    assert not routing.native_operations
    assert {op.type for op, _ in routing.fallback_operations} == {OperationType.OPTIMIZE}


def test_supported_operations_preserves_planner_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    plan = _deep_plan()
    supported = _supported_operations(plan, {"total_delete_files": 0}, {"engine_impl": "go"})
    assert [op.type for op in supported] == [
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
        OperationType.OPTIMIZE,
    ]


def test_convert_deletes_routes_to_sql_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.STANDARD,
        operations=(
            Operation(OperationType.CONVERT_DELETES, {"delete_file_threshold": 1}),
        ),
    )
    routing = resolve_native_routing(
        plan,
        {"total_delete_files": 5},
        {"engine_impl": "go", "sql_fallback_destination_id": "spark-1"},
    )
    assert not routing.native_operations
    assert {op.type for op, _ in routing.fallback_operations} == {OperationType.CONVERT_DELETES}


def test_rewrite_position_deletes_routes_to_sql_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Okolnychyi minor delete compaction has no cryo-engine-go implementation -- same
    fallback-or-skip treatment as CONVERT_DELETES above, not a silent native no-op."""
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.REWRITE_POSITION_DELETES, {}),),
    )
    routing = resolve_native_routing(
        plan,
        {"position_delete_files": 80},
        {"engine_impl": "go", "sql_fallback_destination_id": "spark-1"},
    )
    assert not routing.native_operations
    assert {op.type for op, _ in routing.fallback_operations} == {
        OperationType.REWRITE_POSITION_DELETES
    }


def test_rewrite_position_deletes_skipped_without_sql_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.REWRITE_POSITION_DELETES, {}),),
    )
    routing = resolve_native_routing(plan, {"position_delete_files": 80}, {"engine_impl": "go"})
    assert not routing.native_operations
    assert not routing.fallback_operations
    assert {op.type for op, _ in routing.skipped_operations} == {
        OperationType.REWRITE_POSITION_DELETES
    }
