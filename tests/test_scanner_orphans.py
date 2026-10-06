from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from cryo.config import Settings
from cryo.db.models import TableCost
from cryo.scanner.orphans import (
    carry_forward_orphans,
    parse_s3_location,
    scan_orphans,
    should_run_orphan_scan,
)


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


def test_parse_s3_location_accepts_s3a_scheme() -> None:
    """Regression test: s3a:// is the Hadoop/Spark-ecosystem convention for the exact same
    bucket/key structure as s3:// -- common for Hive Metastore-backed tables written via Spark.
    Rejecting it silently broke bucket discovery and orphan-file scanning for every table on
    such a source, with skipped_reason="unsupported_location" and no error surfaced anywhere."""
    bucket, prefix = parse_s3_location("s3a://acme-lake-gold/gold/revenue/t")
    assert bucket == "acme-lake-gold"
    assert prefix == "gold/revenue/t/data/"


def test_parse_s3_location_accepts_s3n_scheme() -> None:
    bucket, prefix = parse_s3_location("s3n://legacy-bucket/db/table")
    assert bucket == "legacy-bucket"
    assert prefix == "db/table/data/"


def test_parse_s3_location_still_accepts_plain_s3_scheme() -> None:
    bucket, prefix = parse_s3_location("s3://bucket/warehouse/db/table")
    assert bucket == "bucket"
    assert prefix == "warehouse/db/table/data/"


def test_parse_s3_location_rejects_unrelated_schemes() -> None:
    bucket, prefix = parse_s3_location("hdfs://namenode/warehouse/db/table")
    assert bucket is None
    assert prefix == ""


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


@patch("cryo.scanner.orphans.boto3.client")
def test_scan_orphans_does_not_skip_s3a_locations(mock_client: MagicMock) -> None:
    """Before the s3a fix, this table would bail out immediately with
    skipped_reason="unsupported_location" and never actually scan anything."""
    scan_at = datetime(2026, 7, 3, tzinfo=UTC)
    s3 = MagicMock()
    mock_client.return_value = s3
    paginator = MagicMock()
    s3.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {
            "Contents": [
                {
                    "Key": "gold/revenue/t/data/orphan.parquet",
                    "Size": 50,
                    "LastModified": scan_at - timedelta(days=5),
                }
            ]
        }
    ]

    class FakeTable:
        def location(self) -> str:
            return "s3a://acme-lake-gold/gold/revenue/t"

    result = scan_orphans(
        FakeTable(),
        source_config={
            "s3.endpoint": "http://localhost:9000",
            "s3.access-key-id": "minioadmin",
            "s3.secret-access-key": "minioadmin",
        },
        referenced_file_paths={},
        scan_at=scan_at,
        settings=Settings(),
    )

    assert result.skipped_reason is None
    assert result.orphan_bytes == 50


@patch("cryo.scanner.orphans.boto3.client")
def test_scan_orphans_matches_referenced_paths_across_s3_schemes(
    mock_client: MagicMock,
) -> None:
    """Regression, found in production 2026-08-05: manifest file_path entries preserve
    whatever scheme the writer used (s3a:// for this deployment's Spark-written tables), but
    the orphan-scan comparison reconstructed listed objects as hardcoded "s3://" -- so no
    listed object ever matched anything in referenced_file_paths, regardless of what was
    actually referenced. Every table with a completed orphan scan showed exactly
    orphan_bytes == listed_bytes (11,281 tables, ~296TB), because 100% of every table's real,
    live, referenced files were being counted as orphan garbage. This table has two listed
    objects, one genuinely referenced (by an s3a:// manifest path) and one genuinely orphaned;
    only the orphan should be counted."""
    scan_at = datetime(2026, 7, 3, tzinfo=UTC)
    s3 = MagicMock()
    mock_client.return_value = s3
    paginator = MagicMock()
    s3.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {
            "Contents": [
                {
                    "Key": "gold/revenue/t/data/live.parquet",
                    "Size": 100,
                    "LastModified": scan_at - timedelta(days=5),
                },
                {
                    "Key": "gold/revenue/t/data/orphan.parquet",
                    "Size": 50,
                    "LastModified": scan_at - timedelta(days=5),
                },
            ]
        }
    ]

    class FakeTable:
        def location(self) -> str:
            return "s3a://acme-lake-gold/gold/revenue/t"

    result = scan_orphans(
        FakeTable(),
        source_config={
            "s3.endpoint": "http://localhost:9000",
            "s3.access-key-id": "minioadmin",
            "s3.secret-access-key": "minioadmin",
        },
        referenced_file_paths={
            "s3a://acme-lake-gold/gold/revenue/t/data/live.parquet": 100,
        },
        scan_at=scan_at,
        settings=Settings(),
    )

    assert result.listed_bytes == 150
    assert result.orphan_bytes == 50
