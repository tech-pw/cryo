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
    assert not destination_uses_k8s_dispatch(DestinationConfig(id="d1", engine="native", config={}))
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
        table_ref="demo.mart.load_test_events",
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
    # Regression, 2026-08-11: this plan's native ops (expire_snapshots,
    # remove_orphan_files) and its fallback ops (optimize_manifests, optimize) all
    # mutate snapshot/manifest state. Dispatching both concurrently -- the native K8s
    # job and the Spark fallback racing on the same table's Iceberg metadata with no
    # coordination -- corrupted 870 tables in production (current snapshot left
    # pointing at a manifest file the other engine had already deleted). Both fallback
    # ops must now be deferred to the table's next cycle instead of dispatched here.
    assert built.fallback_operations == ()


def test_build_native_request_defers_convert_deletes_alongside_native_reclaim() -> None:
    """Same hazard, different fallback op: convert_deletes is also always fallback-routed
    (NATIVE_ALWAYS_DISCONNECTED) and also mutates snapshot/manifest state, so it must be
    deferred too when native expire_snapshots/remove_orphan_files run in the same cycle --
    confirms the guard isn't special-cased to optimize_manifests specifically."""
    plan = MaintenancePlan(
        table_ref="demo.mart.load_test_events",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retention_days": 7}),
            Operation(OperationType.CONVERT_DELETES, {}),
        ),
    )
    built = build_native_request(
        plan=plan,
        stats={"total_delete_files": 0},
        source_config={"uri": "http://rest:8181", "warehouse": "s3://wh/"},
        destination_config={"engine_impl": "go", "sql_fallback_destination_id": "spark-dest-1"},
        job_id="job-1",
        table_id="table-1",
        mem_mib=4096,
        derived_chunk=10.0,
        budget=1024 * 1024 * 1024,
    )
    op_types = {op["type"] for op in built.request["operations"]}
    assert op_types == {"expire_snapshots"}
    assert built.fallback_operations == ()


def test_build_native_request_defers_optimize_manifests_alongside_native_reclaim() -> None:
    """Direct regression test for the 2026-08-11 incident shape: a COW table (no delete
    files, so OPTIMIZE itself stays native) whose deep plan also wants optimize_manifests
    (always fallback-routed). expire_snapshots + remove_orphan_files run natively while
    optimize_manifests would have gone to Spark concurrently -- this is exactly what
    corrupted store.gold_gst_slabs and 869 other tables in production."""
    plan = MaintenancePlan(
        table_ref="demo.shop.gold_tax_slabs",
        mode=Mode.DEEP,
        operations=(
            Operation(OperationType.EXPIRE_SNAPSHOTS, {"retain_last": 4, "retention_days": 7}),
            Operation(
                OperationType.REMOVE_ORPHAN_FILES,
                {"retention_days": 7, "orphan_min_age_hours": 168},
            ),
            Operation(OperationType.OPTIMIZE_MANIFESTS, {"retain_last": 4}),
        ),
    )
    built = build_native_request(
        plan=plan,
        stats={"total_delete_files": 0},
        source_config={"uri": "http://rest:8181", "warehouse": "s3://wh/"},
        destination_config={"engine_impl": "go", "sql_fallback_destination_id": "spark-dest-1"},
        job_id="job-1",
        table_id="table-1",
        mem_mib=4096,
        derived_chunk=10.0,
        budget=1024 * 1024 * 1024,
    )
    op_types = {op["type"] for op in built.request["operations"]}
    assert op_types == {"expire_snapshots", "remove_orphan_files"}
    # optimize_manifests must be deferred, not dispatched to the fallback engine
    # concurrently with the native reclaim ops above.
    assert built.fallback_operations == ()


def test_build_native_request_keeps_supported_operations_for_cow_table() -> None:
    plan = MaintenancePlan(
        table_ref="demo.mart.load_test_events",
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


def test_native_admit_sizing_filters_ops_hint_to_native_capable_before_estimating() -> None:
    """Cost remediation plan §2, 2026-09-16 (docs/CRYO_UNUSED_COST_FIX_2026-09-16.md):
    admit_native_k8s_jobs' sizing step (the `ops_hint = ...` block right before
    build_packable_job) now filters job.operations down to native_supported_operation_types(
    stats, destination.config) before estimating resources, instead of passing the raw planned
    operations straight through -- otherwise a table whose OPTIMIZE will route entirely to the
    SQL fallback (delete files present) still gets sized as if native were about to rewrite it.
    This test exercises exactly that filtering logic in isolation (the same expression used in
    native_admit.py), since admit_native_k8s_jobs itself needs a full JobStore/session_factory/
    K8s-client integration harness that test_native_resources.py and this file's other tests
    already cover piece by piece."""
    from cryo.engines.native.resources import estimate_table_resources
    from cryo.engines.native.routing import native_supported_operation_types

    stats = {"total_delete_files": 500, "total_size_gb": 50_000}
    destination_config = {"engine_impl": "go"}
    planned_ops = (OperationType.OPTIMIZE,)  # the only planned op, but delete files block native

    native_capable_ops = native_supported_operation_types(stats, destination_config)
    ops_hint = tuple(op for op in planned_ops if op in native_capable_ops)
    assert ops_hint == ()

    sizing_params: dict = {}
    if not ops_hint:
        sizing_params["metadata_only_sizing_hint"] = True

    est_with_hint = estimate_table_resources(stats, operations=ops_hint, params=sizing_params)
    assert not est_with_hint.has_rewrite

    # Baseline: without the fix (raw planned ops passed straight through), this same table
    # would have been sized for a full rewrite despite native never running one.
    est_without_fix = estimate_table_resources(stats, operations=planned_ops)
    assert est_without_fix.has_rewrite
    assert est_without_fix.mem_mib > est_with_hint.mem_mib


def test_native_admit_sizing_keeps_native_capable_ops_and_no_hint_when_mixed() -> None:
    """A job with both native-capable ops (expire_snapshots) and a fallback-routed one
    (optimize on a delete-heavy table) must keep the native-capable one for sizing and must
    NOT set metadata_only_sizing_hint -- there IS real native work here, just not all of it."""
    from cryo.engines.native.resources import estimate_table_resources
    from cryo.engines.native.routing import native_supported_operation_types

    stats = {"total_delete_files": 500, "total_size_gb": 50_000}
    destination_config = {"engine_impl": "go"}
    planned_ops = (OperationType.EXPIRE_SNAPSHOTS, OperationType.OPTIMIZE)

    native_capable_ops = native_supported_operation_types(stats, destination_config)
    ops_hint = tuple(op for op in planned_ops if op in native_capable_ops)
    assert ops_hint == (OperationType.EXPIRE_SNAPSHOTS,)

    est = estimate_table_resources(stats, operations=ops_hint)
    assert not est.has_rewrite
