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
    group_concurrency_for_table,
    max_bytes_per_group_for_pod_mem,
    mem_mib_for_group_packing,
    packing_floor_bytes,
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
    assert "orphan_files" in " ".join(est.reasons) or any("orphan" in r for r in est.reasons)


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


def test_missing_size_with_no_stats_at_all_gets_small_floor_not_100gb_assumption() -> None:
    """Live-verified 2026-09-01 regression: the old fallback assumed 100GB for ANY table
    missing total_size_gb -- confirmed the dominant driver of 589 of 699 live native pods
    hitting the 2.4 CPU/12Gi ceiling regardless of real table size (73% of jobs in an 8h
    sample were on tables under 1GB). A table with genuinely no stats row at all (stats={})
    must now size the same as a real, known 1GB table (the new floor), far below the old
    100GB-driven near-ceiling default -- not literally the smallest possible request, since
    the packing-floor logic still applies its own real minimum for a genuinely tiny table."""
    no_stats = estimate_table_resources({}, operations=(OperationType.OPTIMIZE,))
    known_1gb = estimate_table_resources(
        {"total_size_gb": 1}, operations=(OperationType.OPTIMIZE,)
    )
    known_100gb = estimate_table_resources(
        {"total_size_gb": 100}, operations=(OperationType.OPTIMIZE,)
    )
    assert no_stats.mem_mib == known_1gb.mem_mib
    assert no_stats.mem_mib < known_100gb.mem_mib


def test_missing_size_but_total_files_present_scales_with_file_count() -> None:
    """A stats row that exists but has no total_size_gb (size didn't populate this scan) --
    total_files, when present, is real per-table signal and should size larger for a table
    with many more files than one with few, using an estimate calibrated against the fleet's
    own real median/p90 per-file size (live-verified 2026-09-01: 0.44MB median, 74MB p90),
    not the old flat 100GB assumption regardless of file count."""
    few_files = estimate_table_resources(
        {"total_files": 50}, operations=(OperationType.OPTIMIZE,)
    )
    many_files = estimate_table_resources(
        {"total_files": 5_000}, operations=(OperationType.OPTIMIZE,)
    )
    assert many_files.mem_mib > few_files.mem_mib


def test_missing_size_estimate_still_respects_16gi_ceiling() -> None:
    """A missing-size table with an enormous total_files count must still clamp to the same
    hard ceiling as a known-huge table -- the fix changes the *default*, not the safety cap."""
    est = estimate_table_resources(
        {"total_files": 50_000_000}, operations=(OperationType.OPTIMIZE,)
    )
    assert est.mem_mib <= MAX_POD_MEM_MIB


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
        max_bytes_per_group_for_pod_mem,
        mem_mib_for_group_packing,
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


def test_group_concurrency_flat_default_for_no_partition_stats() -> None:
    assert group_concurrency_for_table(None) == 2
    assert group_concurrency_for_table({}) == 2
    assert group_concurrency_for_table({"total_partitions": 0}) == 2


def test_group_concurrency_scales_with_partition_count() -> None:
    # Regression case: the actual production table that surfaced this gap.
    got = group_concurrency_for_table({"total_partitions": 340}, max_cpu_milli=4000)
    assert got > 2
    assert got <= 8


def test_group_concurrency_small_table_stays_at_floor() -> None:
    got = group_concurrency_for_table({"total_partitions": 10}, max_cpu_milli=4000)
    assert got == 2


def test_group_concurrency_capped_by_cpu_ceiling() -> None:
    # Even a huge partition count should not exceed what the pod's CPU ceiling
    # can plausibly run concurrently.
    got = group_concurrency_for_table({"total_partitions": 100_000}, max_cpu_milli=2000)
    assert got == 2


def test_group_concurrency_capped_by_hard_max() -> None:
    got = group_concurrency_for_table({"total_partitions": 100_000}, max_cpu_milli=100_000)
    assert got == 8


# --- table-size-aware packing floor (2026-08-06 cost fix) -------------------------
#
# Regression coverage for capping the rewrite packing floor by the table's actual
# size: confirmed live that native dispatch pods averaged 154MiB actual RSS against
# ~6GiB requested (2.5% utilization) across the fleet, because the packing floor
# (2x target file size x 8x decompression-expansion factor, ~5.3GiB) applied
# unconditionally to every rewrite job regardless of whether the table had anywhere
# near that much data. 8,590 of 12,053 real fleet tables (71%) are under 0.1GB.


def test_packing_floor_uncapped_when_table_size_not_given() -> None:
    # Backward-compatible: omitting table_total_bytes keeps the original behavior.
    assert packing_floor_bytes({}) == 256 * 1024 * 1024 * 2


def test_packing_floor_capped_by_small_table_size() -> None:
    # A 10MiB table can never produce a group anywhere near the uncapped 512MiB
    # floor -- the floor should shrink to what the table could plausibly need,
    # but never below one target file size (the shred floor).
    ten_mib = 10 * 1024 * 1024
    got = packing_floor_bytes({}, table_total_bytes=ten_mib)
    assert got == 256 * 1024 * 1024  # clamped to the shred floor, not ten_mib


def test_packing_floor_not_reduced_below_shred_floor() -> None:
    got = packing_floor_bytes({}, table_total_bytes=1)
    assert got == 256 * 1024 * 1024


def test_packing_floor_noop_for_large_table() -> None:
    large = 100 * 1024 * 1024 * 1024  # 100GiB
    assert packing_floor_bytes({}, table_total_bytes=large) == packing_floor_bytes({})


def test_mem_mib_for_group_packing_reduced_for_tiny_table() -> None:
    tiny_bytes = int(0.05 * (1024**3))  # 50MiB
    with_cap = mem_mib_for_group_packing(512, {}, table_total_bytes=tiny_bytes)
    without_cap = mem_mib_for_group_packing(512, {})
    assert with_cap < without_cap


def test_mem_mib_for_group_packing_unchanged_for_large_table() -> None:
    large_bytes = 100 * 1024 * 1024 * 1024
    with_cap = mem_mib_for_group_packing(512, {}, table_total_bytes=large_bytes)
    without_cap = mem_mib_for_group_packing(512, {})
    assert with_cap == without_cap


def test_estimate_table_resources_tiny_table_uses_less_memory_than_before_fix() -> None:
    """The actual production case: an 8,590-table cohort (71% of the fleet) is
    under 0.1GB and was getting sized identically to a 20GB table."""
    tiny = estimate_table_resources(
        {"total_size_gb": 0.05, "total_files": 20, "max_files_per_partition": 5},
        operations=(OperationType.OPTIMIZE,),
    )
    twenty_gb = estimate_table_resources(
        {"total_size_gb": 20, "total_files": 100, "max_files_per_partition": 10},
        operations=(OperationType.OPTIMIZE,),
    )
    # Before the fix both sized to the same uncapped packing floor (~5.3GiB).
    assert tiny.mem_mib < twenty_gb.mem_mib


def test_estimate_table_resources_large_table_unaffected_by_size_cap() -> None:
    """The size-aware cap must be a no-op once a table exceeds the uncapped
    floor -- large/hot tables keep exactly their prior sizing headroom."""
    large = estimate_table_resources(
        {
            "total_size_gb": 2000,
            "total_files": 200_000,
            "max_files_per_partition": 5_000,
            "delete_file_pct": 10,
        },
        operations=(OperationType.OPTIMIZE,),
    )
    assert large.mem_mib == 16384
    assert large.hot


def test_estimate_table_resources_half_gb_boundary_matches_uncapped() -> None:
    # 0.5GB in bytes (~512MiB) is right at the default uncapped floor -- the cap
    # should be a near no-op there, not a discontinuity.
    at_boundary = estimate_table_resources(
        {"total_size_gb": 0.5, "total_files": 20, "max_files_per_partition": 5},
        operations=(OperationType.OPTIMIZE,),
    )
    uncapped_equivalent = estimate_table_resources(
        {"total_size_gb": 20, "total_files": 20, "max_files_per_partition": 5},
        operations=(OperationType.OPTIMIZE,),
    )
    assert at_boundary.mem_mib == uncapped_equivalent.mem_mib


def test_derive_chunk_params_smaller_table_gets_smaller_max_bytes_per_group() -> None:
    tiny_params = derive_chunk_params(512, {"total_size_gb": 0.05})
    large_params = derive_chunk_params(512, {"total_size_gb": 100})
    assert tiny_params["max_bytes_per_group"] <= large_params["max_bytes_per_group"]


def test_derive_chunk_params_no_stats_falls_back_to_uncapped() -> None:
    # No total_size_gb in stats (or stats=None) -- must not crash, must behave
    # exactly as the pre-fix uncapped path (safe default, no assumption of "small").
    params = derive_chunk_params(512, None)
    assert params["max_bytes_per_group"] >= 256 * 1024 * 1024


def test_max_bytes_per_group_for_pod_mem_respects_table_total_bytes() -> None:
    tiny_bytes = int(0.05 * (1024**3))
    got = max_bytes_per_group_for_pod_mem(512, {}, table_total_bytes=tiny_bytes)
    assert got == 256 * 1024 * 1024  # shred floor, not the uncapped packing floor
