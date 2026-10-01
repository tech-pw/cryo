"""Access-evidence rules for the S3 Intelligent-Tiering recommender."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cryo.cost.s3_pricing import S3StorageClass, calculate_monthly_storage_cost
from cryo.db.models import AWSIntegration, QueryLog, Source, Table, TableQueryActivity, TableStats
from cryo.tiering.policies import TierCandidate
from cryo.tiering.s3_analyzer import analyze_table_for_tiering
from cryo.tiering.service import summarize_tier_savings


def _seed_table(session, *, size_gb: float = 200.0, total_files: int = 500) -> Table:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={"catalog_name": "lake"},
    )
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="archive",
        table_name=f"events-{uuid4().hex[:8]}",
        is_active=True,
    )
    session.add_all([source, table])
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=70,
            total_files=total_files,
            small_files_count=0,
            total_size_gb=size_gb,
        )
    )
    session.commit()
    return table


def test_activity_within_lookback_is_not_archive(session) -> None:
    table = _seed_table(session)
    session.add(
        TableQueryActivity(
            table_id=table.id,
            day=(datetime.now(UTC) - timedelta(days=10)).date(),
            query_count=4,
        )
    )
    session.commit()

    rec = analyze_table_for_tiering(session, table.id, lookback_days=90)

    assert rec.recommendation != "enable"
    assert rec.days_since_last_access == 10
    assert "82%" not in rec.reason
    assert "Archive Instant" not in rec.reason
    assert "not accessed in 90 days" not in rec.reason


def test_missing_access_evidence_is_unknown_not_cold(session) -> None:
    table = _seed_table(session)

    rec = analyze_table_for_tiering(session, table.id, lookback_days=90)

    assert rec.recommendation == "unknown"
    assert rec.recommendation != "enable"
    assert rec.estimated_annual_savings_usd == 0
    assert rec.days_since_last_access is None
    assert "evidence is missing" in rec.reason
    assert "not accessed in 90 days" not in rec.reason
    assert "82%" not in rec.reason
    assert "cost reduction" not in rec.reason


def test_query_log_outside_lookback_is_not_cold(session) -> None:
    table = _seed_table(session)
    source = session.get(Source, table.source_id)
    session.add(
        QueryLog(
            source_id=source.id,
            table_id=table.id,
            query_id="q-old",
            engine="trino",
            query_text="SELECT 1",
            executed_at=datetime.now(UTC) - timedelta(days=120),
            duration_ms=100,
        )
    )
    session.commit()

    rec = analyze_table_for_tiering(session, table.id, lookback_days=90)

    assert rec.recommendation == "unknown"
    assert rec.estimated_annual_savings_usd == 0
    assert rec.days_since_last_access is None
    assert "evidence is missing" in rec.reason


def test_summarize_tier_savings_does_not_invent_candidate_bytes() -> None:
    candidates = [
        TierCandidate("iceberg.raw.t", "2024-01-01", 100, "GLACIER_IR"),
        TierCandidate("iceberg.raw.t", "2024-01-02", 120, "GLACIER_IR"),
    ]

    summary = summarize_tier_savings(candidates)

    invented = round(2 * 10.0 * (0.023 - 0.0125), 2)
    assert summary["candidate_count"] == 2
    assert summary["estimated_savings_usd_month"] in (None, 0, 0.0)
    assert summary["estimated_savings_usd_month"] != invented
    assert "not measured" in str(summary.get("note", "")).lower()
    assert summary.get("assumptions", {}).get("estimated_gb_per_candidate") != 10.0


def test_summarize_tier_savings_empty_is_zero() -> None:
    summary = summarize_tier_savings([])

    assert summary["candidate_count"] == 0
    assert summary["estimated_savings_usd_month"] == 0.0


def test_us_east_1_rate_unchanged_when_region_passed() -> None:
    cost = calculate_monthly_storage_cost(1000.0, S3StorageClass.STANDARD, region="us-east-1")

    assert cost == pytest.approx(23.0)


def test_unknown_region_falls_back_to_us_east_1() -> None:
    cost = calculate_monthly_storage_cost(1000.0, S3StorageClass.STANDARD, region="eu-west-1")

    assert cost == pytest.approx(23.0)


def test_mumbai_standard_and_glacier_ir_rates() -> None:
    standard = calculate_monthly_storage_cost(1000.0, S3StorageClass.STANDARD, region="ap-south-1")
    glacier_ir = calculate_monthly_storage_cost(
        1000.0, S3StorageClass.GLACIER_IR, region="ap-south-1"
    )

    assert standard == pytest.approx(25.0)
    assert glacier_ir == pytest.approx(5.0)


def test_mumbai_standard_uses_volume_tiers() -> None:
    first_tier_gb = 50 * 1024
    extra_gb = 1024.0
    cost = calculate_monthly_storage_cost(
        first_tier_gb + extra_gb, S3StorageClass.STANDARD, region="ap-south-1"
    )

    assert cost == pytest.approx(first_tier_gb * 0.025 + extra_gb * 0.024)


def test_analyzer_uses_source_region_for_storage_cost(session) -> None:
    table = _seed_table(session, size_gb=1000.0, total_files=100)
    session.add(
        AWSIntegration(source_id=table.source_id, bucket="lake", region="ap-south-1", enabled=True)
    )
    session.commit()

    rec = analyze_table_for_tiering(session, table.id, lookback_days=90)

    assert rec.recommendation == "unknown"
    assert rec.estimated_annual_savings_usd == 0
    assert rec.current_monthly_cost_usd == pytest.approx(
        calculate_monthly_storage_cost(1000.0, S3StorageClass.STANDARD, region="ap-south-1")
    )
