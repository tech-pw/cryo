"""Integration tests for S3 Intelligent-Tiering analysis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from cryo.cost.s3_pricing import S3StorageClass, calculate_monthly_storage_cost
from cryo.db.models import QueryLog, Source, Table, TableStats
from cryo.tiering.s3_analyzer import analyze_table_for_tiering


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={"catalog_name": "lake"},
    )
    session.add(source)
    session.flush()
    return source


def _seed_table(session, *, schema_name: str, table_name: str) -> Table:
    source = _seed_source(session)
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name=schema_name,
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.flush()
    return table


def test_analyze_table_for_tiering_uses_s3_pricing_for_standard_cost(session):
    """Cold large tables should compute current cost via calculate_monthly_storage_cost."""
    table = _seed_table(session, schema_name="archive", table_name="cold_data")
    size_gb = 500.0

    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=75,
            total_files=1000,
            small_files_count=0,
            total_size_gb=size_gb,
        )
    )
    session.commit()

    expected_standard_cost = calculate_monthly_storage_cost(size_gb, S3StorageClass.STANDARD)

    with patch(
        "cryo.tiering.s3_analyzer.calculate_monthly_storage_cost",
        wraps=calculate_monthly_storage_cost,
    ) as mock_pricing:
        rec = analyze_table_for_tiering(session, table.id, lookback_days=90)

    assert mock_pricing.call_count >= 1
    assert rec.current_monthly_cost_usd == expected_standard_cost
    assert rec.current_size_gb == size_gb


def test_analyze_table_for_tiering_recommends_enable_for_cold_table(session):
    """Measured access older than 90 days, still inside the lookback, recommends tiering."""
    table = _seed_table(session, schema_name="archive", table_name="stale_data")
    source = session.get(Source, table.source_id)

    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=70,
            total_files=500,
            small_files_count=0,
            total_size_gb=200.0,
        )
    )
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

    rec = analyze_table_for_tiering(session, table.id, lookback_days=150)

    assert rec.recommendation == "enable"
    assert rec.days_since_last_access >= 90
    assert rec.estimated_annual_savings_usd > 0


def test_analyze_table_for_tiering_rejects_small_files(session):
    """Tables with files below the 128 KB minimum should be marked not_suitable."""
    table = _seed_table(session, schema_name="raw", table_name="tiny_files")

    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="D",
            overall_score=45,
            total_files=1_000_000,
            small_files_count=900_000,
            total_size_gb=10.0,
        )
    )
    session.commit()

    rec = analyze_table_for_tiering(session, table.id)

    assert rec.recommendation == "not_suitable"
    assert rec.avg_file_size_kb < 128
    assert "128 KB" in rec.reason
