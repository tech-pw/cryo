"""Move 20: Bloom filter column recommendations from query patterns.

Verifies that ``_recommend_bloom_filters`` lifts equality-filter-heavy,
high-cardinality ``QueryPattern`` columns into ``BloomFilterRecommendation``
records carrying the Spark ``ALTER TABLE ... SET TBLPROPERTIES`` DDL.

The full ``analyze_table_layout`` / ``generate_layout_recommendation`` paths
require Postgres for ``jsonb_array_length`` (used by
``get_table_query_patterns``); they're exercised under the PostgreSQL CI
job rather than sqlite-in-memory here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.db.models import QueryPattern, Source, Table
from cryo.optimizer.layout_analyzer import _recommend_bloom_filters


def _seed_source(session) -> Source:
    source = Source(
        id=uuid.uuid4(),
        name=f"source-{uuid.uuid4()}",
        type="rest",
        config={"catalog_name": "lake"},
    )
    session.add(source)
    session.flush()
    return source


def _seed_table(session, *, table_name: str) -> Table:
    source = _seed_source(session)
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.flush()
    return table


def _seed_pattern(
    session,
    table: Table,
    *,
    column_name: str,
    filter_count: int,
    equality_filter_count: int = 0,
    range_filter_count: int = 0,
    join_count: int = 0,
) -> QueryPattern:
    pattern = QueryPattern(
        table_id=table.id,
        column_name=column_name,
        filter_count=filter_count,
        equality_filter_count=equality_filter_count,
        range_filter_count=range_filter_count,
        join_count=join_count,
        last_seen_at=datetime.now(UTC),
    )
    session.add(pattern)
    session.flush()
    return pattern


def test_bloom_filters_recommend_high_cardinality_id_columns(session) -> None:
    """A column with many equality filters and id-shaped name becomes a rec."""
    table = _seed_table(session, table_name="orders")
    _seed_pattern(
        session,
        table,
        column_name="customer_id",
        filter_count=200,
        equality_filter_count=180,
        range_filter_count=20,
        join_count=40,
    )

    recs = _recommend_bloom_filters(session, table.id, "analytics", "orders")

    assert len(recs) >= 1
    assert recs[0].column_name == "customer_id"
    assert recs[0].cardinality_estimate == "high"
    assert "bloom-filter-enabled.column.customer_id" in recs[0].spark_alter_statement


def test_bloom_filters_skip_low_cardinality_columns(session) -> None:
    """A status/region/type column does not get a Bloom filter rec."""
    table = _seed_table(session, table_name="shipments")
    _seed_pattern(
        session,
        table,
        column_name="region",
        filter_count=500,
        equality_filter_count=500,
    )

    recs = _recommend_bloom_filters(session, table.id, "analytics", "shipments")

    assert [r.column_name for r in recs] == []


def test_bloom_filters_skip_below_min_equality_count(session) -> None:
    """Five-or-more equality filters is the floor; below that → no rec."""
    table = _seed_table(session, table_name="scans")
    _seed_pattern(
        session,
        table,
        column_name="customer_id",
        filter_count=10,
        equality_filter_count=3,
        join_count=20,
    )

    recs = _recommend_bloom_filters(session, table.id, "analytics", "scans")

    assert recs == []


def test_bloom_filters_skip_when_equality_ratio_below_threshold(session) -> None:
    """Columns that are mostly range-filtered (not equality) are skipped."""
    table = _seed_table(session, table_name="metrics")
    _seed_pattern(
        session,
        table,
        column_name="order_id",
        filter_count=200,
        equality_filter_count=50,
        range_filter_count=150,
    )

    recs = _recommend_bloom_filters(session, table.id, "analytics", "metrics")

    assert recs == []


def test_bloom_filters_returns_empty_when_no_query_patterns(session) -> None:
    """No telemetry → no recommendation, not an exception."""
    table = _seed_table(session, table_name="fresh")

    recs = _recommend_bloom_filters(session, table.id, "analytics", "fresh")

    assert recs == []


def test_spark_alter_targets_correct_catalog_schema_table(session) -> None:
    """The emitted DDL uses the table's catalog.schema.table reference."""
    table = _seed_table(session, table_name="events")
    _seed_pattern(
        session,
        table,
        column_name="order_uuid",
        filter_count=400,
        equality_filter_count=380,
        range_filter_count=20,
        join_count=100,
    )

    recs = _recommend_bloom_filters(session, table.id, "analytics", "events")

    assert len(recs) == 1
    stmt = recs[0].spark_alter_statement
    assert stmt.startswith("ALTER TABLE iceberg.analytics.events SET TBLPROPERTIES")
    assert "order_uuid" in stmt

