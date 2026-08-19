"""Tests for cryo/optimizer/partition_analyzer.py's over/under-partitioning guards.

Covers the two anti-patterns the module is meant to avoid recommending:
- identity-partitioning a high-cardinality column (one partition per distinct value)
- switching to a time granularity too fine for the table's actual data volume
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from cryo.db.models import QueryLog, QueryPattern, Source, Table, TableStats
from cryo.optimizer.partition_analyzer import analyze_partition_evolution, bucket_count_for


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(), name=f"source-{uuid4()}", type="rest", config={"catalog_name": "lake"}
    )
    session.add(source)
    session.flush()
    return source


def _seed_table(session, *, schema_name: str = "analytics", table_name: str) -> Table:
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


def _seed_stats(
    session,
    table: Table,
    *,
    partition_spec: list[dict] | None = None,
    total_size_gb: float = 100.0,
    total_partitions: int | None = 10,
) -> TableStats:
    stats = TableStats(
        table_id=table.id,
        scan_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        needs_compaction=False,
        compaction_priority="LOW",
        grade="A",
        overall_score=95,
        total_files=1000,
        small_files_count=0,
        total_size_gb=total_size_gb,
        total_partitions=total_partitions,
        raw={"partition_spec": partition_spec or []},
    )
    session.add(stats)
    session.flush()
    return stats


def _seed_pattern(
    session,
    table: Table,
    *,
    column_name: str,
    filter_count: int,
    equality_filter_count: int = 0,
    range_filter_count: int = 0,
    avg_selectivity: float | None = None,
) -> QueryPattern:
    pattern = QueryPattern(
        table_id=table.id,
        column_name=column_name,
        filter_count=filter_count,
        equality_filter_count=equality_filter_count,
        range_filter_count=range_filter_count,
        avg_selectivity=avg_selectivity,
    )
    session.add(pattern)
    session.flush()
    return pattern


def _seed_query_logs(
    session,
    table: Table,
    *,
    n: int,
    bytes_read: int,
) -> None:
    now = datetime.now(UTC)
    for i in range(n):
        session.add(
            QueryLog(
                source_id=table.source_id,
                table_id=table.id,
                query_id=f"q-{i}-{uuid4()}",
                engine="trino",
                executed_at=now,
                bytes_read=bytes_read,
            )
        )
    session.flush()


def test_recommends_bucket_not_identity_for_high_cardinality_column(session) -> None:
    table = _seed_table(session, table_name="orders")
    _seed_stats(session, table, partition_spec=[], total_size_gb=200.0)
    _seed_pattern(
        session, table, column_name="order_id", filter_count=900, equality_filter_count=900
    )
    _seed_pattern(session, table, column_name="misc", filter_count=100)

    rec = analyze_partition_evolution(session, table.id, min_query_threshold=1)

    assert rec is not None
    assert rec.cardinality_estimate == "high"
    assert "bucket" in rec.recommended_partition_spec.lower()
    assert "identity" not in rec.recommended_partition_spec.lower()
    assert not rec.warnings


def test_recommends_identity_for_low_cardinality_column(session) -> None:
    table = _seed_table(session, table_name="shipments")
    _seed_stats(session, table, partition_spec=[], total_size_gb=50.0)
    _seed_pattern(session, table, column_name="region", filter_count=900, equality_filter_count=900)
    _seed_pattern(session, table, column_name="misc", filter_count=100)

    rec = analyze_partition_evolution(session, table.id, min_query_threshold=1)

    assert rec is not None
    assert rec.cardinality_estimate == "low"
    assert "identity" in rec.recommended_partition_spec.lower()


def test_unknown_cardinality_column_gets_warning_and_lower_confidence(session) -> None:
    table = _seed_table(session, table_name="widgets")
    _seed_stats(session, table, partition_spec=[], total_size_gb=50.0)
    _seed_pattern(
        session, table, column_name="priority", filter_count=900, equality_filter_count=900
    )
    _seed_pattern(session, table, column_name="misc", filter_count=100)

    rec = analyze_partition_evolution(session, table.id, min_query_threshold=1)

    assert rec is not None
    assert rec.cardinality_estimate == "unknown"
    assert rec.warnings
    assert rec.confidence < 0.95


def test_granularity_mismatch_recommends_finer_transform(session) -> None:
    table = _seed_table(session, table_name="events")
    _seed_stats(
        session,
        table,
        partition_spec=[{"name": "event_date", "transform": "year"}],
        total_size_gb=365.0,  # ~1GB/day after switching to day -- comfortably above the floor
        total_partitions=5,
    )
    # Dominant filter column is already the partitioned column (so case 1 doesn't fire);
    # a separate, lightly-used "event_day"-named pattern carries the day-level range-filter
    # signal that should trigger the granularity-mismatch check instead.
    _seed_pattern(
        session, table, column_name="event_date", filter_count=900, equality_filter_count=900
    )
    _seed_pattern(session, table, column_name="event_day", filter_count=10, range_filter_count=900)

    rec = analyze_partition_evolution(session, table.id, min_query_threshold=1)

    assert rec is not None
    assert "day" in rec.recommended_partition_spec.lower()
    assert "year" not in rec.recommended_partition_spec.lower()


def test_granularity_mismatch_skipped_when_data_volume_too_small(session) -> None:
    table = _seed_table(session, table_name="tiny_events")
    _seed_stats(
        session,
        table,
        partition_spec=[{"name": "event_date", "transform": "year"}],
        total_size_gb=0.5,  # switching to day would produce far-below-floor partitions
        total_partitions=2,
    )
    _seed_pattern(
        session, table, column_name="event_date", filter_count=900, equality_filter_count=900
    )
    _seed_pattern(session, table, column_name="event_day", filter_count=10, range_filter_count=900)

    rec = analyze_partition_evolution(session, table.id, min_query_threshold=1)

    assert rec is None


def test_no_recommendation_when_already_well_partitioned(session) -> None:
    table = _seed_table(session, table_name="well_partitioned")
    _seed_stats(
        session,
        table,
        partition_spec=[{"name": "region", "transform": "identity"}],
        total_size_gb=50.0,
    )
    _seed_pattern(session, table, column_name="region", filter_count=900, equality_filter_count=900)
    _seed_pattern(session, table, column_name="misc", filter_count=100)

    rec = analyze_partition_evolution(session, table.id, min_query_threshold=1)

    assert rec is None


def test_returns_none_below_min_query_threshold(session) -> None:
    table = _seed_table(session, table_name="quiet_table")
    _seed_stats(session, table, partition_spec=[], total_size_gb=50.0)
    _seed_pattern(session, table, column_name="order_id", filter_count=50, equality_filter_count=50)

    rec = analyze_partition_evolution(session, table.id, min_query_threshold=100)

    assert rec is None


def test_scan_ratio_trigger_when_filter_pct_below_eighty(session) -> None:
    """PDAE-8357-shaped: scans are large even though no column dominates at >=80%."""
    table = _seed_table(session, table_name="pdae_events")
    total_size_gb = 10.0
    _seed_stats(
        session,
        table,
        partition_spec=[{"name": "event_date", "transform": "day"}],
        total_size_gb=total_size_gb,
    )
    _seed_pattern(
        session,
        table,
        column_name="user_id",
        filter_count=35,
        equality_filter_count=35,
        avg_selectivity=0.4,
    )
    _seed_pattern(session, table, column_name="misc_a", filter_count=33)
    _seed_pattern(session, table, column_name="misc_b", filter_count=32)
    table_bytes = int(total_size_gb * (1024**3))
    _seed_query_logs(session, table, n=12, bytes_read=int(table_bytes * 0.20))

    rec = analyze_partition_evolution(session, table.id, min_query_threshold=1)

    assert rec is not None
    assert rec.trigger == "scan_ratio"
    assert rec.scan_ratio is not None
    assert rec.scan_ratio > 0.10
    assert rec.avg_selectivity == 0.4
    assert "user_id" in rec.recommended_partition_spec


def test_bucket_too_coarse_recommends_larger_n(session) -> None:
    table = _seed_table(session, table_name="coarse_buckets")
    total_size_gb = 37.0
    _seed_stats(
        session,
        table,
        partition_spec=[{"name": "user_id", "transform": "bucket[64]"}],
        total_size_gb=total_size_gb,
    )

    rec = analyze_partition_evolution(session, table.id, min_query_threshold=100)

    expected_n = bucket_count_for(total_size_gb)
    assert expected_n == 128
    assert rec is not None
    assert rec.trigger == "bucket_sizing"
    assert str(expected_n) in rec.recommended_partition_spec
    assert expected_n >= 64 * 2


def test_healthy_bucket_count_returns_none(session) -> None:
    table = _seed_table(session, table_name="healthy_buckets")
    total_size_gb = 32.0
    n = bucket_count_for(total_size_gb)
    _seed_stats(
        session,
        table,
        partition_spec=[{"name": "user_id", "transform": f"bucket[{n}]"}],
        total_size_gb=total_size_gb,
    )

    rec = analyze_partition_evolution(session, table.id, min_query_threshold=100)

    assert rec is None


def test_bucket_too_fine_recommends_smaller_n(session) -> None:
    table = _seed_table(session, table_name="fine_buckets")
    total_size_gb = 1.0
    _seed_stats(
        session,
        table,
        partition_spec=[{"name": "user_id", "transform": "bucket[512]"}],
        total_size_gb=total_size_gb,
    )

    rec = analyze_partition_evolution(session, table.id, min_query_threshold=100)

    expected_n = bucket_count_for(total_size_gb)
    assert rec is not None
    assert rec.trigger == "bucket_sizing"
    assert "too fine" in rec.reason.lower()
    assert str(expected_n) in rec.recommended_partition_spec
    assert expected_n < 512
