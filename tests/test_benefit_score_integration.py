"""Integration tests for AutoComp benefit scoring."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from cryo.db.models import QueryLog, Table, TablePartition, TableStats
from cryo.planner.benefit_score import (
    compute_benefit_score,
    get_compaction_effectiveness_stats,
    get_top_compaction_candidates,
    update_partition_benefit_scores,
)


def test_compute_benefit_score():
    """Test benefit score calculation."""
    # High-value case: many small files, high query frequency, small partition
    score = compute_benefit_score(
        small_files=100,
        query_frequency=50.0,  # 50 queries/day
        partition_size_gb=10.0,
    )
    assert score > 100.0  # Should be high priority
    
    # Low-value case: few small files, low query frequency
    score = compute_benefit_score(
        small_files=10,
        query_frequency=1.0,
        partition_size_gb=10.0,
    )
    assert score < 10.0  # Should be low priority
    
    # Edge case: zero small files
    score = compute_benefit_score(
        small_files=0,
        query_frequency=100.0,
        partition_size_gb=1.0,
    )
    assert score == 0.0


def test_update_partition_benefit_scores(session):
    """Test updating benefit scores for all partitions of a table."""
    # Create test table
    table = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.compaction_table",
        location="s3://test/table",
        is_active=True,
    )
    session.add(table)
    
    # Create table stats
    stats = TableStats(
        table_id=table.id,
        total_size_gb=100.0,
        small_files_count=500,
    )
    session.add(stats)
    
    # Create partitions with varying small file counts
    scan_at = datetime.now(UTC)
    partitions = [
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-01"},
            partition_value="2024-01-01",
            partition_size_gb=10.0,
            small_files_in_partition_count=100,
            scan_at=scan_at,
        ),
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-02"},
            partition_value="2024-01-02",
            partition_size_gb=5.0,
            small_files_in_partition_count=50,
            scan_at=scan_at,
        ),
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-03"},
            partition_value="2024-01-03",
            partition_size_gb=20.0,
            small_files_in_partition_count=200,
            scan_at=scan_at,
        ),
    ]
    session.add_all(partitions)
    
    # Add query logs
    for i in range(20):
        log = QueryLog(
            id=uuid4(),
            table_id=table.id,
            query_text="SELECT * FROM test.compaction_table",
            engine="trino",
            executed_at=datetime.now(UTC) - timedelta(hours=i),
            execution_time_ms=1000,
            tables_accessed=[str(table.id)],
        )
        session.add(log)
    
    session.commit()
    
    # Update benefit scores
    updated = update_partition_benefit_scores(session, table.id)
    assert updated == 3
    
    # Verify scores were set
    updated_partitions = session.scalars(
        select(TablePartition).where(TablePartition.table_id == table.id)
    ).all()
    
    for p in updated_partitions:
        assert p.compaction_benefit_score is not None
        assert p.compaction_benefit_score > 0
        assert p.query_frequency is not None


def test_get_top_compaction_candidates(session):
    """Test getting top compaction candidates by benefit score."""
    table = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.ranked_table",
        location="s3://test/ranked",
        is_active=True,
    )
    session.add(table)
    
    scan_at = datetime.now(UTC)
    
    # Create partitions with pre-computed benefit scores
    partitions = [
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-01"},
            partition_value="2024-01-01",
            partition_size_gb=10.0,
            small_files_in_partition_count=100,
            compaction_benefit_score=50.0,  # High score
            scan_at=scan_at,
        ),
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-02"},
            partition_value="2024-01-02",
            partition_size_gb=5.0,
            small_files_in_partition_count=50,
            compaction_benefit_score=5.0,  # Low score
            scan_at=scan_at,
        ),
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-03"},
            partition_value="2024-01-03",
            partition_size_gb=20.0,
            small_files_in_partition_count=200,
            compaction_benefit_score=100.0,  # Highest score
            scan_at=scan_at,
        ),
    ]
    session.add_all(partitions)
    session.commit()
    
    # Get top candidates with min_score=10
    candidates = get_top_compaction_candidates(
        session,
        table.id,
        min_score=10.0,
        limit=10,
    )
    
    # Should return 2 partitions (score 50 and 100, not 5)
    assert len(candidates) == 2
    
    # Should be ordered by score descending
    assert candidates[0].compaction_benefit_score == 100.0
    assert candidates[1].compaction_benefit_score == 50.0


def test_compaction_effectiveness_stats(session):
    """Test compaction effectiveness ratio calculation."""
    from cryo.db.models import AuditLog
    
    table = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.effectiveness_table",
        location="s3://test/effectiveness",
        is_active=True,
    )
    session.add(table)
    session.commit()
    
    # Add audit logs with varying effectiveness
    now = datetime.now(UTC)
    audit_logs = [
        AuditLog(
            table_id=table.id,
            action="optimize",
            status="ok",
            input_files_count=100,
            output_files_count=2,  # 50:1 ratio (excellent)
            ts=now - timedelta(days=1),
            duration_ms=60000,
        ),
        AuditLog(
            table_id=table.id,
            action="optimize",
            status="ok",
            input_files_count=50,
            output_files_count=1,  # 50:1 ratio (excellent)
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
    # Create table with small number of small files
    table = Table(
        id=uuid4(),
        source_id=uuid4(),
        name="test.premature_table",
        location="s3://test/premature",
        is_active=True,
    )
    session.add(table)
    
    scan_at = datetime.now(UTC)
    partition = TablePartition(
        table_id=table.id,
        partition_values={"dt": "2024-01-01"},
        partition_value="2024-01-01",
        partition_size_gb=50.0,  # Large partition
        small_files_in_partition_count=5,  # Very few small files
        scan_at=scan_at,
    )
    session.add(partition)
    session.commit()
    
    # Update benefit scores with no queries
    updated = update_partition_benefit_scores(session, table.id)
    assert updated == 1
    
    # Verify score is very low
    updated_partition = session.scalar(
        select(TablePartition).where(
            TablePartition.table_id == table.id,
            TablePartition.partition_value == "2024-01-01",
        )
    )
    
    assert updated_partition.compaction_benefit_score is not None
    # Low files + low query frequency + large size = very low score
    assert updated_partition.compaction_benefit_score < 1.0
    
    # Should not be returned as a top candidate
    candidates = get_top_compaction_candidates(
        session,
        table.id,
        min_score=1.0,
        limit=10,
    )
    assert len(candidates) == 0
