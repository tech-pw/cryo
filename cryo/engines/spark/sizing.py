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

# Confirmed live, 2026-10-13: this flat ceiling (correctly) stops a 1GB job from
# reserving a big cluster, but applies identically to a 144.5GB/4,239-small-file table --
# 2 executors x 1 vCPU/2GiB each, same as the smallest job on this destination. That
# table's optimize alone took 1h51m against 84 partition predicates; more executors would
# have let Spark actually parallelize across them instead of serializing through 2 slots.
# Reuses _SIZE_TIERS' existing size breaks (already tuned against this destination's real
# traffic) so a table's executor *ceiling* scales with the same breakpoints its executor
# *fraction* already does -- just against a bigger max for tables that have earned it,
# not a second, disconnected set of thresholds to keep in sync by hand.
_DISPATCH_CEILING_TIERS: tuple[tuple[float, int, int, int], ...] = (
    # (size_gb_upper_bound, executor_instances, executor_cpu_milli, executor_mem_mib)
    (50.0, DISPATCH_EXECUTOR_INSTANCES, DISPATCH_EXECUTOR_CPU_MILLI, DISPATCH_EXECUTOR_MEM_MIB),
    (500.0, 6, 2000, 4096),
    (5_000.0, 12, 3000, 6144),
)
_DISPATCH_CEILING_EXECUTOR_INSTANCES = 20
_DISPATCH_CEILING_EXECUTOR_CPU_MILLI = 3000
_DISPATCH_CEILING_EXECUTOR_MEM_MIB = 6144
# Driver stays flat across tiers (DISPATCH_DRIVER_* above, unchanged by any tier) -- it
# coordinates partition predicates, doesn't process them, so its own cost scales far more
# weakly with table size than executor count does (same reasoning _MIN_DRIVER_FRACTION
# already uses for scale_executor_profile above).


def _dispatch_ceiling_for_table(
    total_size_gb: float | None,
) -> tuple[int, int, int]:
    """(max_executor_instances, max_executor_cpu_milli, max_executor_mem_mib) for this size.

    Unknown size takes the smallest tier -- same fail-safe-small choice _tier_fraction and
    cap_deadline_seconds_for_table both make when size isn't known yet.
    """
    if total_size_gb is None:
        tier = _DISPATCH_CEILING_TIERS[0]
        return tier[1], tier[2], tier[3]
    for upper_bound, instances, cpu_milli, mem_mib in _DISPATCH_CEILING_TIERS:
        if total_size_gb < upper_bound:
            return instances, cpu_milli, mem_mib
    return (
        _DISPATCH_CEILING_EXECUTOR_INSTANCES,
        _DISPATCH_CEILING_EXECUTOR_CPU_MILLI,
        _DISPATCH_CEILING_EXECUTOR_MEM_MIB,
    )


# Confirmed live, 2026-10-07: a flat 7200s ceiling (below) correctly stopped small jobs
# from holding capacity forever, but applies to every job regardless of table size, same
# blind spot CPU/memory scaling above was built to fix. Of 50 tables dispatched on
# spark-k8s-fallback, 22 hit this exact deadline -- every one of the 10 largest was
# 500GB-2TB. Tiers below are deliberately coarser than _SIZE_TIERS: a table only needs
# more wall-clock time once it's genuinely large enough that compaction can't finish in
# 2h even with full executor capacity, not at every size break CPU/memory scale on.
# Ceiling (14400s / 4h) matches this destination's own pre-cost-fix deadline (comment
# above) -- a real, previously-run value, not an arbitrary new one.
_DEADLINE_SIZE_TIERS: tuple[tuple[float, int], ...] = (
    (300.0, DISPATCH_DEADLINE_SECONDS),
    (1_000.0, 10_800),
)
_DEADLINE_CEILING_SECONDS = 14_400


def cap_deadline_seconds_for_table(seconds: int, *, total_size_gb: float | None) -> int:
    """Same intent as cap_deadline_seconds, scaled by table size.

    Unknown size takes the base (smallest) tier -- same fail-safe-small choice
    _tier_fraction makes for CPU/memory when size isn't known yet.
    """
    if total_size_gb is not None:
        for upper_bound, ceiling in _DEADLINE_SIZE_TIERS:
            if total_size_gb < upper_bound:
                return min(seconds, ceiling)
        return min(seconds, _DEADLINE_CEILING_SECONDS)
    return cap_deadline_seconds(seconds)


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
    profile: ExecutorProfile,
    *,
    operation: str | None = None,
    total_size_gb: float | None = None,
) -> ExecutorProfile:
    """Bound what a cryo-spark submit is allowed to reserve, whatever the destination says.

    total_size_gb scales the executor ceiling itself (not just the fraction of it a job
    gets, which scale_executor_profile already does) -- see _dispatch_ceiling_for_table.
    Omitted entirely, this falls back to the original flat DISPATCH_EXECUTOR_* ceiling, so
    every existing caller that doesn't pass it keeps today's behavior unchanged.
    """
    if operation in _LIGHTWEIGHT_OPS:
        return _LIGHTWEIGHT_PROFILE
    max_instances, max_cpu_milli, max_mem_mib = (
        _dispatch_ceiling_for_table(total_size_gb)
        if total_size_gb is not None
        else (DISPATCH_EXECUTOR_INSTANCES, DISPATCH_EXECUTOR_CPU_MILLI, DISPATCH_EXECUTOR_MEM_MIB)
    )
    return ExecutorProfile(
        executor_instances=min(profile.executor_instances, max_instances),
        executor_cpu_milli=min(profile.executor_cpu_milli, max_cpu_milli),
        executor_mem_mib=min(profile.executor_mem_mib, max_mem_mib),
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
