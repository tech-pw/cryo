"""Tests for AutoComp-style compaction candidate ranking, including the scan-cost signal."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import QueryLog, Source, Table, TablePartition
from cryo.scanner.compaction_candidates import find_candidates


def _seed_source(session) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={},
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


def _seed_partition(session, table: Table, *, small_files: int, total_files: int) -> None:
    session.add(
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-01"},
            partition_fields=[],
            partition_value="2024-01-01",
            partition_path="dt=2024-01-01",
            partition_size_gb=10.0,
            small_files_in_partition_count=small_files,
            files_per_partition=total_files,
            scan_at=datetime.now(UTC),
        )
    )


def _seed_queries(session, table: Table, *, bytes_read: int, count: int = 10) -> None:
    source = session.get(Source, table.source_id)
    for i in range(count):
        session.add(
            QueryLog(
                source_id=source.id,
                table_id=table.id,
                query_id=f"{table.table_name}-query-{i}",
                engine="trino",
                query_text=f"SELECT * FROM {table.schema_name}.{table.table_name}",
                executed_at=datetime.now(UTC) - timedelta(hours=i),
                duration_ms=1000,
                bytes_read=bytes_read,
            )
        )


def test_find_candidates_ranks_expensive_scans_above_cheap_ones(session):
    """The core regression this feature exists to fix: two tables with identical fragmentation
    and identical query *counts* must not tie in find_candidates' actual ranking (candidates
    sort by `.score`, not just the HIGH/MED/LOW `.priority` label) when one's queries scan far
    more bytes per hit -- the real Trino cost signal."""
    cheap_table = _seed_table(session, schema_name="test", table_name="cheap_scans")
    expensive_table = _seed_table(session, schema_name="test", table_name="expensive_scans")

    _seed_partition(session, cheap_table, small_files=100, total_files=100)
    _seed_partition(session, expensive_table, small_files=100, total_files=100)

    _seed_queries(session, cheap_table, bytes_read=10 * 1024 * 1024)  # 10MB/query
    _seed_queries(session, expensive_table, bytes_read=50 * 1024 * 1024 * 1024)  # 50GB/query
    session.commit()

    cheap_candidates = find_candidates(session, cheap_table.id)
    expensive_candidates = find_candidates(session, expensive_table.id)

    assert len(cheap_candidates) == 1
    assert len(expensive_candidates) == 1
    # Same query_freq_per_day (both seeded with 10 queries in-window) -- score must still diverge.
    assert cheap_candidates[0].query_freq_per_day == expensive_candidates[0].query_freq_per_day
    assert expensive_candidates[0].score > cheap_candidates[0].score
    assert expensive_candidates[0].priority == "HIGH"


def test_find_candidates_reason_surfaces_high_scan_cost(session):
    table = _seed_table(session, schema_name="test", table_name="hot_scan_table")
    _seed_partition(session, table, small_files=100, total_files=100)
    _seed_queries(session, table, bytes_read=20 * 1024 * 1024 * 1024)  # 20GB/query
    session.commit()

    candidates = find_candidates(session, table.id)
    assert len(candidates) == 1
    assert any("scan cost" in reason for reason in candidates[0].reasons)


def test_find_candidates_unaffected_when_no_query_log_bytes(session):
    """No QueryLog rows at all -- avg_bytes_per_query resolves to None, and find_candidates
    must behave exactly as it did before this feature (no crash, neutral scoring)."""
    table = _seed_table(session, schema_name="test", table_name="no_query_log")
    _seed_partition(session, table, small_files=100, total_files=100)
    session.commit()

    candidates = find_candidates(session, table.id)
    assert len(candidates) == 1
    assert candidates[0].score >= 0.0
    assert not any("scan cost" in reason for reason in candidates[0].reasons)
