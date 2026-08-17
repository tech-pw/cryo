"""Integration tests for AutoComp benefit scoring."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from cryo.db.models import AuditLog, QueryLog, Source, Table, TablePartition, TableStats
from cryo.planner.benefit_score import (
    DEFAULT_MOOP_WEIGHTS,
    compute_benefit_score,
    compute_moop_scores,
    get_compaction_effectiveness_stats,
    get_top_compaction_candidates,
    update_all_benefit_scores,
    update_partition_benefit_scores,
    update_table_effectiveness,
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


def test_compute_benefit_score_scan_cost_multiplier_is_neutral_when_unknown():
    """avg_bytes_per_query defaults to None -- every pre-existing caller that never learned
    about this parameter must keep scoring exactly as before it existed."""
    without_signal = compute_benefit_score(
        small_files=100, query_frequency=10.0, partition_size_gb=5.0
    )
    with_none = compute_benefit_score(
        small_files=100, query_frequency=10.0, partition_size_gb=5.0, avg_bytes_per_query=None
    )
    assert without_signal == with_none


def test_compute_benefit_score_rewards_tables_with_expensive_scans():
    """Two tables, identical fragmentation and identical query *count* -- the one whose queries
    actually scan more bytes per hit (the real Trino cost signal) must score higher. This is
    the exact gap a count-only query_frequency term can't see."""
    cheap_queries = compute_benefit_score(
        small_files=200,
        query_frequency=20.0,
        partition_size_gb=10.0,
        avg_bytes_per_query=10 * 1024 * 1024,  # 10MB/query -- cheap
    )
    expensive_queries = compute_benefit_score(
        small_files=200,
        query_frequency=20.0,
        partition_size_gb=10.0,
        avg_bytes_per_query=50 * 1024 * 1024 * 1024,  # 50GB/query -- real Trino cost driver
    )
    assert expensive_queries > cheap_queries


def test_compute_benefit_score_scan_cost_multiplier_is_bounded():
    """An extreme outlier (real sample: one query scanned 525GB) shouldn't let scan cost alone
    drown out every other trait -- see _MAX_BYTES_MULTIPLIER's rationale in benefit_score.py."""
    from cryo.planner.benefit_score import _MAX_BYTES_MULTIPLIER, _scan_cost_multiplier

    huge = _scan_cost_multiplier(525 * 1024 * 1024 * 1024)  # 525GB, the real observed outlier
    assert huge == _MAX_BYTES_MULTIPLIER

    tiny = _scan_cost_multiplier(1)  # 1 byte -- degenerate small end
    assert tiny > 0.0  # never suppresses the score to zero on its own


def test_compute_benefit_score_scan_cost_multiplier_zero_or_negative_is_neutral():
    from cryo.planner.benefit_score import _scan_cost_multiplier

    assert _scan_cost_multiplier(0) == 1.0
    assert _scan_cost_multiplier(-5.0) == 1.0


def test_compute_moop_scores_empty_batch():
    assert compute_moop_scores([]) == []


def test_compute_moop_scores_ranks_small_files_and_frequency_up_size_down():
    candidates = [
        {"small_files": 500, "size_gb": 5.0, "query_frequency": 20.0},  # best on 3 traits
        {"small_files": 10, "size_gb": 100.0, "query_frequency": 0.0},  # worst on 3 traits
    ]
    traits = compute_moop_scores(candidates)
    assert len(traits) == 2
    assert traits[0].s_c > traits[1].s_c
    assert traits[0].t1 == 1.0  # most small files in this batch -> max-normalized to 1.0
    assert traits[0].t2 == 0.0  # smallest (cheapest) size -> min-normalized to 0.0
    assert traits[1].t1 == 0.0
    assert traits[1].t2 == 1.0


def test_compute_moop_scores_all_tied_candidates_get_neutral_traits_not_zero():
    # Every candidate identical on every trait -- min==max, so normalization is undefined; must
    # not silently read as "worst on every trait" (0.0) for an entire uniform batch.
    candidates = [{"small_files": 50, "size_gb": 5.0, "query_frequency": 2.0} for _ in range(3)]
    traits = compute_moop_scores(candidates)
    for t in traits:
        assert t.t1 == 0.5
        assert t.t2 == 0.5
        assert t.t4 == 0.5


def test_compute_moop_scores_t4_unaffected_when_avg_bytes_per_query_absent():
    """No candidate carries avg_bytes_per_query -- T4 (and therefore s_c) must be identical to
    before this parameter existed, same guarantee as compute_benefit_score's own None default."""
    candidates_without_key = [
        {"small_files": 500, "size_gb": 5.0, "query_frequency": 20.0},
        {"small_files": 10, "size_gb": 100.0, "query_frequency": 5.0},
    ]
    candidates_with_none = [{**c, "avg_bytes_per_query": None} for c in candidates_without_key]
    assert compute_moop_scores(candidates_without_key) == compute_moop_scores(
        candidates_with_none
    )


def test_compute_moop_scores_rewards_expensive_scans_via_t4():
    """Two candidates, identical query_frequency -- the one whose queries actually scan more
    bytes per hit must score higher on T4 (query affinity) and therefore s_c, the score
    job_store._table_autocomp_summary and planner._compare_candidates actually dispatch on."""
    candidates = [
        {
            "small_files": 100,
            "size_gb": 10.0,
            "query_frequency": 20.0,
            "avg_bytes_per_query": 10 * 1024 * 1024,  # 10MB/query -- cheap
        },
        {
            "small_files": 100,
            "size_gb": 10.0,
            "query_frequency": 20.0,
            "avg_bytes_per_query": 50 * 1024 * 1024 * 1024,  # 50GB/query -- expensive
        },
    ]
    traits = compute_moop_scores(candidates)
    cheap, expensive = traits
    assert expensive.t4 > cheap.t4
    assert expensive.s_c > cheap.s_c
    # T1/T2 (file count, size) are identical between the two -- only T4 should diverge.
    assert expensive.t1 == cheap.t1
    assert expensive.t2 == cheap.t2


def test_compute_moop_scores_without_file_entropy_redistributes_its_weight():
    weights = DEFAULT_MOOP_WEIGHTS
    candidates = [
        {"small_files": 500, "size_gb": 1.0, "query_frequency": 10.0},
        {"small_files": 10, "size_gb": 1.0, "query_frequency": 0.0},
    ]
    traits = compute_moop_scores(candidates, weights)
    # T3 excluded entirely (no file_entropy anywhere in the batch) -- its weight lands on
    # T1/T4 proportionally, not silently dropped (S_c would otherwise never reach its max).
    assert traits[0].t3 == 0.0
    top = traits[0]
    base = weights.t1_file_count + weights.t4_query_affinity
    share = weights.t3_file_entropy / base
    t1_w = weights.t1_file_count + weights.t1_file_count * share
    t4_w = weights.t4_query_affinity + weights.t4_query_affinity * share
    expected = t1_w * top.t1 + t4_w * top.t4 - weights.t2_compute_cost * top.t2
    assert top.s_c == pytest.approx(round(expected, 4))


def test_compute_moop_scores_with_file_entropy_uses_it_and_can_go_negative():
    candidates = [
        # Worst on every positive trait (fewest small files, coldest, lowest entropy) and
        # worst (most expensive) on the one negative trait -- guaranteed s_c < 0, since T2 is
        # the only term still contributing once T1/T3/T4 all bottom out at 0.
        {"small_files": 1, "size_gb": 1000.0, "query_frequency": 0.0, "file_entropy": 0.0},
        {"small_files": 100, "size_gb": 1.0, "query_frequency": 10.0, "file_entropy": 5.0},
    ]
    traits = compute_moop_scores(candidates)
    assert traits[1].s_c > traits[0].s_c
    assert traits[0].s_c < 0
    assert traits[0].t3 == 0.0
    assert traits[1].t3 == 1.0


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
        # None of these fixture partitions carry a base_column/transform, so there is no
        # per-partition signal to attribute to -- every partition must still fall back to the
        # broadcast table-level rate, unchanged from before per-partition affinity existed.
        assert partition.query_frequency == pytest.approx(20 / 7)
        # AutoComp §A2: MOOP traits persisted alongside the score every scan, not just on
        # some separate opt-in pass.
        assert partition.autocomp_traits is not None
        assert set(partition.autocomp_traits) == {"t1", "t2", "t3", "t4", "s_c", "weights_version"}

    by_value = {p.partition_value: p for p in updated_partitions}
    # Tied query_frequency (broadcast) across all three -- small_files should still drive T1
    # divergence: the 200-small-file partition should out-rank the 50-small-file one.
    hottest_s_c = by_value["2024-01-03"].autocomp_traits["s_c"]
    coldest_s_c = by_value["2024-01-02"].autocomp_traits["s_c"]
    assert hottest_s_c > coldest_s_c


def test_update_partition_benefit_scores_uses_real_scan_cost_from_query_log(session):
    """Two otherwise-identical tables (same fragmentation, same query count) -- the one whose
    QueryLog rows carry a high bytes_read must end up with a higher compaction_benefit_score.
    End-to-end version of test_compute_benefit_score_rewards_tables_with_expensive_scans,
    proving the signal actually reaches the DB write path, not just the pure function."""
    cheap_table = _seed_table(session, schema_name="test", table_name="cheap_scans")
    expensive_table = _seed_table(session, schema_name="test", table_name="expensive_scans")
    scan_at = datetime.now(UTC)

    for table in (cheap_table, expensive_table):
        session.add(
            TablePartition(
                table_id=table.id,
                partition_values={"dt": "2024-01-01"},
                partition_fields=[],
                partition_value="2024-01-01",
                partition_size_gb=10.0,
                small_files_in_partition_count=100,
                scan_at=scan_at,
            )
        )

    def _seed_queries(table, bytes_read: int, count: int = 10) -> None:
        source = session.get(Source, table.source_id)
        for i in range(count):
            session.add(
                QueryLog(
                    source_id=source.id,
                    table_id=table.id,
                    query_id=f"{table.table_name}-query-{i}",
                    engine="trino",
                    query_text=f"SELECT * FROM test.{table.table_name}",
                    executed_at=datetime.now(UTC) - timedelta(hours=i),
                    duration_ms=1000,
                    bytes_read=bytes_read,
                )
            )

    _seed_queries(cheap_table, bytes_read=10 * 1024 * 1024)  # 10MB/query
    _seed_queries(expensive_table, bytes_read=50 * 1024 * 1024 * 1024)  # 50GB/query
    session.commit()

    update_partition_benefit_scores(session, cheap_table.id)
    update_partition_benefit_scores(session, expensive_table.id)

    cheap_partition = session.scalars(
        select(TablePartition).where(TablePartition.table_id == cheap_table.id)
    ).one()
    expensive_partition = session.scalars(
        select(TablePartition).where(TablePartition.table_id == expensive_table.id)
    ).one()

    # Both tables have identical fragmentation and identical query counts (10/window on each) --
    # only the real Trino scan-cost signal (bytes_read) differs, and it must be what separates
    # them here.
    assert expensive_partition.query_frequency == pytest.approx(cheap_partition.query_frequency)
    assert (
        expensive_partition.compaction_benefit_score > cheap_partition.compaction_benefit_score
    )


def test_update_partition_benefit_scores_scan_cost_reaches_dispatch_score(session):
    """job_store._table_autocomp_summary and planner._compare_candidates prefer
    autocomp_traits["s_c"] over compaction_benefit_score whenever both are present -- that's
    the field real job dispatch order actually reads. Two partitions on the *same* table/scan
    (so MOOP's within-batch min-max normalization has a real spread to normalize against,
    unlike a single-partition table where every trait degenerates to the trivial 0.5 midpoint):
    identical size, uneven small_files so query_frequency naturally diverges per-partition via
    real filter affinity, plus a high table-level scan-cost signal -- the higher-frequency
    partition's s_c must end up materially above the neutral tie point, proving the scan-cost
    multiplier reached the persisted dispatch field end-to-end, not just the pure function."""
    table = _seed_table(session, schema_name="analytics", table_name="scan_cost_dispatch")
    scan_at = datetime.now(UTC)

    def _day_partition(epoch_day: int, small_files: int) -> TablePartition:
        return TablePartition(
            table_id=table.id,
            partition_values={"event_date_day": epoch_day},
            partition_fields=[
                {
                    "base_column": "event_date",
                    "partition_field": "event_date_day",
                    "transform": "day",
                    "col_type": "date",
                }
            ],
            partition_value=str(epoch_day),
            base_column="event_date",
            base_column_type="date",
            transform="day",
            partition_size_gb=10.0,
            small_files_in_partition_count=small_files,
            scan_at=scan_at,
        )

    hot = _day_partition(20210, small_files=100)  # queried a lot, expensive scans
    cold = _day_partition(20211, small_files=100)  # never queried
    session.add_all([hot, cold])

    source = session.get(Source, table.source_id)
    for i in range(8):
        session.add(
            QueryLog(
                source_id=source.id,
                table_id=table.id,
                query_id=f"hot-{i}",
                engine="trino",
                query_text=(
                    "SELECT * FROM analytics.scan_cost_dispatch WHERE event_date = DATE "
                    "'2025-05-02'"
                ),
                executed_at=datetime.now(UTC) - timedelta(hours=i),
                duration_ms=1000,
                bytes_read=50 * 1024 * 1024 * 1024,  # 50GB/query -- expensive
            )
        )
    session.commit()

    update_partition_benefit_scores(session, table.id)

    by_value = {
        p.partition_value: p
        for p in session.scalars(
            select(TablePartition).where(TablePartition.table_id == table.id)
        ).all()
    }
    hot_traits = by_value["20210"].autocomp_traits
    cold_traits = by_value["20211"].autocomp_traits

    # Identical fragmentation (t1/t2 tied) -- only the queried, expensive-scan partition should
    # come out ahead, and specifically via t4 (query affinity), not t1/t2 drift.
    assert hot_traits["t1"] == cold_traits["t1"]
    assert hot_traits["t2"] == cold_traits["t2"]
    assert hot_traits["t4"] > cold_traits["t4"]
    assert hot_traits["s_c"] > cold_traits["s_c"]


def test_update_partition_benefit_scores_diverges_per_partition_with_uneven_filters(session):
    """AutoComp §A3.1: a partition actually queried should score higher than an equally
    small-filed, equally sized partition nobody queries -- the bug the table-broadcast
    behavior above still has when a real partition spec + literal query filters exist."""
    table = _seed_table(session, schema_name="analytics", table_name="events")
    scan_at = datetime.now(UTC)

    def _day_partition(epoch_day: int, small_files: int) -> TablePartition:
        return TablePartition(
            table_id=table.id,
            partition_values={"event_date_day": epoch_day},
            partition_fields=[
                {
                    "base_column": "event_date",
                    "partition_field": "event_date_day",
                    "transform": "day",
                    "col_type": "date",
                }
            ],
            partition_value=str(epoch_day),
            base_column="event_date",
            base_column_type="date",
            transform="day",
            partition_size_gb=10.0,
            small_files_in_partition_count=small_files,
            scan_at=scan_at,
        )

    hot = _day_partition(20210, small_files=50)  # 2025-05-02, queried a lot
    cold = _day_partition(20211, small_files=200)  # 2025-05-03, never queried
    untouched = _day_partition(20212, small_files=200)  # 2025-05-04, never queried
    session.add_all([hot, cold, untouched])

    source = session.get(Source, table.source_id)
    for i in range(8):
        session.add(
            QueryLog(
                source_id=source.id,
                table_id=table.id,
                query_id=f"hot-{i}",
                engine="trino",
                query_text="SELECT * FROM analytics.events WHERE event_date = DATE '2025-05-02'",
                executed_at=datetime.now(UTC) - timedelta(hours=i),
                duration_ms=500,
            )
        )
    # A couple of queries that don't reference the partition column at all -- they count toward
    # the coverage denominator but must not be attributable to any partition.
    for i in range(2):
        session.add(
            QueryLog(
                source_id=source.id,
                table_id=table.id,
                query_id=f"untargeted-{i}",
                engine="trino",
                query_text="SELECT count(*) FROM analytics.events",
                executed_at=datetime.now(UTC) - timedelta(hours=i),
                duration_ms=200,
            )
        )
    session.commit()

    updated = update_partition_benefit_scores(session, table.id)
    assert updated == 3

    by_value = {
        p.partition_value: p
        for p in session.scalars(
            select(TablePartition).where(TablePartition.table_id == table.id)
        ).all()
    }

    # 8/10 queries resolved to the hot partition -> 80% coverage, well above the 20% trust
    # threshold, so per-partition (not broadcast) frequency applies.
    assert by_value["20210"].query_frequency == pytest.approx(8 / 7)
    assert by_value["20211"].query_frequency == 0.0
    assert by_value["20212"].query_frequency == 0.0

    # The never-queried partition has *more* small files than the queried one -- under the old
    # table-broadcast behavior it would still win on benefit score just by file count. With
    # per-partition affinity, actual query traffic must be what decides it.
    assert by_value["20210"].compaction_benefit_score > by_value["20211"].compaction_benefit_score


def test_update_partition_benefit_scores_falls_back_to_broadcast_below_coverage_threshold(
    session,
):
    """When too few in-window queries resolve to a specific partition, trust the existing
    table-level rate rather than noisy per-partition numbers (AutoComp §A3.1 fallback)."""
    table = _seed_table(session, schema_name="analytics", table_name="low_coverage")
    scan_at = datetime.now(UTC)
    partition = TablePartition(
        table_id=table.id,
        partition_values={"event_date_day": 20210},
        partition_fields=[
            {
                "base_column": "event_date",
                "partition_field": "event_date_day",
                "transform": "day",
                "col_type": "date",
            }
        ],
        partition_value="20210",
        base_column="event_date",
        base_column_type="date",
        transform="day",
        partition_size_gb=10.0,
        small_files_in_partition_count=100,
        scan_at=scan_at,
    )
    session.add(partition)

    source = session.get(Source, table.source_id)
    # One resolvable query out of ten -- 10% coverage, below MIN_COVERAGE_RATIO (20%).
    session.add(
        QueryLog(
            source_id=source.id,
            table_id=table.id,
            query_id="resolvable",
            engine="trino",
            query_text="SELECT * FROM analytics.low_coverage WHERE event_date = DATE '2025-05-02'",
            executed_at=datetime.now(UTC),
            duration_ms=500,
        )
    )
    for i in range(9):
        session.add(
            QueryLog(
                source_id=source.id,
                table_id=table.id,
                query_id=f"untargeted-{i}",
                engine="trino",
                query_text="SELECT count(*) FROM analytics.low_coverage",
                executed_at=datetime.now(UTC) - timedelta(hours=i),
                duration_ms=200,
            )
        )
    session.commit()

    update_partition_benefit_scores(session, table.id)

    refreshed = session.get(TablePartition, partition.id)
    # 10 queries / 7 days broadcast, same as if affinity had never run.
    assert refreshed.query_frequency == pytest.approx(10 / 7)


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
            status="OK",
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


def test_update_table_effectiveness_persists_onto_table_stats_raw(session):
    table = _seed_table(session, schema_name="test", table_name="effectiveness_persist")
    stats = TableStats(
        table_id=table.id,
        scan_at=datetime.now(UTC),
        needs_compaction=False,
        compaction_priority="LOW",
        grade="A",
        overall_score=95,
        total_files=10,
        small_files_count=0,
        total_size_gb=1.0,
        raw={"unrelated_key": "keep-me"},
    )
    session.add(stats)
    session.add(
        AuditLog(
            table_id=table.id,
            action="optimize",
            status="ok",
            input_files_count=100,
            output_files_count=2,
            ts=datetime.now(UTC),
            duration_ms=1000,
        )
    )
    session.commit()

    updated = update_table_effectiveness(session, table.id)
    assert updated is True

    refreshed = session.get(TableStats, table.id)
    assert refreshed.raw["unrelated_key"] == "keep-me"  # existing raw keys aren't clobbered
    persisted = refreshed.raw["compaction_effectiveness"]
    assert persisted["effectiveness_ratio"] == 50.0
    assert persisted["effectiveness"] == "excellent"
    assert "computed_at" in persisted


def test_update_table_effectiveness_is_noop_without_a_stats_row(session):
    table = _seed_table(session, schema_name="test", table_name="no_stats_yet")
    session.commit()
    assert update_table_effectiveness(session, table.id) is False


def test_update_all_benefit_scores_also_refreshes_effectiveness(session):
    table = _seed_table(session, schema_name="test", table_name="fleet_effectiveness")
    stats = TableStats(
        table_id=table.id,
        scan_at=datetime.now(UTC),
        needs_compaction=False,
        compaction_priority="LOW",
        grade="A",
        overall_score=95,
        total_files=10,
        small_files_count=0,
        total_size_gb=1.0,
    )
    session.add(stats)
    scan_at = datetime.now(UTC)
    session.add(
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-01"},
            partition_fields=[],
            partition_value="2024-01-01",
            partition_size_gb=10.0,
            small_files_in_partition_count=100,
            scan_at=scan_at,
        )
    )
    session.add(
        AuditLog(
            table_id=table.id,
            action="optimize",
            status="ok",
            input_files_count=20,
            output_files_count=1,
            ts=datetime.now(UTC),
            duration_ms=1000,
        )
    )
    session.commit()

    update_all_benefit_scores(session)

    refreshed = session.get(TableStats, table.id)
    assert refreshed.raw["compaction_effectiveness"]["effectiveness_ratio"] == 20.0


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
