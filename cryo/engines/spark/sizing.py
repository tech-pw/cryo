"""Table-size-aware executor sizing for the shared Spark-on-K8s destination.

Confirmed live, 2026-08-03: a single destination's config (executor_instances,
executor_cpu_milli, executor_mem_mib, driver_cpu_milli, driver_mem_mib) is one static
profile applied to *every* job dispatched to it, regardless of the table being processed.
A 97-table bulk cleanup run -- almost all of them well under 1GB -- each reserved the same
76 vCPU / 160 GiB as a concurrently-running 24TB table, because that's what the shared
"spark-k8s-fallback" destination is configured for. Grafana's namespace cost dashboard
showed 86% of the cryo namespace's cost as "unused" (reserved capacity, not utilized) for
exactly this reason: a job touching a dozen small files finishes in minutes but reserved a
cluster sized for a 24TB rewrite for its entire (short) lifetime.

This scales a destination's *configured* profile down as a fraction based on the table's
total_size_gb, so the destination's own config still acts as the ceiling (for tables that
actually need that scale) while small tables reserve a proportionally small slice of it --
no separate destination or config duplication needed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# (size_gb_upper_bound, fraction_of_configured_max) -- table size is compared against each
# upper_bound in order; the first tier it's strictly under wins. A table at or above the
# largest bound gets the full configured profile (today's behavior, unchanged for the
# tables that actually need it).
_SIZE_TIERS: tuple[tuple[float, float], ...] = (
    (1.0, 0.05),
    (50.0, 0.15),
    (500.0, 0.35),
    (5_000.0, 0.70),
)
_FULL_FRACTION = 1.0
# Driver planning/coordination cost scales much more weakly with data size than executor
# compute does -- it's orchestrating the plan, not processing partitions -- so floor its
# fraction higher than the executor fraction rather than let a tiny table starve the driver.
_MIN_DRIVER_FRACTION = 0.25


@dataclass(frozen=True)
class ExecutorProfile:
    executor_instances: int
    executor_cpu_milli: int
    executor_mem_mib: int
    driver_cpu_milli: int
    driver_mem_mib: int


def _tier_fraction(total_size_gb: float | None) -> float:
    # Unknown size used to take the full configured profile. The live spark-k8s-fallback
    # destination was 24 executors × 3 vCPU × 6Gi, so one unscanned table reserved ~76 vCPU
    # for up to 4h and landed in the untagged cost bucket (Spark creates the driver and
    # executors with no Deployment owner). Use the sub-50GB tier; cap_dispatch_profile still
    # bounds the result if that destination config is restored.
    if total_size_gb is None:
        return 0.15
    for upper_bound, fraction in _SIZE_TIERS:
        if total_size_gb < upper_bound:
            return fraction
    return _FULL_FRACTION


def scale_executor_profile(
    *,
    total_size_gb: float | None,
    max_executor_instances: int,
    max_executor_cpu_milli: int,
    max_executor_mem_mib: int,
    max_driver_cpu_milli: int,
    max_driver_mem_mib: int,
) -> ExecutorProfile:
    fraction = _tier_fraction(total_size_gb)
    driver_fraction = max(fraction, _MIN_DRIVER_FRACTION)
    return ExecutorProfile(
        executor_instances=max(1, round(max_executor_instances * fraction)),
        executor_cpu_milli=max(500, round(max_executor_cpu_milli * fraction)),
        executor_mem_mib=max(1024, round(max_executor_mem_mib * fraction)),
        driver_cpu_milli=max(500, round(max_driver_cpu_milli * driver_fraction)),
        driver_mem_mib=max(1024, round(max_driver_mem_mib * driver_fraction)),
    )


# Hard ceiling applied at spark-submit time, independent of destination.config.
# Live spark-k8s-fallback was 24×3000m×6144Mi driver 4000m/16384Mi, deadline 14400s,
# slot_cap 12. That is one cryo-spark Job plus an untagged driver and 24 executors.
DISPATCH_EXECUTOR_INSTANCES = 2
DISPATCH_EXECUTOR_CPU_MILLI = 1000
DISPATCH_EXECUTOR_MEM_MIB = 2048
DISPATCH_DRIVER_CPU_MILLI = 1000
DISPATCH_DRIVER_MEM_MIB = 2048
DISPATCH_DEADLINE_SECONDS = 7200

_LIGHTWEIGHT_OPS = frozenset(
    {
        "expire_snapshots",
        "remove_orphan_files",
        "optimize_manifests",
        "compute_statistics",
    }
)
_LIGHTWEIGHT_PROFILE = ExecutorProfile(
    executor_instances=1,
    executor_cpu_milli=500,
    executor_mem_mib=1024,
    driver_cpu_milli=500,
    driver_mem_mib=1024,
)


def cap_dispatch_profile(
    profile: ExecutorProfile, *, operation: str | None = None
) -> ExecutorProfile:
    """Bound what a cryo-spark submit is allowed to reserve, whatever the destination says."""
    if operation in _LIGHTWEIGHT_OPS:
        return _LIGHTWEIGHT_PROFILE
    return ExecutorProfile(
        executor_instances=min(profile.executor_instances, DISPATCH_EXECUTOR_INSTANCES),
        executor_cpu_milli=min(profile.executor_cpu_milli, DISPATCH_EXECUTOR_CPU_MILLI),
        executor_mem_mib=min(profile.executor_mem_mib, DISPATCH_EXECUTOR_MEM_MIB),
        driver_cpu_milli=min(profile.driver_cpu_milli, DISPATCH_DRIVER_CPU_MILLI),
        driver_mem_mib=min(profile.driver_mem_mib, DISPATCH_DRIVER_MEM_MIB),
    )


def cap_deadline_seconds(seconds: int) -> int:
    return min(seconds, DISPATCH_DEADLINE_SECONDS)


def spark_slot_ceiling(configured: int) -> int:
    """Honor CRYO_SPARK_K8S_SLOT_CAP as a ceiling over a destination's slot_cap.

    Absent in tests and local runs, so an explicit slot_cap still wins there. Prod sets
    the env to 2 so a destination left at 12 cannot start 12 clusters at once.
    """
    raw = os.environ.get("CRYO_SPARK_K8S_SLOT_CAP")
    if not raw:
        return configured
    try:
        ceiling = int(raw)
    except ValueError:
        return configured
    if ceiling < 1:
        return configured
    return min(configured, ceiling)
