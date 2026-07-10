"""Tests for GET /tables/group-by -- the Tables page's group-by summary view.

Calls the route handler function directly (same convention used throughout this test suite for
route logic that doesn't need the full HTTP/dependency-injection stack), seeding Source/Table/
TableStats rows and asserting the grouped counts/totals.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from cryo.api.routes import group_tables
from cryo.db.models import Source, Table, TableStats


def _seed_source(session, *, name: str) -> Source:
    source = Source(name=name, type="hive", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(
    session,
    source: Source,
    *,
    name: str,
    grade: str,
    priority: str = "LOW",
    needs_compaction: bool = False,
    total_size_gb: float = 5.0,
    small_files_count: int = 10,
    savings: float = 0.0,
) -> Table:
    table = Table(source_id=source.id, schema_name="finance", table_name=name, is_active=True)
    session.add(table)
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=needs_compaction,
            compaction_priority=priority,
            grade=grade,
            overall_score=80,
            total_files=100,
            small_files_count=small_files_count,
            total_size_gb=total_size_gb,
            projected_savings_usd_month=savings,
        )
    )
    session.commit()
    return table


def test_group_by_grade_counts_and_sums_per_group(session) -> None:
    source = _seed_source(session, name="cdp")
    _seed_table(session, source, name="a", grade="A", total_size_gb=10, savings=0)
    _seed_table(session, source, name="b", grade="A", total_size_gb=20, savings=0)
    _seed_table(session, source, name="c", grade="D", total_size_gb=5, savings=50)

    result = group_tables(session, field="grade")

    by_grade = {row.group_value: row for row in result.rows}
    assert by_grade["A"].count == 2
    assert by_grade["A"].total_size_gb == 30
    assert by_grade["D"].count == 1
    assert by_grade["D"].total_savings_usd_month == 50


def test_group_by_catalog_groups_across_sources(session) -> None:
    source_a = _seed_source(session, name="cdp")
    source_b = _seed_source(session, name="analytics")
    _seed_table(session, source_a, name="a", grade="A")
    _seed_table(session, source_a, name="b", grade="B")
    _seed_table(session, source_b, name="c", grade="A")

    result = group_tables(session, field="catalog")

    by_catalog = {row.group_value: row.count for row in result.rows}
    assert by_catalog == {"cdp": 2, "analytics": 1}


def test_group_by_needs_compaction_splits_true_false(session) -> None:
    source = _seed_source(session, name="cdp")
    _seed_table(session, source, name="a", grade="A", needs_compaction=True)
    _seed_table(session, source, name="b", grade="A", needs_compaction=False)

    result = group_tables(session, field="needs_compaction")

    assert sorted(row.count for row in result.rows) == [1, 1]


def test_group_by_size_bucket_buckets_correctly(session) -> None:
    source = _seed_source(session, name="cdp")
    _seed_table(session, source, name="tiny", grade="A", total_size_gb=0.5)
    _seed_table(session, source, name="mid", grade="A", total_size_gb=50)
    _seed_table(session, source, name="huge", grade="A", total_size_gb=2000)

    result = group_tables(session, field="size_bucket")

    buckets = {row.group_value for row in result.rows}
    assert "< 1 GB" in buckets
    assert "10-100 GB" in buckets
    assert "> 1 TB" in buckets


def test_group_by_includes_tables_with_no_stats_yet(session) -> None:
    source = _seed_source(session, name="cdp")
    unscanned = Table(
        source_id=source.id, schema_name="finance", table_name="fresh", is_active=True
    )
    session.add(unscanned)
    session.commit()

    result = group_tables(session, field="grade")

    unscanned_row = next(row for row in result.rows if row.group_value is None)
    assert unscanned_row.count == 1
    assert unscanned_row.total_size_gb == 0


def test_group_by_respects_filter_predicate(session) -> None:
    source = _seed_source(session, name="cdp")
    _seed_table(session, source, name="a", grade="A")
    _seed_table(session, source, name="b", grade="D")

    result = group_tables(
        session, field="grade", filter='{"field": "grade", "op": "=", "value": "A"}'
    )

    assert [row.group_value for row in result.rows] == ["A"]


def test_group_by_rejects_unsupported_field(session) -> None:
    with pytest.raises(HTTPException) as exc_info:
        group_tables(session, field="total_files")

    assert exc_info.value.status_code == 400
