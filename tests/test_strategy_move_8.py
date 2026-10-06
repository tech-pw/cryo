"""Move 8 — S3-request-cost preflight estimate before large orphan scans.

The estimate computes how many S3 LIST requests an orphan scan will make before
running it, using the table's total_files from the most recent scan (no extra S3 calls).
"""

from __future__ import annotations

from cryo.config import Settings
from cryo.scanner.orphans import (
    S3_LIST_KEYS_PER_PAGE,
    estimate_orphan_scan_cost,
)


class TestEstimateOrphanScanCost:
    def test_small_table_under_threshold(self) -> None:
        settings = Settings()
        result = estimate_orphan_scan_cost(
            total_files=100,
            partition_depth=2,
            settings=settings,
        )
        assert result.estimated_files_to_scan == 100
        assert result.partition_depth == 2
        list_pages = (100 + S3_LIST_KEYS_PER_PAGE - 1) // S3_LIST_KEYS_PER_PAGE
        assert result.estimated_list_requests == list_pages + 2
        assert result.estimated_cost_usd == round(
            (list_pages + 2) / 1000.0 * settings.s3_list_usd_per_1k, 6
        )
        assert result.warning is None

    def test_large_table_capped(self) -> None:
        settings = Settings()
        huge = settings.cost_scan_max_list_keys + 1
        result = estimate_orphan_scan_cost(
            total_files=huge,
            partition_depth=3,
            settings=settings,
        )
        assert result.estimated_files_to_scan == settings.cost_scan_max_list_keys
        assert result.warning is not None
        assert "capped" in result.warning

    def test_high_cost_warning_fires_above_threshold(self) -> None:
        mock_settings = Settings(
            s3_list_usd_per_1k=2000.0,
            cost_scan_max_list_keys=2_000_000,
        )
        result = estimate_orphan_scan_cost(
            total_files=2,
            partition_depth=1,
            settings=mock_settings,
        )
        list_reqs = (2 + S3_LIST_KEYS_PER_PAGE - 1) // S3_LIST_KEYS_PER_PAGE + 1
        assert result.estimated_list_requests == list_reqs
        assert result.estimated_cost_usd > 1.0
        assert result.warning is not None
        assert "exceeds $1.00" in result.warning

    def test_zero_files(self) -> None:
        settings = Settings()
        result = estimate_orphan_scan_cost(
            total_files=0,
            partition_depth=0,
            settings=settings,
        )
        assert result.estimated_files_to_scan == 0
        assert result.estimated_list_requests == 0
        assert result.estimated_cost_usd == 0.0
        assert result.warning is None

    def test_partition_depth_affects_request_count(self) -> None:
        settings = Settings()
        r1 = estimate_orphan_scan_cost(total_files=500, partition_depth=1, settings=settings)
        r2 = estimate_orphan_scan_cost(total_files=500, partition_depth=4, settings=settings)
        assert r2.estimated_list_requests == r1.estimated_list_requests + 3
