"""Tests for native Job resource estimator (≤16Gi hard cap) and chunk-from-pod."""

from __future__ import annotations

from cryo.domain.enums import OperationType
from cryo.engines.native.resources import (
    MAX_CHUNK_GB,
    MAX_POD_CPU_MILLI,
    MAX_POD_MEM_MIB,
    MIN_CHUNK_GB,
    ResourceCeilings,
    chunk_size_gb_for_pod_mem,
    compaction_memory_budget_bytes,
    compute_job_resources,
    derive_chunk_params,
    estimate_table_resources,
)


def test_expire_only_is_light() -> None:
    est = estimate_table_resources(
        {"total_size_gb": 50, "total_snapshots": 100, "manifest_count": 20},
        operations=(OperationType.EXPIRE_SNAPSHOTS,),
    )
    assert est.mem_mib >= 256
    assert est.mem_mib < 2048
    assert not est.hot
    assert not est.has_rewrite


def test_orphan_and_expire_peak_not_sum() -> None:
    est = estimate_table_resources(
        {
            "total_size_gb": 200,
            "total_files": 50_000,
            "total_snapshots": 500,
            "manifest_count": 100,
        },
        operations=(OperationType.EXPIRE_SNAPSHOTS, OperationType.REMOVE_ORPHAN_FILES),
    )
    assert est.mem_mib <= MAX_POD_MEM_MIB
    assert "orphan_files" in " ".join(est.reasons) or any(
        "orphan" in r for r in est.reasons
    )


def test_rewrite_scales_with_table_size_not_preset_chunk() -> None:
    small = estimate_table_resources(
        {"total_size_gb": 20, "total_files": 100, "max_files_per_partition": 10},
        operations=(OperationType.OPTIMIZE,),
    )
    large = estimate_table_resources(
        {
            "total_size_gb": 2000,
            "total_files": 200_000,
            "max_files_per_partition": 5_000,
            "delete_file_pct": 10,
        },
        operations=(OperationType.OPTIMIZE,),
    )
    assert large.mem_mib > small.mem_mib
    assert large.cpu_milli >= small.cpu_milli
    assert large.has_rewrite
    assert large.mem_mib >= 4096


def test_pathological_table_never_exceeds_16gi() -> None:
    est = estimate_table_resources(
        {
            "total_size_gb": 50_000,
            "total_files": 10_000_000,
            "max_files_per_partition": 2_000_000,
            "delete_file_pct": 80,
            "total_delete_files": 5_000_000,
            "total_snapshots": 100_000,
            "manifest_count": 500_000,
        },
        operations=(
            OperationType.EXPIRE_SNAPSHOTS,
            OperationType.REMOVE_ORPHAN_FILES,
            OperationType.OPTIMIZE,
        ),
        params={"group_concurrency": 8},
    )
    assert est.mem_mib <= MAX_POD_MEM_MIB
    assert est.cpu_milli <= MAX_POD_CPU_MILLI
    assert est.hot


def test_config_above_16gi_is_clamped() -> None:
    ceilings = ResourceCeilings.from_config({"maxMemory": "32Gi", "maxCpu": "8"})
    assert ceilings.max_mem_mib == MAX_POD_MEM_MIB
    assert ceilings.max_cpu_milli == MAX_POD_CPU_MILLI
    est = estimate_table_resources(
        {"total_size_gb": 10_000, "total_files": 1_000_000},
        operations=(OperationType.OPTIMIZE,),
        ceilings=ceilings,
    )
    assert est.mem_mib <= 16384


def test_config_can_lower_ceiling() -> None:
    ceilings = ResourceCeilings.from_config({"max_memory_mib": 4096, "max_cpu_milli": 1000})
    est = estimate_table_resources(
        {"total_size_gb": 10_000, "total_files": 1_000_000},
        operations=(OperationType.OPTIMIZE,),
        ceilings=ceilings,
    )
    assert est.mem_mib <= 4096
    assert est.cpu_milli <= 1000


def test_config_cannot_lower_below_runtime_minimum() -> None:
    ceilings = ResourceCeilings.from_config({"max_memory_mib": 128, "max_cpu_milli": 100})
    assert ceilings.max_mem_mib == 512
    assert ceilings.max_cpu_milli == 250


def test_hot_by_size() -> None:
    est = estimate_table_resources(
        {"total_size_gb": 600, "total_files": 1000, "max_files_per_partition": 100},
        operations=(OperationType.OPTIMIZE,),
    )
    assert est.hot
    assert any("size_gb" in r for r in est.reasons)


def test_legacy_compute_job_resources_wrapper() -> None:
    cpu, mem = compute_job_resources(100.0)
    assert 250 <= cpu <= MAX_POD_CPU_MILLI
    assert 512 <= mem <= MAX_POD_MEM_MIB


def test_compaction_budget_under_request() -> None:
    budget = compaction_memory_budget_bytes(4096)
    assert budget == int(4096 * 0.75) * 1024 * 1024
    assert compaction_memory_budget_bytes(MAX_POD_MEM_MIB + 1000) <= MAX_POD_MEM_MIB * 1024 * 1024


def test_ops_from_plan_dict() -> None:
    est = estimate_table_resources(
        {"total_size_gb": 10},
        plan={"operations": [{"type": "expire_snapshots", "params": {}}]},
    )
    assert not est.has_rewrite


def test_chunk_size_gb_for_pod_mem_bounds() -> None:
    small = chunk_size_gb_for_pod_mem(512)
    assert small >= MIN_CHUNK_GB
    assert small <= MAX_CHUNK_GB
    large = chunk_size_gb_for_pod_mem(MAX_POD_MEM_MIB)
    assert large > small
    assert large <= MAX_CHUNK_GB
    assert large >= 100.0


def test_chunk_size_scales_with_pod_mem() -> None:
    light = chunk_size_gb_for_pod_mem(1024)
    heavy = chunk_size_gb_for_pod_mem(8192)
    assert heavy > light
    assert light >= MIN_CHUNK_GB
    assert heavy <= MAX_CHUNK_GB


def test_derive_chunk_params_injects_key() -> None:
    params = derive_chunk_params(4096, {"total_size_gb": 50}, {"retention_days": 7})
    assert params["retention_days"] == 7
    assert params["chunk_size_gb"] >= MIN_CHUNK_GB
    assert params["max_bytes_per_group"] >= 256 * 1024 * 1024
    # derive may bump mem for packing floor above the caller hint
    assert params["compaction_memory_budget_bytes"] >= compaction_memory_budget_bytes(4096)


def test_max_bytes_per_group_floors_at_target_file_size() -> None:
    from cryo.engines.native.resources import max_bytes_per_group_for_pod_mem

    tiny = max_bytes_per_group_for_pod_mem(512, {"file_size_mb": 256})
    assert tiny >= 256 * 1024 * 1024


def test_max_bytes_per_group_scales_with_pod_mem() -> None:
    from cryo.engines.native.resources import max_bytes_per_group_for_pod_mem

    light = max_bytes_per_group_for_pod_mem(1024, {"file_size_mb": 256})
    heavy = max_bytes_per_group_for_pod_mem(MAX_POD_MEM_MIB, {"file_size_mb": 256})
    assert heavy > light
    assert light >= 256 * 1024 * 1024
    assert heavy <= 8 * 1024 * 1024 * 1024


def test_max_bytes_never_below_custom_target() -> None:
    from cryo.engines.native.resources import max_bytes_per_group_for_pod_mem

    target = 512 * 1024 * 1024
    got = max_bytes_per_group_for_pod_mem(512, {"target_file_size_bytes": target})
    assert got >= target


def test_max_bytes_capped_below_old_slack_but_keeps_packing_floor() -> None:
    """Regression: no 2 GiB slack groups; still ≥2× target after packing bump."""
    from cryo.engines.native.resources import (
        mem_mib_for_group_packing,
        max_bytes_per_group_for_pod_mem,
    )

    target = 256 * 1024 * 1024
    # derive/estimate bump small rewrite pods so packing floor fits in budget.
    sized = mem_mib_for_group_packing(682, {"file_size_mb": 256})
    assert sized > 682
    got = max_bytes_per_group_for_pod_mem(sized, {"file_size_mb": 256})
    assert got >= 2 * target  # packing floor: amortize remainder files
    assert got < 2 * 1024 * 1024 * 1024  # must not restore slack=4× 2 GiB groups


def test_derive_keeps_packing_floor_for_small_rewrite_pods() -> None:
    target = 256 * 1024 * 1024
    params = derive_chunk_params(682, {"total_size_gb": 4.5}, {"file_size_mb": 256})
    assert params["max_bytes_per_group"] >= 2 * target
    assert params["max_bytes_per_group"] < 2 * 1024 * 1024 * 1024
    assert params["compaction_memory_budget_bytes"] >= 2 * target * 8


def test_estimate_rewrite_bumps_mem_for_packing() -> None:
    est = estimate_table_resources(
        {"total_size_gb": 4.5, "total_files": 84},
        operations=(OperationType.OPTIMIZE,),
        params={"file_size_mb": 256},
    )
    # Packing floor 512 MiB × expansion 8 → budget 4 GiB → mem ≥ ~5462 MiB
    assert est.mem_mib >= 5000


def test_derive_preserves_explicit_max_bytes_override() -> None:
    override = 32 * 1024 * 1024
    params = derive_chunk_params(
        8192,
        {"total_size_gb": 100},
        {"max_bytes_per_group": override, "file_size_mb": 256},
    )
    assert params["max_bytes_per_group"] == override
    assert params["chunk_size_gb"] >= MIN_CHUNK_GB