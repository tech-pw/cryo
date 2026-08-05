"""Table-size-aware executor sizing for the shared Spark-on-K8s destination.

Regression, 2026-08-03: a destination's configured executor_instances/cpu/mem was one
static profile applied to every job regardless of table size -- a table with a dozen files
reserved the same 76 vCPU / 160 GiB as a concurrently-running 24TB table. Grafana showed 86%
of the cryo namespace's cost as "unused" (reserved, not utilized) for exactly this reason.
"""

from __future__ import annotations

from cryo.engines.spark.sizing import ExecutorProfile, scale_executor_profile

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


def test_unknown_size_gets_the_full_configured_profile() -> None:
    """A table Cryo has never scanned -- over-provision once rather than silently starve a
    table that turns out to be genuinely large."""
    profile = scale_executor_profile(total_size_gb=None, **_MAX)
    assert profile.executor_instances == 24


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
