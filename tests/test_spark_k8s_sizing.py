"""Table-size-aware executor sizing for the shared Spark-on-K8s destination.

Regression, 2026-08-03: a destination's configured executor_instances/cpu/mem was one
static profile applied to every job regardless of table size -- a table with a dozen files
reserved the same 76 vCPU / 160 GiB as a concurrently-running 24TB table. Grafana showed 86%
of the cryo namespace's cost as "unused" (reserved, not utilized) for exactly this reason.
"""

from __future__ import annotations

from cryo.engines.spark.sizing import (
    DISPATCH_DEADLINE_SECONDS,
    DISPATCH_EXECUTOR_INSTANCES,
    ExecutorProfile,
    cap_deadline_seconds_for_table,
    cap_dispatch_profile,
    scale_executor_profile,
    spark_slot_ceiling,
)

_MAX = {
    "max_executor_instances": 24,
    "max_executor_cpu_milli": 3000,
    "max_executor_mem_mib": 6144,
    "max_driver_cpu_milli": 4000,
    "max_driver_mem_mib": 16384,
}


def test_tiny_table_gets_a_small_fraction_of_the_configured_profile() -> None:
    profile = scale_executor_profile(total_size_gb=0.2, **_MAX)
    assert profile.executor_instances < 24
    assert profile.executor_cpu_milli < 3000
    assert profile.executor_mem_mib < 6144
    # Driver floors higher than executors -- coordination cost doesn't shrink as fast as
    # compute does.
    assert profile.driver_cpu_milli >= 1000
    assert profile.driver_mem_mib >= 4096


def test_huge_table_gets_the_full_configured_profile_unchanged() -> None:
    profile = scale_executor_profile(total_size_gb=24_000.0, **_MAX)
    assert profile == ExecutorProfile(
        executor_instances=24,
        executor_cpu_milli=3000,
        executor_mem_mib=6144,
        driver_cpu_milli=4000,
        driver_mem_mib=16384,
    )


def test_unknown_size_uses_the_sub_50gb_tier_not_the_full_profile() -> None:
    """An unscanned table must not inherit a 24-executor destination. That reservation
    is what filled the untagged cost bucket."""
    profile = scale_executor_profile(total_size_gb=None, **_MAX)
    assert profile.executor_instances == round(24 * 0.15)
    assert profile.executor_instances < 24


def test_tiers_are_monotonically_non_decreasing_with_size() -> None:
    sizes = [0.1, 0.9, 5.0, 100.0, 1000.0, 10_000.0]
    profiles = [scale_executor_profile(total_size_gb=s, **_MAX) for s in sizes]
    for smaller, larger in zip(profiles, profiles[1:], strict=False):
        assert larger.executor_instances >= smaller.executor_instances
        assert larger.executor_cpu_milli >= smaller.executor_cpu_milli
        assert larger.executor_mem_mib >= smaller.executor_mem_mib


def test_scaled_values_never_go_below_a_usable_floor() -> None:
    """A tiny configured profile scaled down further must still be schedulable -- not 0
    executors or a driver with no memory."""
    profile = scale_executor_profile(
        total_size_gb=0.01,
        max_executor_instances=2,
        max_executor_cpu_milli=1000,
        max_executor_mem_mib=2048,
        max_driver_cpu_milli=1000,
        max_driver_mem_mib=2048,
    )
    assert profile.executor_instances >= 1
    assert profile.executor_cpu_milli >= 500
    assert profile.executor_mem_mib >= 1024
    assert profile.driver_cpu_milli >= 500
    assert profile.driver_mem_mib >= 1024


def test_dispatch_cap_refuses_the_live_24_executor_destination() -> None:
    profile = cap_dispatch_profile(
        ExecutorProfile(
            executor_instances=24,
            executor_cpu_milli=3000,
            executor_mem_mib=6144,
            driver_cpu_milli=4000,
            driver_mem_mib=16384,
        )
    )
    assert profile == ExecutorProfile(
        executor_instances=2,
        executor_cpu_milli=1000,
        executor_mem_mib=2048,
        driver_cpu_milli=1000,
        driver_mem_mib=2048,
    )


def test_expire_and_orphan_dispatch_as_one_small_executor() -> None:
    huge = ExecutorProfile(24, 3000, 6144, 4000, 16384)
    for operation in ("expire_snapshots", "remove_orphan_files", "optimize_manifests"):
        profile = cap_dispatch_profile(huge, operation=operation)
        assert profile.executor_instances == 1
        assert profile.executor_mem_mib == 1024


def test_expire_and_orphan_keep_one_executor_and_size_the_driver_from_files() -> None:
    """Executors stay at 1. The 2026-10-19 expire kill was the driver, not the data plane.

    A 12-file table must not inherit a 24-executor destination. A 179k-file expire must
    not start on the same 1024MiB heap that the cgroup killed. Delete files count: the
    scanner's total_files is data files only. Unknown file count stays small.
    """
    huge = ExecutorProfile(24, 3000, 6144, 4000, 16384)
    for operation in ("expire_snapshots", "remove_orphan_files"):
        small = cap_dispatch_profile(huge, operation=operation, total_files=12)
        assert small.executor_instances == 1
        assert small.executor_mem_mib == 1024
        assert small.driver_mem_mib == 1024
        large = cap_dispatch_profile(huge, operation=operation, total_files=179_020)
        assert large.executor_instances == 1
        assert large.executor_mem_mib == 1024
        assert large.driver_mem_mib == 3072
        unknown = cap_dispatch_profile(huge, operation=operation, total_files=None)
        assert unknown.driver_mem_mib == 1024
    manifests = cap_dispatch_profile(huge, operation="optimize_manifests", total_files=179_020)
    assert manifests.executor_instances == 1
    assert manifests.driver_mem_mib == 1024


def test_oom_step_adds_a_fixed_heap_and_stops_at_the_ceiling() -> None:
    from cryo.engines.spark.sizing import lightweight_driver_heap_mib

    assert lightweight_driver_heap_mib(total_files=179_020, oom_steps=0) == 3072
    assert lightweight_driver_heap_mib(total_files=179_020, oom_steps=1) == 4096
    # A second kill is not a third pod. 16GiB lane headroom is a different runtime.
    assert lightweight_driver_heap_mib(total_files=179_020, oom_steps=2) == 4096
    assert lightweight_driver_heap_mib(total_files=12, oom_steps=1) == 3072
    assert lightweight_driver_heap_mib(total_files=250_000, oom_steps=0) == 4096


def test_slot_ceiling_is_a_no_op_without_the_env(monkeypatch) -> None:
    monkeypatch.delenv("CRYO_SPARK_K8S_SLOT_CAP", raising=False)
    assert spark_slot_ceiling(12) == 12


def test_slot_ceiling_caps_a_destination_slot_cap(monkeypatch) -> None:
    monkeypatch.setenv("CRYO_SPARK_K8S_SLOT_CAP", "2")
    assert spark_slot_ceiling(12) == 2
    assert spark_slot_ceiling(1) == 1


def test_deadline_small_table_stays_at_the_base_cap() -> None:
    # Confirmed live, 2026-10-07: 50 tables dispatched on spark-k8s-fallback, 22 timed out,
    # every one of the 10 largest was 500GB-2TB -- the flat 7200s deadline applied to every
    # job regardless of size, same blind spot CPU/memory scaling was built to fix. Small
    # tables keep the original cap unchanged; this is purely additive for large ones.
    assert cap_deadline_seconds_for_table(14_400, total_size_gb=0.5) == DISPATCH_DEADLINE_SECONDS
    assert cap_deadline_seconds_for_table(14_400, total_size_gb=299.9) == DISPATCH_DEADLINE_SECONDS


def test_deadline_mid_size_table_gets_three_hours() -> None:
    assert cap_deadline_seconds_for_table(14_400, total_size_gb=300.0) == 10_800
    assert cap_deadline_seconds_for_table(14_400, total_size_gb=999.9) == 10_800


def test_deadline_large_table_gets_the_full_four_hour_ceiling() -> None:
    # 14400s matches this destination's own pre-cost-fix deadline (sizing.py's own comment
    # above DISPATCH_DEADLINE_SECONDS) -- a real, previously-run value, not a new invented one.
    assert cap_deadline_seconds_for_table(99_999, total_size_gb=1_000.0) == 14_400
    assert cap_deadline_seconds_for_table(99_999, total_size_gb=2_700.0) == 14_400


def test_deadline_still_caps_a_lower_configured_value() -> None:
    # The destination's own config can only ever shrink the window, same as
    # cap_dispatch_profile's min() behavior for CPU/memory -- a destination configured for
    # 1800s on a 2TB table should not get bumped up to 14400s.
    assert cap_deadline_seconds_for_table(1_800, total_size_gb=2_700.0) == 1_800


def test_deadline_unknown_size_falls_back_to_the_flat_cap() -> None:
    assert cap_deadline_seconds_for_table(14_400, total_size_gb=None) == DISPATCH_DEADLINE_SECONDS


# Confirmed live, 2026-10-13: a 144.5GB table with 4,239 small files and 84 partition
# predicates took 1h51m to optimize against the flat DISPATCH_EXECUTOR_* ceiling --
# 2 executors x 1 vCPU/2GiB each, identical to what a 1GB job gets. cap_dispatch_profile's
# total_size_gb parameter scales that CEILING itself (not just the fraction of it a job
# gets, which scale_executor_profile already did), so a genuinely large table can actually
# use more of the destination's configured capacity instead of serializing through the
# same 2 slots as everything else on the destination.
_BIG_PROFILE = ExecutorProfile(
    executor_instances=20,
    executor_cpu_milli=3000,
    executor_mem_mib=6144,
    driver_cpu_milli=2000,
    driver_mem_mib=4096,
)


def test_dispatch_ceiling_small_table_matches_the_original_flat_cap() -> None:
    # Omitting total_size_gb (every pre-existing caller) must be bit-for-bit identical to
    # before this change -- no regression for callers that don't know a table's size yet.
    with_size = cap_dispatch_profile(_BIG_PROFILE, total_size_gb=0.5)
    without_size = cap_dispatch_profile(_BIG_PROFILE)
    assert with_size == without_size
    assert with_size.executor_instances == DISPATCH_EXECUTOR_INSTANCES


def test_dispatch_ceiling_raises_for_a_mid_size_table() -> None:
    profile = cap_dispatch_profile(_BIG_PROFILE, total_size_gb=144.5)
    assert profile.executor_instances == 6
    assert profile.executor_instances > DISPATCH_EXECUTOR_INSTANCES


def test_dispatch_ceiling_raises_further_for_a_large_table() -> None:
    profile = cap_dispatch_profile(_BIG_PROFILE, total_size_gb=800.0)
    assert profile.executor_instances == 12


def test_dispatch_ceiling_full_ceiling_for_a_huge_table() -> None:
    profile = cap_dispatch_profile(_BIG_PROFILE, total_size_gb=6_000.0)
    assert profile.executor_instances == 20


def test_dispatch_ceiling_still_caps_a_lower_configured_value() -> None:
    # The destination's own config (profile, here) can only ever shrink the result, same
    # min() behavior as every other cap in this module -- a destination configured for 1
    # executor on a 6TB table must not get bumped up to 20.
    small_profile = ExecutorProfile(
        executor_instances=1,
        executor_cpu_milli=500,
        executor_mem_mib=1024,
        driver_cpu_milli=500,
        driver_mem_mib=1024,
    )
    profile = cap_dispatch_profile(small_profile, total_size_gb=6_000.0)
    assert profile.executor_instances == 1


def test_dispatch_ceiling_lightweight_ops_ignore_table_size() -> None:
    # expire_snapshots/orphan-removal/manifest-rewrite/stats don't rewrite data files, so
    # table size shouldn't change their footprint -- the lightweight-op short-circuit must
    # still fire before total_size_gb is ever consulted.
    profile = cap_dispatch_profile(
        _BIG_PROFILE, operation="expire_snapshots", total_size_gb=6_000.0
    )
    assert profile.executor_instances == 1
    assert profile.executor_mem_mib == 1024
