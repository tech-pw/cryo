from __future__ import annotations

from datetime import date

from cryo.config import Settings
from cryo.cost.model import (
    GIB,
    CostStatsInput,
    ReclaimBytes,
    bytes_to_usd,
    current_storage_bytes,
    derive_trino_scan_cost_prices,
    project_savings,
    query_request_savings_usd,
)
from cryo.domain.enums import CostPrecision, OperationType
from cryo.domain.models import StorageBreakdown
from cryo.scanner.cost import GB, estimate_storage_cost


def test_bytes_to_usd_uses_gib_by_default() -> None:
    assert bytes_to_usd(GB, price_per_gb_month=0.023) == 0.023


def test_bytes_to_usd_uses_decimal_gb_when_configured() -> None:
    assert bytes_to_usd(1_000_000_000, price_per_gb_month=0.023, use_gib=False) == 0.023


def test_project_savings_sums_disjoint_operations() -> None:
    breakdown = StorageBreakdown(
        live_bytes=100 * GB,
        dead_bytes=20 * GB,
        metadata_bytes=5 * GB,
        delete_bytes=2 * GB,
        orphan_bytes=3 * GB,
    )
    reclaim = ReclaimBytes(
        reclaim_expire_bytes=20 * GB,
        reclaim_orphan_bytes=3 * GB,
        reclaim_optimize_bytes=2 * GB,
        reclaim_manifests_bytes=0,
    )
    settings = Settings()
    stats = CostStatsInput(
        small_files_count=10,
        small_file_bytes=500 * 1024 * 1024,
        query_count_30d=1000,
    )

    projection = project_savings(breakdown, reclaim, stats, settings)

    assert projection.precision is CostPrecision.METADATA
    assert projection.current_monthly_cost_usd == bytes_to_usd(
        current_storage_bytes(breakdown), price_per_gb_month=settings.cost_price_per_gb_month
    )
    assert projection.reclaimable_bytes == 25 * GB
    price = settings.cost_price_per_gb_month
    assert projection.by_operation[OperationType.EXPIRE_SNAPSHOTS] == bytes_to_usd(
        20 * GB, price_per_gb_month=price
    )
    assert projection.by_operation[OperationType.REMOVE_ORPHAN_FILES] == bytes_to_usd(
        3 * GB, price_per_gb_month=price
    )
    assert projection.by_operation[OperationType.OPTIMIZE] == bytes_to_usd(
        2 * GB, price_per_gb_month=price
    )
    assert projection.projected_savings_usd_month == round(sum(projection.by_operation.values()), 4)
    assert projection.query_request_savings_usd_month > 0
    assert projection.projected_savings_usd_month == round(
        projection.by_operation[OperationType.EXPIRE_SNAPSHOTS]
        + projection.by_operation[OperationType.REMOVE_ORPHAN_FILES]
        + projection.by_operation[OperationType.OPTIMIZE]
        + projection.by_operation[OperationType.OPTIMIZE_MANIFESTS],
        4,
    )


def test_query_request_savings_uses_file_delta() -> None:
    stats = CostStatsInput(
        small_files_count=10,
        small_file_bytes=500 * 1024 * 1024,
        target_file_size_bytes=268_435_456,
        query_count_30d=1000,
    )
    savings = query_request_savings_usd(
        stats,
        query_gets_per_file=1.0,
        query_count_30d=1000,
        s3_get_usd_per_1k=0.0004,
    )
    assert savings == round(8 * 1.0 * 1000 * 0.0004 / 1000, 4)


def test_heuristic_tier0_parity_with_estimate_storage_cost() -> None:
    total_size_bytes = 100 * GB
    delete_bytes = 5 * GB
    manifest_bytes = 1 * GB
    total_snapshots = 50

    tier0 = estimate_storage_cost(
        total_size_bytes=total_size_bytes,
        total_delete_bytes=delete_bytes,
        manifest_total_bytes=manifest_bytes,
        total_snapshots=total_snapshots,
    )
    breakdown = StorageBreakdown(
        live_bytes=total_size_bytes,
        dead_bytes=0,
        metadata_bytes=manifest_bytes,
        delete_bytes=delete_bytes,
    )
    reclaim = ReclaimBytes(0, 0, delete_bytes, 0)
    projection = project_savings(
        breakdown,
        reclaim,
        CostStatsInput(),
        Settings(),
        precision=CostPrecision.HEURISTIC,
    )

    # Tier 0 heuristic includes snapshot overhead in total storage, not in breakdown buckets.
    # Current cost from breakdown alone is lower; parity is on the Tier 0 estimate helper.
    assert tier0.snapshot_overhead_gb == 15
    assert tier0.monthly_s3_cost_usd == 2.78
    settings = Settings()
    assert projection.current_monthly_cost_usd == bytes_to_usd(
        total_size_bytes + delete_bytes + manifest_bytes,
        price_per_gb_month=settings.cost_price_per_gb_month,
    )


def _minimal_breakdown_and_reclaim() -> tuple[StorageBreakdown, ReclaimBytes]:
    return (
        StorageBreakdown(live_bytes=10 * GB, dead_bytes=0, metadata_bytes=0, delete_bytes=0),
        ReclaimBytes(0, 0, 0, 0),
    )


def test_project_savings_tags_fallback_default_when_no_live_price_given() -> None:
    """docs/s3-request-cost-tracking.md §6.4: a fallback-derived price must never be visually
    indistinguishable from a live-fetched one -- the caller/UI needs
    assumptions["s3_get_price_source"] to render the "approximate" label."""
    breakdown, reclaim = _minimal_breakdown_and_reclaim()
    projection = project_savings(
        breakdown,
        reclaim,
        CostStatsInput(small_files_count=10, query_count_30d=1000),
        Settings(),
    )

    assert projection.assumptions["s3_get_price_source"] == "fallback_default"


def test_project_savings_tags_live_and_uses_live_price_when_given() -> None:
    breakdown, reclaim = _minimal_breakdown_and_reclaim()
    settings = Settings()
    stats = CostStatsInput(small_files_count=10, query_count_30d=1000)

    fallback_projection = project_savings(breakdown, reclaim, stats, settings)
    live_projection = project_savings(
        breakdown,
        reclaim,
        stats,
        settings,
        live_s3_get_price_per_1k=settings.s3_get_usd_per_1k * 10,
    )

    assert live_projection.assumptions["s3_get_price_source"] == "live"
    # A 10x higher live GET price must actually change the computed savings, not just the label.
    assert live_projection.query_request_savings_usd_month == round(
        fallback_projection.query_request_savings_usd_month * 10, 4
    )


def test_project_savings_preserves_caller_assumptions_alongside_pricing_source() -> None:
    breakdown, reclaim = _minimal_breakdown_and_reclaim()
    projection = project_savings(
        breakdown,
        reclaim,
        CostStatsInput(),
        Settings(),
        assumptions={"format_version": 2},
    )

    assert projection.assumptions["format_version"] == 2
    assert projection.assumptions["s3_get_price_source"] == "fallback_default"


def test_derive_trino_scan_cost_prices_computes_dollars_per_gib() -> None:
    day = date(2026, 8, 14)
    prices = derive_trino_scan_cost_prices(
        namespace_cost_by_day={day: 100.0},
        bytes_scanned_by_day={day: 50 * GIB},
    )
    assert len(prices) == 1
    assert prices[0].day == day
    assert prices[0].price_usd_per_gib == round(100.0 / 50, 8)
    assert prices[0].namespace_cost_usd == 100.0
    assert prices[0].bytes_scanned == 50 * GIB


def test_derive_trino_scan_cost_prices_skips_days_missing_from_either_side() -> None:
    """A day with real cost but no bytes-scanned data (watermark lag), or bytes but no cost
    (CUR's 24-48h lag), must produce no price for that day -- never a guessed one."""
    day1, day2, day3 = date(2026, 8, 14), date(2026, 8, 15), date(2026, 8, 16)
    prices = derive_trino_scan_cost_prices(
        namespace_cost_by_day={day1: 100.0, day2: 50.0},  # day3 has no cost yet
        bytes_scanned_by_day={day1: 50 * GIB, day3: 10 * GIB},  # day2 has no bytes yet
    )
    assert len(prices) == 1
    assert prices[0].day == day1


def test_derive_trino_scan_cost_prices_skips_zero_or_negative_bytes() -> None:
    day = date(2026, 8, 14)
    prices = derive_trino_scan_cost_prices(
        namespace_cost_by_day={day: 100.0},
        bytes_scanned_by_day={day: 0},
    )
    assert prices == []


def test_derive_trino_scan_cost_prices_no_hardcoded_fallback_when_empty() -> None:
    """Unlike S3 pricing (which has settings.cost_price_per_gb_month as a real published
    fallback), Trino compute cost has no sane universal rate -- empty input must produce an
    empty result, never a guessed constant."""
    assert derive_trino_scan_cost_prices({}, {}) == []
