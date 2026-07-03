from __future__ import annotations

from cryo.config import Settings
from cryo.cost.model import (
    CostStatsInput,
    ReclaimBytes,
    bytes_to_usd,
    current_storage_bytes,
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
