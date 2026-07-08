"""End-to-end audit field mapping for cryo-engine-go subprocess results (findings #13/#14)."""

from __future__ import annotations

from cryo.domain.enums import EngineType, Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.base import RenderedStatement
from cryo.engines.native.engine import (
    _exec_result_from_native,
    _exec_results_from_native_batch,
)
from cryo.engines.native.runner import NativeExecuteResult, NativeOperationResult


def _optimize_result() -> NativeOperationResult:
    return NativeOperationResult(
        operation="rewrite_data_files",
        success=True,
        input_files=12,
        output_files=2,
        bytes_removed=1_024,
        bytes_written=8_192,
        duration_ms=900,
    )


def _expire_result() -> NativeOperationResult:
    return NativeOperationResult(
        operation="expire_snapshots",
        success=True,
        snapshots_expired=3,
        duration_ms=40,
    )


def _orphan_result() -> NativeOperationResult:
    return NativeOperationResult(
        operation="remove_orphan_files",
        success=True,
        input_files=5,
        output_files=0,
        bytes_removed=50_000,
        duration_ms=120,
    )


def test_optimize_maps_distinct_input_output_counts() -> None:
    stmt = RenderedStatement(
        action=OperationType.OPTIMIZE,
        sql="NATIVE:rewrite_data_files:demo.orders",
        engine=EngineType.NATIVE,
        payload={},
    )
    result = NativeExecuteResult(
        ok=True,
        table_ref="demo.orders",
        results=(_optimize_result(),),
    )
    exec_result = _exec_result_from_native(stmt, result, duration_ms=900)
    assert exec_result.input_files_count == 12
    assert exec_result.output_files_count == 2
    assert exec_result.input_files_count != exec_result.output_files_count
    assert exec_result.actual_bytes_freed == 1_024
    assert exec_result.snapshots_expired is None
    assert exec_result.manifests_rewritten is None


def test_expire_maps_snapshots_expired() -> None:
    stmt = RenderedStatement(
        action=OperationType.EXPIRE_SNAPSHOTS,
        sql="NATIVE:expire_snapshots:demo.orders",
        engine=EngineType.NATIVE,
        payload={},
    )
    result = NativeExecuteResult(ok=True, table_ref="demo.orders", results=(_expire_result(),))
    exec_result = _exec_result_from_native(stmt, result, duration_ms=40)
    assert exec_result.snapshots_expired == 3
    assert exec_result.manifests_rewritten is None


def test_batch_deep_run_maps_per_operation_audit_fields() -> None:
    plan = MaintenancePlan(
        table_ref="rest.demo.orders",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),
            Operation(OperationType.REMOVE_ORPHAN_FILES, {"orphan_min_age_hours": 72}),
            Operation(OperationType.OPTIMIZE, {"target_file_size_bytes": 256 * 1024 * 1024}),
        ),
    )
    native_result = NativeExecuteResult(
        ok=True,
        table_ref="demo.orders",
        results=(_expire_result(), _orphan_result(), _optimize_result()),
    )
    exec_results = _exec_results_from_native_batch(plan.operations, native_result, duration_ms=1000)

    assert len(exec_results) == 3
    expire, orphan, optimize = exec_results
    assert expire.snapshots_expired == 3
    assert orphan.input_files_count == 5
    assert orphan.output_files_count == 0
    assert orphan.actual_bytes_freed == 50_000
    assert optimize.input_files_count == 12
    assert optimize.output_files_count == 2
