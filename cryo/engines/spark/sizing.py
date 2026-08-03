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
    # Unknown size (e.g. a table Cryo has never scanned) -- better to over-provision once
    # than silently starve a table that turns out to be genuinely large.
    if total_size_gb is None:
        return _FULL_FRACTION
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
