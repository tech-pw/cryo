"""Tests for the CUR + S3 Inventory cost-attribution module.

Covers:
- CUR row parsing (CSV, JSON)
- Bucket extraction from ARN resource ids
- Table-prefix inference from S3 inventory keys
- Fixture-based ingest (full pipeline without real AWS)
- Aggregation queries (by-bucket, daily-series, by-prefix)
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date
from pathlib import Path

import pytest

from cryo.db.models import (
    AWSCostInsightConfig,
    AWSCurLineItem,
    AWSInventoryObject,
    Source,
)
from cryo.integrations import aws_cost_insights

# ---------------------------------------------------------------------------
# Pure parsing tests (no DB)
# ---------------------------------------------------------------------------


def test_parse_cur_csv_row_extracts_bucket_from_resource_arn() -> None:
    row = {
        "lineItem/UsageStartDate": "2026-07-01T00:00:00Z",
        "lineItem/ProductCode": "AmazonS3",
        "lineItem/UsageType": "TimedStorage-ByteHrs",
        "lineItem/ResourceId": "arn:aws:s3:::lake-bucket/db/sales/data.parquet",
        "lineItem/UnblendedCost": "12.34567890",
        "lineItem/UsageAmount": "1024",
        "lineItem/UsageUnit": "GB-Mo",
        "product/region": "ap-south-1",
    }
    parsed = aws_cost_insights._parse_cur_row_csv(row)
    assert parsed is not None
    assert parsed.bucket == "lake-bucket"
    assert parsed.usage_date == date(2026, 7, 1)
    assert parsed.unblended_cost == 12.34567890
    assert parsed.usage_quantity == 1024.0
    assert parsed.region == "ap-south-1"


def test_parse_cur_csv_row_skips_zero_cost_rows() -> None:
    row = {
        "lineItem/UsageStartDate": "2026-07-01T00:00:00Z",
        "lineItem/ProductCode": "AmazonS3",
        "lineItem/UsageType": "TimedStorage-ByteHrs",
        "lineItem/UnblendedCost": "0",
    }
    assert aws_cost_insights._parse_cur_row_csv(row) is None


def test_parse_cur_csv_row_falls_back_to_tagged_bucket() -> None:
    row = {
        "lineItem/UsageStartDate": "2026-07-01T00:00:00Z",
        "lineItem/ProductCode": "AmazonS3",
        "lineItem/UsageType": "TimedStorage-ByteHrs",
        "lineItem/UnblendedCost": "0.01",
        "resourceTags/user:bucket": "tagged-bucket",
    }
    parsed = aws_cost_insights._parse_cur_row_csv(row)
    assert parsed is not None
    assert parsed.bucket == "tagged-bucket"


def test_parse_cur_json_row_uses_nested_keys() -> None:
    entry = {
        "lineItem": {
            "UsageStartDate": "2026-07-02T00:00:00Z",
            "ProductCode": "AmazonS3",
            "UsageType": "Requests-Tier1",
            "ResourceId": "arn:aws:s3:::json-bucket/path/x",
            "UnblendedCost": 0.5,
            "UsageAmount": 100,
            "UsageUnit": "Requests",
        },
        "product": {"region": "us-east-1"},
    }
    parsed = aws_cost_insights._parse_cur_row_json(entry)
    assert parsed is not None
    assert parsed.bucket == "json-bucket"
    assert parsed.region == "us-east-1"
    assert parsed.usage_quantity == 100.0


def test_parse_cur_csv_stream_handles_full_record() -> None:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "lineItem/UsageStartDate",
            "lineItem/ProductCode",
            "lineItem/UsageType",
            "lineItem/ResourceId",
            "lineItem/UnblendedCost",
            "lineItem/UsageAmount",
            "lineItem/UsageUnit",
            "product/region",
        ]
    )
    writer.writerow(
        [
            "2026-07-01T00:00:00Z",
            "AmazonS3",
            "TimedStorage-ByteHrs",
            "arn:aws:s3:::b1/path",
            "1.0",
            "100",
            "GB-Mo",
            "us-east-1",
        ]
    )
    writer.writerow(
        [
            "2026-07-02T00:00:00Z",
            "AmazonS3",
            "Requests-Tier1",
            "arn:aws:s3:::b2/path",
            "0.5",
            "1000",
            "Requests",
            "us-east-1",
        ]
    )
    buf.seek(0)
    rows = list(aws_cost_insights._parse_cur_csv(buf))
    assert len(rows) == 2
    assert [r.bucket for r in rows] == ["b1", "b2"]


def test_parse_inventory_csv_infers_table_prefix() -> None:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Bucket", "Key", "Size", "LastModifiedDate", "StorageClass"])
    writer.writerow(
        ["prod", "db/sales/data/file1.parquet", "1024", "2026-07-01T00:00:00Z", "STANDARD"]
    )
    writer.writerow(
        ["prod", "db/sales/data/file2.parquet", "2048", "2026-07-01T00:00:00Z", "STANDARD"]
    )
    writer.writerow(
        ["prod", "logs/audit/year=2026/file.parquet", "512", "2026-07-01T00:00:00Z", "STANDARD_IA"]
    )
    buf.seek(0)
    rows = list(aws_cost_insights._parse_inventory_csv(buf))
    assert len(rows) == 3
    assert [r.table_prefix for r in rows] == ["db/sales", "db/sales", "logs/audit"]
    assert rows[0].size_bytes == 1024


# ---------------------------------------------------------------------------
# DB-backed tests (CUR fixture ingest + aggregations)
# ---------------------------------------------------------------------------


def _seed_source(session) -> Source:
    src = Source(
        id=uuid.uuid4(),
        name=f"source-{uuid.uuid4()}",
        type="glue",
        config={},
        enabled=True,
        maintenance_schema_names=[],
    )
    session.add(src)
    session.flush()
    return src


def _write_cur_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "cur-sample.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "lineItem/UsageStartDate",
                "lineItem/ProductCode",
                "lineItem/UsageType",
                "lineItem/ResourceId",
                "lineItem/UnblendedCost",
                "lineItem/UsageAmount",
                "lineItem/UsageUnit",
                "product/region",
            ]
        )
        rows = [
            (
                "2026-07-01T00:00:00Z",
                "AmazonS3",
                "TimedStorage-ByteHrs",
                "arn:aws:s3:::lake-bucket/db/sales/x",
                "10.0",
                "100",
                "GB-Mo",
                "ap-south-1",
            ),
            (
                "2026-07-01T00:00:00Z",
                "AmazonS3",
                "TimedStorage-ByteHrs",
                "arn:aws:s3:::lake-bucket/db/users/x",
                "5.0",
                "50",
                "GB-Mo",
                "ap-south-1",
            ),
            (
                "2026-07-02T00:00:00Z",
                "AmazonS3",
                "Requests-Tier1",
                "arn:aws:s3:::lake-bucket/db/sales/x",
                "0.05",
                "1000",
                "Requests",
                "ap-south-1",
            ),
            (
                "2026-07-02T00:00:00Z",
                "AmazonEC2",
                "BoxUsage:t3.medium",
                "arn:aws:ec2:ap-south-1:123:instance/i-aaaa",
                "1.23",
                "24",
                "Hrs",
                "ap-south-1",
            ),
        ]
        for r in rows:
            writer.writerow(r)
    return path


def test_ingest_cur_from_fixture_persists_line_items(session, tmp_path) -> None:
    src = _seed_source(session)
    cfg = AWSCostInsightConfig(source_id=src.id, fixture_path=str(_write_cur_fixture(tmp_path)))
    session.add(cfg)
    session.flush()

    summary = aws_cost_insights.ingest_cur_from_fixture(session, cfg, cfg.fixture_path)
    session.commit()

    assert summary.error is None
    assert summary.rows_ingested == 4
    assert summary.through_date == date(2026, 7, 2)

    rows = session.query(AWSCurLineItem).all()
    assert len(rows) == 4
    # Three rows belong to lake-bucket; the EC2 row is unattributed by bucket.
    buckets = {row.bucket for row in rows}
    assert buckets == {"lake-bucket", None}


def test_cost_by_bucket_groups_unattributed_into_label(session, tmp_path) -> None:
    src = _seed_source(session)
    cfg = AWSCostInsightConfig(source_id=src.id, fixture_path=str(_write_cur_fixture(tmp_path)))
    session.add(cfg)
    session.flush()
    aws_cost_insights.ingest_cur_from_fixture(session, cfg, cfg.fixture_path)
    session.commit()

    result = aws_cost_insights.cost_by_bucket(session, src.id)
    assert result[0]["bucket"] == "lake-bucket"
    assert result[0]["total_cost_usd"] == pytest.approx(15.05)
    assert result[1]["bucket"] == "(unattributed)"
    assert result[1]["total_cost_usd"] == pytest.approx(1.23)


def test_cost_daily_series_returns_ascending_dates(session, tmp_path) -> None:
    src = _seed_source(session)
    cfg = AWSCostInsightConfig(source_id=src.id, fixture_path=str(_write_cur_fixture(tmp_path)))
    session.add(cfg)
    session.flush()
    aws_cost_insights.ingest_cur_from_fixture(session, cfg, cfg.fixture_path)
    session.commit()

    series = aws_cost_insights.cost_daily_series(session, src.id)
    assert len(series) == 2
    assert series[0]["usage_date"] == "2026-07-01"
    assert series[0]["total_cost_usd"] == pytest.approx(15.0)
    assert series[1]["usage_date"] == "2026-07-02"
    assert series[1]["total_cost_usd"] == pytest.approx(1.28)


def test_size_by_prefix_groups_inventory_rows(session) -> None:
    src = _seed_source(session)
    # Inventory: 3 rows under db/sales, 1 under logs/audit
    session.add(
        AWSInventoryObject(
            source_id=src.id,
            inventory_date=date(2026, 7, 1),
            bucket="lake-bucket",
            key="db/sales/a.parquet",
            size_bytes=1024,
            storage_class="STANDARD",
            table_prefix="db/sales",
        )
    )
    session.add(
        AWSInventoryObject(
            source_id=src.id,
            inventory_date=date(2026, 7, 1),
            bucket="lake-bucket",
            key="db/sales/b.parquet",
            size_bytes=2048,
            storage_class="STANDARD",
            table_prefix="db/sales",
        )
    )
    session.add(
        AWSInventoryObject(
            source_id=src.id,
            inventory_date=date(2026, 7, 1),
            bucket="lake-bucket",
            key="db/sales/c.parquet",
            size_bytes=512,
            storage_class="STANDARD",
            table_prefix="db/sales",
        )
    )
    session.add(
        AWSInventoryObject(
            source_id=src.id,
            inventory_date=date(2026, 7, 1),
            bucket="lake-bucket",
            key="logs/audit/x.parquet",
            size_bytes=256,
            storage_class="STANDARD_IA",
            table_prefix="logs/audit",
        )
    )
    session.commit()

    result = aws_cost_insights.size_by_table_prefix(session, src.id)
    assert result[0]["table_prefix"] == "db/sales"
    assert result[0]["size_bytes"] == 3584
    assert result[0]["object_count"] == 3
    assert result[1]["table_prefix"] == "logs/audit"
    assert result[1]["size_bytes"] == 256


def test_cost_by_bucket_filters_by_date_range(session, tmp_path) -> None:
    src = _seed_source(session)
    cfg = AWSCostInsightConfig(source_id=src.id, fixture_path=str(_write_cur_fixture(tmp_path)))
    session.add(cfg)
    session.flush()
    aws_cost_insights.ingest_cur_from_fixture(session, cfg, cfg.fixture_path)
    session.commit()

    only_july_1 = aws_cost_insights.cost_by_bucket(
        session, src.id, start=date(2026, 7, 1), end=date(2026, 7, 1)
    )
    # Only the 2 storage rows on July 1 belong to lake-bucket.
    assert only_july_1[0]["bucket"] == "lake-bucket"
    assert only_july_1[0]["total_cost_usd"] == pytest.approx(15.0)
    assert len(only_july_1) == 1


def test_find_config_for_source_returns_none_when_unset(session) -> None:
    src = _seed_source(session)
    assert aws_cost_insights.find_config_for_source(session, src.id) is None


def test_ingest_cur_from_fixture_returns_error_for_missing_file(session) -> None:
    src = _seed_source(session)
    cfg = AWSCostInsightConfig(source_id=src.id, fixture_path="/nonexistent/path.csv")
    session.add(cfg)
    session.flush()

    summary = aws_cost_insights.ingest_cur_from_fixture(session, cfg, cfg.fixture_path)
    assert summary.error is not None
    assert "Fixture not found" in summary.error
    assert summary.rows_ingested == 0


def test_ingest_cur_from_fixture_handles_unrecognized_extension(session, tmp_path) -> None:
    src = _seed_source(session)
    bad = tmp_path / "cur.weird"
    bad.write_text("not a cur file")
    cfg = AWSCostInsightConfig(source_id=src.id, fixture_path=str(bad))
    session.add(cfg)
    session.flush()

    summary = aws_cost_insights.ingest_cur_from_fixture(session, cfg, cfg.fixture_path)
    assert summary.error is not None
    assert "Unrecognized fixture format" in summary.error


def test_parse_cur_json_stream_skips_malformed_lines() -> None:
    buf = io.StringIO()
    buf.write(
        json.dumps(
            {
                "lineItem": {
                    "UsageStartDate": "2026-07-01T00:00:00Z",
                    "ProductCode": "AmazonS3",
                    "UsageType": "TimedStorage-ByteHrs",
                    "ResourceId": "arn:aws:s3:::x/y",
                    "UnblendedCost": 1.0,
                }
            }
        )
        + "\n"
    )
    buf.write("not json at all\n")
    buf.write(
        json.dumps(
            {
                "lineItem": {
                    "UsageStartDate": "2026-07-02T00:00:00Z",
                    "ProductCode": "AmazonS3",
                    "UsageType": "Requests-Tier1",
                    "ResourceId": "arn:aws:s3:::y/z",
                    "UnblendedCost": 0.25,
                }
            }
        )
        + "\n"
    )
    buf.seek(0)
    rows = list(aws_cost_insights._parse_cur_json(buf))
    assert len(rows) == 2
