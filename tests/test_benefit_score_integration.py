"""Integration tests for AutoComp benefit scoring."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from cryo.db.models import AuditLog, QueryLog, Source, Table, TablePartition, TableStats
from cryo.planner.benefit_score import (
    compute_benefit_score,
    get_compaction_effectiveness_stats,
    get_top_compaction_candidates,
    update_all_benefit_scores,
    update_partition_benefit_scores,
)


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


def test_compute_benefit_score():
    """Test benefit score calculation."""
    score = compute_benefit_score(
        small_files=100,
        query_frequency=50.0,
        partition_size_gb=10.0,
    )
    assert score > 100.0

    score = compute_benefit_score(
        small_files=10,
        query_frequency=1.0,
        partition_size_gb=10.0,
    )
    assert score < 10.0

    score = compute_benefit_score(
        small_files=0,
        query_frequency=100.0,
        partition_size_gb=1.0,
    )
    assert score == 0.0


def test_update_partition_benefit_scores(session):
    """Test updating benefit scores for all partitions of a table."""
    table = _seed_table(session, schema_name="test", table_name="compaction_table")

    stats = TableStats(
        table_id=table.id,
        scan_at=datetime.now(UTC),
        needs_compaction=True,
        compaction_priority="HIGH",
        grade="C",
        overall_score=50,
        total_files=500,
        small_files_count=500,
        total_size_gb=100.0,
    )
    session.add(stats)

    scan_at = datetime.now(UTC)
    partitions = [
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-01"},
            partition_fields=[],
            partition_value="2024-01-01",
            partition_size_gb=10.0,
            small_files_in_partition_count=100,
            scan_at=scan_at,
        ),
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-02"},
            partition_fields=[],
            partition_value="2024-01-02",
            partition_size_gb=5.0,
            small_files_in_partition_count=50,
            scan_at=scan_at,
        ),
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-03"},
            partition_fields=[],
            partition_value="2024-01-03",
            partition_size_gb=20.0,
            small_files_in_partition_count=200,
            scan_at=scan_at,
        ),
    ]
    session.add_all(partitions)

    source = session.get(Source, table.source_id)
    for i in range(20):
        log = QueryLog(
            source_id=source.id,
            table_id=table.id,
            query_id=f"query-{i}",
            engine="trino",
            query_text="SELECT * FROM test.compaction_table",
            executed_at=datetime.now(UTC) - timedelta(hours=i),
            duration_ms=1000,
        )
        session.add(log)

    session.commit()

    updated = update_partition_benefit_scores(session, table.id)
    assert updated == 3

    updated_partitions = session.scalars(
        select(TablePartition).where(TablePartition.table_id == table.id)
    ).all()

    for partition in updated_partitions:
        assert partition.compaction_benefit_score is not None
        assert partition.compaction_benefit_score > 0
        assert partition.query_frequency is not None


def test_update_all_benefit_scores_covers_every_active_table(session):
    """update_all_benefit_scores was rewritten to loop over plain table ids and expunge the
    session's identity map after each table (see its docstring) -- this pins the actual
    functional contract (every active table's partitions get scored) that refactor must not
    break, independent of the memory-management mechanics."""
    table_a = _seed_table(session, schema_name="test", table_name="table_a")
    table_b = _seed_table(session, schema_name="test", table_name="table_b")
    scan_at = datetime.now(UTC)
    session.add_all(
        [
            TablePartition(
                table_id=table_a.id,
                partition_values={"dt": "2024-01-01"},
                partition_fields=[],
                partition_value="2024-01-01",
                partition_size_gb=10.0,
                small_files_in_partition_count=100,
                scan_at=scan_at,
            ),
            TablePartition(
                table_id=table_b.id,
                partition_values={"dt": "2024-01-01"},
                partition_fields=[],
                partition_value="2024-01-01",
                partition_size_gb=5.0,
                small_files_in_partition_count=50,
                scan_at=scan_at,
            ),
        ]
    )
    session.commit()

    total = update_all_benefit_scores(session)

    assert total == 2
    partitions = session.scalars(select(TablePartition)).all()
    assert len(partitions) == 2
    for partition in partitions:
        assert partition.compaction_benefit_score is not None


def test_update_all_benefit_scores_expunges_session_after_each_table(session):
    """Regression test for the OOM that was killing the scheduler process every ~20-40min in
    production: the old version looped over Table ORM objects held in the session's identity
    map for the entire run, and commit() doesn't clear that map -- across a large fleet (13k+
    tables, up to 10k partitions each) that unbounded accumulation, not any single table's own
    work, was the actual memory growth. Confirms objects are genuinely detached, not just that
    the function still returns the right count."""
    table = _seed_table(session, schema_name="test", table_name="expunge_check")
    scan_at = datetime.now(UTC)
    partition = TablePartition(
        table_id=table.id,
        partition_values={"dt": "2024-01-01"},
        partition_fields=[],
        partition_value="2024-01-01",
        partition_size_gb=10.0,
        small_files_in_partition_count=100,
        scan_at=scan_at,
    )
    session.add(partition)
    session.commit()

    update_all_benefit_scores(session)

    assert table not in session
    assert partition not in session


def test_get_top_compaction_candidates(session):
    """Test getting top compaction candidates by benefit score."""
    table = _seed_table(session, schema_name="test", table_name="ranked_table")
    scan_at = datetime.now(UTC)

    partitions = [
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-01"},
            partition_fields=[],
            partition_value="2024-01-01",
            partition_size_gb=10.0,
            small_files_in_partition_count=100,
            compaction_benefit_score=50.0,
            scan_at=scan_at,
        ),
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-02"},
            partition_fields=[],
            partition_value="2024-01-02",
            partition_size_gb=5.0,
            small_files_in_partition_count=50,
            compaction_benefit_score=5.0,
            scan_at=scan_at,
        ),
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-03"},
            partition_fields=[],
            partition_value="2024-01-03",
            partition_size_gb=20.0,
            small_files_in_partition_count=200,
            compaction_benefit_score=100.0,
            scan_at=scan_at,
        ),
    ]
    session.add_all(partitions)
    session.commit()

    candidates = get_top_compaction_candidates(
        session,
        table.id,
        min_score=10.0,
        limit=10,
    )

    assert len(candidates) == 2
    assert candidates[0].compaction_benefit_score == 100.0
    assert candidates[1].compaction_benefit_score == 50.0


def test_get_top_compaction_candidates_ignores_stale_scans(session):
    """table_partitions is append-only (one row per partition per scan). A partition that scored
    high in an older scan must not show up as a duplicate "different" candidate alongside its
    current-scan row -- only the latest scan's rows should ever be ranked."""
    table = _seed_table(session, schema_name="test", table_name="stale_scan_table")
    older_scan = datetime.now(UTC) - timedelta(days=1)
    latest_scan = datetime.now(UTC)

    session.add_all(
        [
            TablePartition(
                table_id=table.id,
                partition_values={"dt": "2024-01-01"},
                partition_fields=[],
                partition_value="2024-01-01",
                partition_size_gb=10.0,
                small_files_in_partition_count=100,
                compaction_benefit_score=90.0,
                scan_at=older_scan,
            ),
            TablePartition(
                table_id=table.id,
                partition_values={"dt": "2024-01-01"},
                partition_fields=[],
                partition_value="2024-01-01",
                partition_size_gb=10.0,
                small_files_in_partition_count=100,
                compaction_benefit_score=95.0,
                scan_at=latest_scan,
            ),
        ]
    )
    session.commit()

    candidates = get_top_compaction_candidates(session, table.id, min_score=10.0, limit=10)

    assert len(candidates) == 1
    assert candidates[0].compaction_benefit_score == 95.0


def test_compaction_effectiveness_stats(session):
    """Test compaction effectiveness ratio calculation."""
    table = _seed_table(session, schema_name="test", table_name="effectiveness_table")
    session.commit()

    now = datetime.now(UTC)
    audit_logs = [
        AuditLog(
            table_id=table.id,
            action="optimize",
            status="ok",
            input_files_count=100,
            output_files_count=2,
            ts=now - timedelta(days=1),
            duration_ms=60000,
        ),
        AuditLog(
            table_id=table.id,
            action="optimize",
            status="ok",
            input_files_count=50,
            output_files_count=1,
            ts=now - timedelta(days=2),
            duration_ms=30000,
        ),
    ]
    session.add_all(audit_logs)
    session.commit()

    stats = get_compaction_effectiveness_stats(session, table.id, lookback_days=30)

    assert stats["total_input_files"] == 150
    assert stats["total_output_files"] == 3
    assert stats["run_count"] == 2
    assert stats["effectiveness_ratio"] == 50.0
    assert stats["effectiveness"] == "excellent"


def test_benefit_score_prevents_premature_compaction(session):
    """Test that low benefit scores prevent wasteful compaction."""
    table = _seed_table(session, schema_name="test", table_name="premature_table")

    scan_at = datetime.now(UTC)
    partition = TablePartition(
        table_id=table.id,
        partition_values={"dt": "2024-01-01"},
        partition_fields=[],
        partition_value="2024-01-01",
        partition_size_gb=50.0,
        small_files_in_partition_count=5,
        scan_at=scan_at,
    )
    session.add(partition)
    session.commit()

    updated = update_partition_benefit_scores(session, table.id)
    assert updated == 1

    updated_partition = session.scalar(
        select(TablePartition).where(
            TablePartition.table_id == table.id,
            TablePartition.partition_value == "2024-01-01",
        )
    )

    assert updated_partition.compaction_benefit_score is not None
    assert updated_partition.compaction_benefit_score < 1.0

    candidates = get_top_compaction_candidates(
        session,
        table.id,
        min_score=1.0,
        limit=10,
    )
    assert len(candidates) == 0
