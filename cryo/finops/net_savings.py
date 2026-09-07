"""Net savings: what maintenance actually returns after everything it costs.

Storage-bytes-saved is the number every lakehouse optimization vendor leads with. It is also
the number that flatters whoever quotes it, because it omits three real costs: the compute
burned doing the rewrite, the compute burned keeping the table optimized as it churns, and --
for a commercial product priced on results -- the vendor's ongoing cut of the savings.

This module nets all of that out over a horizon so an operator can compare like with like:

    net = storage_saved - rewrite_cost - ongoing_maintenance - platform_fee

Two structural points fall out of that arithmetic, and they are why this view exists:

1. A **percentage-of-savings** fee recurs for as long as the table exists, while the layout fix
   that produced the savings is mostly one-time. Over a long enough horizon the fee dominates,
   and the customer keeps a shrinking share of a fixed win.
2. Compute that runs on infrastructure the customer already owns and has already paid for is
   cheaper at the margin than compute a vendor provisions and bills for.

Neither point requires Cryo to win on bytes saved, which is the honest position: Cryo does not
ship a compression codec and should not imply that it matches one. It can be behind on gross
reduction and still ahead on what the customer keeps.

Nothing here is a benchmark or a competitive claim. It is arithmetic over inputs the caller
supplies; ``ScenarioResult.assumptions`` records every one of them so a projection can be
audited or disputed. Populate them from measured CUR data and completed-job telemetry, not from
vendor marketing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_BYTES_PER_GB = 1024**3
_DAYS_PER_MONTH = 30.0


@dataclass(frozen=True)
class MaintenanceScenario:
    """One way of getting a table optimized, costed end to end.

    ``platform_fee_fraction_of_savings`` models outcome-based pricing: the share of realized
    storage savings the vendor takes, charged for as long as the arrangement runs. Set 0.0 for
    self-hosted.

    ``rewrite_cost_per_gb`` should come from completed jobs. For a vendor running its own
    engine this is the price billed; for Cryo on the customer's Trino/Spark it is the marginal
    cost of compute already provisioned -- typically lower, and zero on a cluster that is idle
    anyway (which callers should only assume when it is actually true).
    """

    name: str
    storage_saved_fraction: float
    rewrite_cost_per_gb: float = 0.0
    platform_fee_fraction_of_savings: float = 0.0
    # Share of the table rewritten per month to hold the layout as data churns.
    monthly_maintenance_fraction: float = 0.0


@dataclass
class ScenarioResult:
    """Costed outcome for one scenario over the horizon."""

    name: str
    storage_saved_bytes: int
    gross_storage_savings_usd: float
    rewrite_cost_usd: float
    maintenance_cost_usd: float
    platform_fee_usd: float
    net_savings_usd: float
    horizon_days: int
    assumptions: dict[str, float | int | str] = field(default_factory=dict)

    @property
    def customer_keeps_fraction(self) -> float:
        """Share of gross storage savings the customer actually retains."""
        if self.gross_storage_savings_usd <= 0:
            return 0.0
        return self.net_savings_usd / self.gross_storage_savings_usd

    @property
    def payback_days(self) -> float | None:
        """Days until recurring savings repay the one-time rewrite. None if never."""
        daily_net = (
            self.gross_storage_savings_usd - self.maintenance_cost_usd - self.platform_fee_usd
        ) / max(self.horizon_days, 1)
        if daily_net <= 0:
            return None
        return round(self.rewrite_cost_usd / daily_net, 1)


@dataclass
class Comparison:
    """Scenarios ranked by what the customer keeps."""

    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def best(self) -> ScenarioResult | None:
        return max(self.results, key=lambda r: r.net_savings_usd) if self.results else None

    def ranked(self) -> list[ScenarioResult]:
        return sorted(self.results, key=lambda r: r.net_savings_usd, reverse=True)


def evaluate_scenario(
    scenario: MaintenanceScenario,
    *,
    table_size_bytes: int,
    storage_cost_per_gb_month: float = 0.023,
    horizon_days: int = 365,
) -> ScenarioResult:
    """Cost one scenario over ``horizon_days``.

    ``storage_cost_per_gb_month`` defaults to S3 Standard list price, matching
    ``compare_scenarios``; pass the measured CUR rate when available.

    The rewrite is charged once; storage savings, maintenance, and any platform fee accrue for
    the whole horizon. Horizon length is therefore not neutral between scenarios -- a recurring
    fee and a one-time cost trade places as it lengthens, which is the comparison's whole point.
    """
    size_gb = table_size_bytes / _BYTES_PER_GB
    months = horizon_days / _DAYS_PER_MONTH

    saved_fraction = max(0.0, min(1.0, scenario.storage_saved_fraction))
    saved_bytes = int(table_size_bytes * saved_fraction)
    saved_gb = saved_bytes / _BYTES_PER_GB

    gross = saved_gb * storage_cost_per_gb_month * months
    rewrite = size_gb * scenario.rewrite_cost_per_gb
    maintenance = (
        size_gb * scenario.monthly_maintenance_fraction * scenario.rewrite_cost_per_gb * months
    )
    fee = gross * max(0.0, min(1.0, scenario.platform_fee_fraction_of_savings))

    return ScenarioResult(
        name=scenario.name,
        storage_saved_bytes=saved_bytes,
        gross_storage_savings_usd=round(gross, 2),
        rewrite_cost_usd=round(rewrite, 2),
        maintenance_cost_usd=round(maintenance, 2),
        platform_fee_usd=round(fee, 2),
        net_savings_usd=round(gross - rewrite - maintenance - fee, 2),
        horizon_days=horizon_days,
        assumptions={
            "table_size_bytes": table_size_bytes,
            "storage_cost_per_gb_month": storage_cost_per_gb_month,
            "storage_saved_fraction": saved_fraction,
            "rewrite_cost_per_gb": scenario.rewrite_cost_per_gb,
            "platform_fee_fraction_of_savings": scenario.platform_fee_fraction_of_savings,
            "monthly_maintenance_fraction": scenario.monthly_maintenance_fraction,
            "horizon_days": horizon_days,
        },
    )


def compare_scenarios(
    scenarios: list[MaintenanceScenario],
    *,
    table_size_bytes: int,
    storage_cost_per_gb_month: float = 0.023,
    horizon_days: int = 365,
) -> Comparison:
    """Cost several scenarios on identical inputs and rank by net savings.

    ``storage_cost_per_gb_month`` defaults to S3 Standard list price; pass the measured CUR
    rate when available. Every scenario sees the same table and the same horizon, so the only
    thing that differs is the cost structure being compared.
    """
    return Comparison(
        results=[
            evaluate_scenario(
                scenario,
                table_size_bytes=table_size_bytes,
                storage_cost_per_gb_month=storage_cost_per_gb_month,
                horizon_days=horizon_days,
            )
            for scenario in scenarios
        ]
    )
