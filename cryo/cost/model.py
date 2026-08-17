from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from cryo.config import Settings
from cryo.domain.enums import CostPrecision, OperationType
from cryo.domain.models import SavingsProjection, StorageBreakdown

GIB = 1024**3
GB = 1_000_000_000


@dataclass(frozen=True)
class ReclaimBytes:
    reclaim_expire_bytes: int
    reclaim_orphan_bytes: int
    reclaim_optimize_bytes: int
    reclaim_manifests_bytes: int


@dataclass(frozen=True)
class CostStatsInput:
    small_files_count: int = 0
    small_file_bytes: int = 0
    target_file_size_bytes: int = 268_435_456
    query_count_30d: int = 0


def bytes_to_usd(
    bytes_value: int,
    *,
    price_per_gb_month: float,
    use_gib: bool = True,
) -> float:
    divisor = GIB if use_gib else GB
    return round((max(bytes_value, 0) / divisor) * price_per_gb_month, 4)


def current_storage_bytes(breakdown: StorageBreakdown) -> int:
    orphan = breakdown.orphan_bytes or 0
    return (
        breakdown.live_bytes
        + breakdown.dead_bytes
        + breakdown.metadata_bytes
        + breakdown.delete_bytes
        + orphan
    )


def reclaimable_bytes(reclaim: ReclaimBytes) -> int:
    return (
        reclaim.reclaim_expire_bytes
        + reclaim.reclaim_orphan_bytes
        + reclaim.reclaim_optimize_bytes
        + reclaim.reclaim_manifests_bytes
    )


def query_request_savings_usd(
    stats: CostStatsInput,
    *,
    query_gets_per_file: float,
    query_count_30d: int,
    s3_get_usd_per_1k: float,
    target_file_size_bytes: int | None = None,
) -> float:
    target = target_file_size_bytes or stats.target_file_size_bytes
    if target <= 0:
        return 0.0
    merged_files = math.ceil(stats.small_file_bytes / target) if stats.small_file_bytes else 0
    delta_files = max(0, stats.small_files_count - merged_files)
    return round(
        delta_files * query_gets_per_file * query_count_30d * s3_get_usd_per_1k / 1000,
        4,
    )


def project_savings(
    breakdown: StorageBreakdown,
    reclaim: ReclaimBytes,
    stats: CostStatsInput,
    settings: Settings,
    *,
    precision: CostPrecision = CostPrecision.METADATA,
    assumptions: dict[str, Any] | None = None,
    live_s3_get_price_per_1k: float | None = None,
) -> SavingsProjection:
    """Compute a savings projection for a table.

    The `live_s3_get_price_per_1k` parameter exists so callers with AWS credentials can pass
    the live Price-List-API result (`fetch_s3_request_prices().price_per_1k_get`) instead of
    the hardcoded `settings.s3_get_usd_per_1k` constant. When None, the existing constant
    fallback is used — the right behavior for callers that don't have an ``AWSIntegration``
    (e.g. the local MinIO dev stack), but silently wrong-looking-right for any real AWS
    account if not flagged. The returned projection's ``assumptions["s3_get_price_source"]``
    is always set to ``"live"`` or ``"fallback_default"`` so callers/UI never have to
    re-derive "was this approximate" from whether an optional argument happened to be passed.
    """
    price = settings.cost_price_per_gb_month
    use_gib = settings.cost_use_gib

    current_bytes = current_storage_bytes(breakdown)
    current_cost = bytes_to_usd(current_bytes, price_per_gb_month=price, use_gib=use_gib)

    by_operation: dict[OperationType, float] = {
        OperationType.EXPIRE_SNAPSHOTS: bytes_to_usd(
            reclaim.reclaim_expire_bytes, price_per_gb_month=price, use_gib=use_gib
        ),
        OperationType.REMOVE_ORPHAN_FILES: bytes_to_usd(
            reclaim.reclaim_orphan_bytes, price_per_gb_month=price, use_gib=use_gib
        ),
        OperationType.OPTIMIZE: bytes_to_usd(
            reclaim.reclaim_optimize_bytes, price_per_gb_month=price, use_gib=use_gib
        ),
        OperationType.OPTIMIZE_MANIFESTS: bytes_to_usd(
            reclaim.reclaim_manifests_bytes, price_per_gb_month=price, use_gib=use_gib
        ),
    }
    projected_savings = round(sum(by_operation.values()), 4)
    projected_cost = round(max(0.0, current_cost - projected_savings), 4)
    # Prefer the live price when the caller has AWS credentials; fall back to the settings
    # constant otherwise. Both paths are correct (P0a keeps the hardcoded constant as the
    # fallback default — see §6.4) but they must never be visually indistinguishable in the
    # UI, so the source is always recorded below, not left for the caller to infer.
    have_live_price = live_s3_get_price_per_1k is not None
    effective_s3_get_price = (
        live_s3_get_price_per_1k
        if live_s3_get_price_per_1k is not None
        else settings.s3_get_usd_per_1k
    )
    query_savings = query_request_savings_usd(
        stats,
        query_gets_per_file=settings.query_gets_per_file,
        query_count_30d=stats.query_count_30d,
        s3_get_usd_per_1k=effective_s3_get_price,
    )
    merged_assumptions = {
        **(assumptions or {}),
        "s3_get_price_source": "live" if have_live_price else "fallback_default",
    }

    return SavingsProjection(
        precision=precision,
        current_monthly_cost_usd=current_cost,
        projected_monthly_cost_usd=projected_cost,
        projected_savings_usd_month=projected_savings,
        reclaimable_bytes=reclaimable_bytes(reclaim),
        by_operation=by_operation,
        query_request_savings_usd_month=query_savings,
        assumptions=merged_assumptions,
    )


@dataclass(frozen=True, slots=True)
class TrinoScanCostPrice:
    """A derived $/GiB Trino compute price for one day, plus enough to audit how it was
    computed. Unlike S3 (settings.cost_price_per_gb_month, a real published AWS rate), there is
    no sane universal Trino compute rate to fall back to -- a cluster's own sizing/spot-mix/
    utilization determines it entirely, so this is always either a real derived number or
    nothing at all. Callers must not invent a fallback constant the way project_savings does
    for S3 GET pricing."""

    day: date
    price_usd_per_gib: float
    namespace_cost_usd: float
    bytes_scanned: int


def derive_trino_scan_cost_prices(
    namespace_cost_by_day: Mapping[date, float],
    bytes_scanned_by_day: Mapping[date, int],
) -> list[TrinoScanCostPrice]:
    """$/GiB Trino compute price per day: real namespace spend (used+unused, from
    cryo.reporting.cost_ceiling.fetch_namespace_cost_range) divided by real bytes scanned that
    day (from QueryCostAttributionDaily, summed across clients/schemas for the day).

    Only days present in *both* mappings produce a price -- a day with cost but no
    bytes-scanned data (e.g. cost_attribution's watermark hasn't caught up yet) or bytes but no
    cost (e.g. CUR's 24-48h lag, see fetch_todays_namespace_cost's docstring) has no honest
    price to report for that day, not a guessed one. Callers writing this onto
    QueryCostAttributionDaily.estimated_scan_cost_usd should leave the column NULL for any day
    not returned here, exactly today's behavior before this function existed.
    """
    prices: list[TrinoScanCostPrice] = []
    for day, cost_usd in namespace_cost_by_day.items():
        bytes_scanned = bytes_scanned_by_day.get(day)
        if not bytes_scanned or bytes_scanned <= 0:
            continue
        if cost_usd <= 0:
            continue
        price_per_gib = round(cost_usd / (bytes_scanned / GIB), 8)
        prices.append(
            TrinoScanCostPrice(
                day=day,
                price_usd_per_gib=price_per_gib,
                namespace_cost_usd=cost_usd,
                bytes_scanned=bytes_scanned,
            )
        )
    return prices
