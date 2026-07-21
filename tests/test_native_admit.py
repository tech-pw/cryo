"""Unit tests for Governor native pack-on-admit helpers."""

from __future__ import annotations

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan, Operation
from cryo.engines.native.resources import MAX_POD_MEM_MIB
from cryo.governor.native_admit import (
    build_native_request,
    cluster_headroom,
    destination_uses_k8s_dispatch,
)
from cryo.worker.worker import DestinationConfig


def test_destination_uses_k8s_dispatch() -> None:
    assert not destination_uses_k8s_dispatch(
        DestinationConfig(id="d1", engine="native", config={})
    )
    assert destination_uses_k8s_dispatch(
        DestinationConfig(id="d1", engine="native", config={"k8s_dispatch": True})
    )
    assert not destination_uses_k8s_dispatch(
        DestinationConfig(id="d1", engine="trino", config={"k8s_dispatch": True})
    )


def test_cluster_headroom() -> None:
    live = {
        "default": CapacitySnapshot(
            capacity=5,
            used=2,
            cpu_used_milli=2000,
            mem_used_mib=4096,
        )
    }
    slots, cpu, mem = cluster_headroom(
        live,
        config={
            "slot_cap": 5,
            "max_cluster_cpu_milli": 20000,
            "max_cluster_mem_mib": 5 * MAX_POD_MEM_MIB,
        },
    )
    assert slots == 3
    assert cpu == 18000
    assert mem == 5 * MAX_POD_MEM_MIB - 4096


def test_build_native_request_drops_unsupported_operations() -> None:
    """Regression: a deep-preset plan on a MOR table dispatched optimize_manifests
    straight to the Go engine and failed the whole batch in production (2026-07-21)
    because create-on-admit never filtered the plan through native routing, unlike
    the Worker legacy path."""
    plan = MaintenancePlan(
        table_ref="cdp.mview.load_test_basefact",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),
            Operation(OperationType.REMOVE_ORPHAN_FILES, {"orphan_min_age_hours": 168}),
            Operation(OperationType.OPTIMIZE, {"file_size_mb": 256}),
            Operation(OperationType.OPTIMIZE_MANIFESTS, {}),
        ),
    )
    stats = {"total_delete_files": 12710, "delete_file_pct": 93.22}
    source_config = {"uri": "http://rest:8181", "warehouse": "s3://wh/"}
    # With a fallback destination configured, routing classifies unsupported ops as
    # fallback_operations (dispatch to it) rather than skipped_operations (drop).
    destination_config = {"engine_impl": "go", "sql_fallback_destination_id": "spark-dest-1"}

    built = build_native_request(
        plan=plan,
        stats=stats,
        source_config=source_config,
        destination_config=destination_config,
        job_id="job-1",
        table_id="table-1",
        mem_mib=16384,
        derived_chunk=10.0,
        budget=8 * 1024 * 1024 * 1024,
    )

    op_types = {op["type"] for op in built.request["operations"]}
    assert "optimize_manifests" not in op_types
    # delete files present -> OPTIMIZE can't run on the Go engine either; only the
    # base ops (always native-supported) should remain in the native Go batch.
    assert op_types == {"expire_snapshots", "remove_orphan_files"}
    # Both optimize_manifests and OPTIMIZE itself are surfaced for fallback dispatch --
    # OPTIMIZE-with-delete-files routes to Spark/Trino when a fallback is configured
    # (proven live against a real delete-heavy table, 2026-07-21), it's no longer
    # dropped outright the way it was before cryo.engines.spark.k8s existed.
    fallback_types = {op.type.value for op, _ in built.fallback_operations}
    assert fallback_types == {"optimize_manifests", "optimize"}


def test_build_native_request_keeps_supported_operations_for_cow_table() -> None:
    plan = MaintenancePlan(
        table_ref="cdp.mview.load_test_basefact",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),
            Operation(OperationType.OPTIMIZE, {"file_size_mb": 256}),
        ),
    )
    built = build_native_request(
        plan=plan,
        stats={"total_delete_files": 0},
        source_config={"uri": "http://rest:8181", "warehouse": "s3://wh/"},
        destination_config={"engine_impl": "go"},
        job_id="job-1",
        table_id="table-1",
        mem_mib=4096,
        derived_chunk=10.0,
        budget=1024 * 1024 * 1024,
    )
    op_types = {op["type"] for op in built.request["operations"]}
    assert op_types == {"expire_snapshots", "rewrite_data_files"}
    assert built.fallback_operations == ()
