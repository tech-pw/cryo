from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from cryo.config import Settings
from cryo.db.models import TableCost
from cryo.scanner.orphans import carry_forward_orphans, scan_orphans, should_run_orphan_scan


def test_should_run_orphan_scan_when_forced() -> None:
    settings = Settings(cost_full_scan_every_hours=168)
    assert should_run_orphan_scan(None, datetime.now(UTC), settings, force=True) is True


def test_should_run_orphan_scan_on_cadence() -> None:
    settings = Settings(cost_full_scan_every_hours=24)
    scan_at = datetime(2026, 7, 3, tzinfo=UTC)
    previous = TableCost(
        table_id=MagicMock(),
        scan_at=scan_at - timedelta(days=2),
        precision="metadata",
        price_per_gb_month=0.023,
        live_bytes=0,
        dead_bytes=0,
        metadata_bytes=0,
        reclaim_expire_bytes=0,
        reclaim_orphan_bytes=0,
        reclaim_optimize_bytes=0,
        reclaim_manifests_bytes=0,
        current_monthly_cost_usd=0.0,
        projected_monthly_cost_usd=0.0,
        projected_savings_usd_month=0.0,
        last_full_cost_scan_at=scan_at - timedelta(days=2),
    )
    assert should_run_orphan_scan(previous, scan_at, settings, force=False) is True


def test_carry_forward_orphans_returns_previous_values() -> None:
    scan_at = datetime(2026, 7, 3, tzinfo=UTC)
    previous = TableCost(
        table_id=MagicMock(),
        scan_at=scan_at,
        precision="storage_truth",
        price_per_gb_month=0.023,
        live_bytes=0,
        dead_bytes=0,
        metadata_bytes=0,
        orphan_bytes=123,
        listed_bytes=456,
        reclaim_expire_bytes=0,
        reclaim_orphan_bytes=123,
        reclaim_optimize_bytes=0,
        reclaim_manifests_bytes=0,
        current_monthly_cost_usd=0.0,
        projected_monthly_cost_usd=0.0,
        projected_savings_usd_month=0.0,
        last_full_cost_scan_at=scan_at - timedelta(days=1),
    )
    assert carry_forward_orphans(previous) == (123, 456, previous.last_full_cost_scan_at)


@patch("cryo.scanner.orphans.boto3.client")
def test_scan_orphans_counts_unreferenced_keys(mock_client: MagicMock) -> None:
    scan_at = datetime(2026, 7, 3, tzinfo=UTC)
    s3 = MagicMock()
    mock_client.return_value = s3
    paginator = MagicMock()
    s3.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {
            "Contents": [
                {
                    "Key": "warehouse/db/table/data/live.parquet",
                    "Size": 100,
                    "LastModified": scan_at - timedelta(days=5),
                },
                {
                    "Key": "warehouse/db/table/data/orphan.parquet",
                    "Size": 50,
                    "LastModified": scan_at - timedelta(days=5),
                },
            ]
        }
    ]

    class FakeTable:
        def location(self) -> str:
            return "s3://bucket/warehouse/db/table/"

    result = scan_orphans(
        FakeTable(),
        source_config={
            "s3.endpoint": "http://localhost:9000",
            "s3.access-key-id": "minioadmin",
            "s3.secret-access-key": "minioadmin",
            "s3.path-style-access": "true",
        },
        referenced_file_paths={"s3://bucket/warehouse/db/table/data/live.parquet": 100},
        scan_at=scan_at,
        settings=Settings(),
    )

    assert result.orphan_bytes == 50
    assert result.listed_bytes == 150
    assert result.listed_keys == 2
    assert result.last_full_cost_scan_at == scan_at
