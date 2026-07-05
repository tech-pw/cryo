"""Integration tests for zero-scan data quality profiling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import Source, Table, TableStats, TableStatsHistory
from cryo.quality.profiler import DataQualityProfile, profile_table_quality


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


def _manifest_stats(
    *,
    amount_null_rate: float = 0.0,
    amount_min: float = 0.0,
    amount_max: float = 1000.0,
) -> dict:
    value_count = 1000
    null_count = int(value_count * amount_null_rate / 100)
    return {
        "amount": {
            "null_count": null_count,
            "value_count": value_count,
            "lower_bound": amount_min,
            "upper_bound": amount_max,
        },
        "customer_id": {
            "null_count": 0,
            "value_count": value_count,
            "lower_bound": 1,
            "upper_bound": 99999,
        },
    }


def test_profile_table_quality_detects_negative_amounts(session):
    """Profiler should flag negative values in financial columns."""
    table = _seed_table(session, schema_name="finance", table_name="payments")
    scan_at = datetime.now(UTC)

    session.add(
        TableStats(
            table_id=table.id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=100,
            small_files_count=5,
            total_size_gb=10.0,
            raw={"manifest_stats": _manifest_stats(amount_min=-50.0, amount_max=500.0)},
        )
    )
    session.commit()

    profile = profile_table_quality(session, table.id)

    assert isinstance(profile, DataQualityProfile)
    assert profile.table_id == str(table.id)
    amount_metrics = next(m for m in profile.column_metrics if m.column_name == "amount")
    assert amount_metrics.value_range_changed is True
    assert "Negative" in amount_metrics.value_range_explanation
    assert profile.anomalies_detected >= 1


def test_profile_table_quality_detects_null_rate_spike(session):
    """Null rate more than 2x baseline should be flagged as a spike."""
    table = _seed_table(session, schema_name="finance", table_name="orders")
    scan_at = datetime.now(UTC)

    baseline_stats = _manifest_stats(amount_null_rate=2.0)
    for days_ago in range(10, 0, -1):
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=scan_at - timedelta(days=days_ago),
                small_files_count=10,
                total_size_gb=5.0,
                raw={"manifest_stats": baseline_stats},
            )
        )

    session.add(
        TableStats(
            table_id=table.id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="C",
            overall_score=65,
            total_files=200,
            small_files_count=20,
            total_size_gb=5.0,
            raw={"manifest_stats": _manifest_stats(amount_null_rate=25.0)},
        )
    )
    session.commit()

    profile = profile_table_quality(session, table.id, lookback_days=30)

    amount_metrics = next(m for m in profile.column_metrics if m.column_name == "amount")
    assert amount_metrics.null_rate_spike is True
    assert amount_metrics.null_rate_pct > amount_metrics.null_rate_baseline_pct * 2


def test_profile_table_quality_detects_schema_changes(session):
    """New and dropped columns between scans should be reported."""
    table = _seed_table(session, schema_name="analytics", table_name="events")
    scan_at = datetime.now(UTC)

    session.add(
        TableStatsHistory(
            table_id=table.id,
            scan_at=scan_at - timedelta(days=14),
            small_files_count=50,
            total_size_gb=8.0,
            raw={"manifest_stats": {"event_id": {"null_count": 0, "value_count": 100}}},
        )
    )
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=78,
            total_files=150,
            small_files_count=10,
            total_size_gb=8.0,
            raw={
                "manifest_stats": {
                    "event_id": {"null_count": 0, "value_count": 100},
                    "event_type": {"null_count": 0, "value_count": 100},
                }
            },
        )
    )
    session.commit()

    profile = profile_table_quality(session, table.id)

    assert profile.schema_version_changed is True
    assert "event_type" in profile.new_columns
    assert profile.anomalies_detected >= 1
