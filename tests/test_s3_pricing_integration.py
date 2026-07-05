"""Integration tests for S3 storage pricing calculations."""

from __future__ import annotations

import pytest

from cryo.cost.s3_pricing import (
    S3_PRICING_US_EAST_1,
    S3StorageClass,
    calculate_monthly_storage_cost,
)


@pytest.mark.parametrize(
    "storage_class",
    list(S3StorageClass),
)
def test_calculate_monthly_storage_cost_all_storage_classes(storage_class: S3StorageClass):
    """Every storage class should produce a positive monthly cost for non-zero size."""
    size_gb = 100.0
    pricing = S3_PRICING_US_EAST_1[storage_class]
    expected = size_gb * pricing.storage_cost_per_gb_month

    cost = calculate_monthly_storage_cost(size_gb, storage_class)

    assert cost == pytest.approx(expected)
    assert cost > 0


def test_calculate_monthly_storage_cost_standard_pricing():
    """Standard tier should use $0.023/GB/month in us-east-1."""
    cost = calculate_monthly_storage_cost(1000.0, S3StorageClass.STANDARD)

    assert cost == pytest.approx(23.0)


def test_calculate_monthly_storage_cost_deep_archive_cheapest():
    """Deep Archive should cost less than Standard for the same volume."""
    size_gb = 500.0
    standard = calculate_monthly_storage_cost(size_gb, S3StorageClass.STANDARD)
    deep_archive = calculate_monthly_storage_cost(size_gb, S3StorageClass.DEEP_ARCHIVE)

    assert deep_archive < standard
    assert deep_archive == pytest.approx(size_gb * 0.00099)


def test_calculate_monthly_storage_cost_zero_size():
    """Zero-size storage should cost nothing regardless of class."""
    for storage_class in S3StorageClass:
        assert calculate_monthly_storage_cost(0.0, storage_class) == 0.0


def test_calculate_monthly_storage_cost_scales_linearly():
    """Cost should scale linearly with storage size."""
    unit_cost = calculate_monthly_storage_cost(1.0, S3StorageClass.STANDARD_IA)
    bulk_cost = calculate_monthly_storage_cost(250.0, S3StorageClass.STANDARD_IA)

    assert bulk_cost == pytest.approx(unit_cost * 250)
