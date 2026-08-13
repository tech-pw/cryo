"""Tests for cryo.api.routes_dedup -- the table-similarity API endpoints.

Regression coverage for the 2026-08-13 OOM: list_similarity_clusters previously loaded
full ORM Table/TableStats entities for every clustered table via unbounded IN clauses,
plus one extra max-similarity query PER CLUSTER (498 of them on the real fleet) -- the
same class of bug already found and fixed in
cryo.intelligence.dedup.pipeline._replace_clusters. This endpoint OOM-killed the live
API pod (6Gi limit) twice within minutes of the Similarity page going live, which is why
these tests exist: the pipeline tests alone didn't cover the read path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from cryo.api.routes_dedup import list_similarity_clusters, list_similarity_pairs
from cryo.db.models import (
    Source,
    Table,
    TableSimilarityCluster,
    TableSimilarityPair,
    TableStats,
)


def _make_table(
    session: Session,
    source: Source,
    *,
    schema_name: str,
    table_name: str,
    size_gb: float,
    query_count_30d: int = 0,
) -> Table:
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name=schema_name,
        table_name=table_name,
        is_active=True,
    )
    session.add(table)
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=0,
            total_records=1000,
            total_size_gb=size_gb,
            query_count_30d=query_count_30d,
        )
    )
    return table


def test_list_similarity_clusters_empty_when_no_generation_exists(session: Session) -> None:
    result = list_similarity_clusters(session)

    assert result.clusters == []
    assert result.generation is None
    assert result.total_clusters == 0


def test_list_similarity_clusters_returns_real_cluster_with_correct_totals(
    session: Session,
) -> None:
    source = Source(id=uuid.uuid4(), name="src", type="iceberg", config={})
    session.add(source)
    session.flush()

    keep_table = _make_table(
        session, source, schema_name="store", table_name="orders", size_gb=100.0, query_count_30d=42
    )
    drop_table = _make_table(
        session,
        source,
        schema_name="store",
        table_name="orders_backup",
        size_gb=95.0,
        query_count_30d=0,
    )
    session.flush()

    cluster_id = uuid.uuid4()
    now = datetime.now(UTC)
    session.add_all(
        [
            TableSimilarityCluster(
                id=uuid.uuid4(),
                cluster_id=cluster_id,
                table_id=keep_table.id,
                role="keep",
                keep_score=0.8,
                keep_reason="42 queries in last 30d",
                generation=1,
                computed_at=now,
            ),
            TableSimilarityCluster(
                id=uuid.uuid4(),
                cluster_id=cluster_id,
                table_id=drop_table.id,
                role="drop_candidate",
                keep_score=0.0,
                keep_reason="zero reads",
                generation=1,
                computed_at=now,
            ),
        ]
    )
    a_id, b_id = sorted([keep_table.id, drop_table.id], key=str)
    session.add(
        TableSimilarityPair(
            id=uuid.uuid4(),
            table_a_id=a_id,
            table_b_id=b_id,
            stage="blocked",
            similarity_score=0.91,
            schema_similarity=1.0,
            stats_similarity=0.95,
            file_overlap_detected=False,
            score_components={},
            computed_at=now,
        )
    )
    session.flush()

    result = list_similarity_clusters(session)

    assert result.generation == 1
    assert result.total_clusters == 1
    assert result.total_tables_in_clusters == 2
    cluster = result.clusters[0]
    assert cluster.reclaimable_gb == 95.0  # only the drop_candidate's size
    assert cluster.total_size_gb == 195.0  # both members
    assert cluster.max_pair_similarity == 0.91  # from the single aggregate query, not N+1
    roles = {m.table_name: m.role for m in cluster.members}
    assert roles["orders"] == "keep"
    assert roles["orders_backup"] == "drop_candidate"


def test_list_similarity_clusters_filters_by_min_reclaimable_gb(session: Session) -> None:
    source = Source(id=uuid.uuid4(), name="src", type="iceberg", config={})
    session.add(source)
    session.flush()

    small_keep = _make_table(session, source, schema_name="s", table_name="t1", size_gb=1.0)
    small_drop = _make_table(session, source, schema_name="s", table_name="t2", size_gb=0.5)
    session.flush()

    cluster_id = uuid.uuid4()
    now = datetime.now(UTC)
    session.add_all(
        [
            TableSimilarityCluster(
                id=uuid.uuid4(),
                cluster_id=cluster_id,
                table_id=small_keep.id,
                role="keep",
                generation=1,
                computed_at=now,
            ),
            TableSimilarityCluster(
                id=uuid.uuid4(),
                cluster_id=cluster_id,
                table_id=small_drop.id,
                role="drop_candidate",
                generation=1,
                computed_at=now,
            ),
        ]
    )
    session.flush()

    result = list_similarity_clusters(session, min_reclaimable_gb=10.0)

    assert result.clusters == []  # 0.5GB reclaimable is below the 10GB filter


def test_list_similarity_clusters_only_latest_generation(session: Session) -> None:
    source = Source(id=uuid.uuid4(), name="src", type="iceberg", config={})
    session.add(source)
    session.flush()

    old_table = _make_table(session, source, schema_name="s", table_name="old", size_gb=10.0)
    new_table = _make_table(session, source, schema_name="s", table_name="new", size_gb=20.0)
    session.flush()

    now = datetime.now(UTC)
    session.add(
        TableSimilarityCluster(
            id=uuid.uuid4(),
            cluster_id=uuid.uuid4(),
            table_id=old_table.id,
            role="keep",
            generation=1,
            computed_at=now,
        )
    )
    session.add(
        TableSimilarityCluster(
            id=uuid.uuid4(),
            cluster_id=uuid.uuid4(),
            table_id=new_table.id,
            role="keep",
            generation=2,
            computed_at=now,
        )
    )
    session.flush()

    result = list_similarity_clusters(session)

    assert result.generation == 2
    assert result.total_tables_in_clusters == 1
    assert result.clusters[0].members[0].table_name == "new"


def test_list_similarity_pairs_returns_qualified_names(session: Session) -> None:
    source = Source(id=uuid.uuid4(), name="mysrc", type="iceberg", config={})
    session.add(source)
    session.flush()

    a = _make_table(session, source, schema_name="store", table_name="orders", size_gb=10.0)
    b = _make_table(session, source, schema_name="store", table_name="orders_backup", size_gb=9.0)
    session.flush()

    a_id, b_id = sorted([a.id, b.id], key=str)
    session.add(
        TableSimilarityPair(
            id=uuid.uuid4(),
            table_a_id=a_id,
            table_b_id=b_id,
            stage="blocked",
            similarity_score=0.85,
            schema_similarity=1.0,
            stats_similarity=0.9,
            file_overlap_detected=False,
            score_components={"composite_score": 0.85},
            computed_at=datetime.now(UTC),
        )
    )
    session.flush()

    results = list_similarity_pairs(session, min_score=0.5, limit=10)

    assert len(results) == 1
    assert results[0].similarity_score == 0.85
    assert "orders" in results[0].table_a_name or "orders" in results[0].table_b_name


def test_list_similarity_pairs_excludes_below_min_score(session: Session) -> None:
    source = Source(id=uuid.uuid4(), name="src", type="iceberg", config={})
    session.add(source)
    session.flush()

    a = _make_table(session, source, schema_name="s", table_name="a", size_gb=1.0)
    b = _make_table(session, source, schema_name="s", table_name="b", size_gb=1.0)
    session.flush()

    a_id, b_id = sorted([a.id, b.id], key=str)
    session.add(
        TableSimilarityPair(
            id=uuid.uuid4(),
            table_a_id=a_id,
            table_b_id=b_id,
            stage="blocked",
            similarity_score=0.2,
            score_components={},
            computed_at=datetime.now(UTC),
        )
    )
    session.flush()

    results = list_similarity_pairs(session, min_score=0.5, limit=10)

    assert results == []


def test_max_pair_similarity_found_even_when_pair_sides_land_in_different_chunks(
    session: Session,
) -> None:
    """Pins _max_pair_similarity_by_cluster's OR-based table_a_id/table_b_id filter: a
    pair is found even when its two sides land in different chunks (chunk_size=1 forces
    this deterministically with just two tables). Note this is a defensive-design test,
    not a regression for an actual bug -- the table_a_id-only filter this replaced was
    re-derived during review to be correct too (the outer loop iterates every chunk of
    table_ids, and table_a_id is always present in table_ids for any pair touching a
    clustered table, so it was never actually possible to miss a pair). The OR filter is
    kept anyway because each chunk's query is self-contained and its correctness is
    checkable by inspection, without needing to reason about the surrounding loop.
    """
    from cryo.api.routes_dedup import _max_pair_similarity_by_cluster

    source = Source(id=uuid.uuid4(), name="src", type="iceberg", config={})
    session.add(source)
    session.flush()

    t1 = _make_table(session, source, schema_name="s", table_name="t1", size_gb=10.0)
    t2 = _make_table(session, source, schema_name="s", table_name="t2", size_gb=10.0)
    session.flush()

    cluster_id = uuid.uuid4()
    a_id, b_id = sorted([t1.id, t2.id], key=str)
    session.add(
        TableSimilarityPair(
            id=uuid.uuid4(),
            table_a_id=a_id,
            table_b_id=b_id,
            stage="blocked",
            similarity_score=0.77,
            score_components={},
            computed_at=datetime.now(UTC),
        )
    )
    session.flush()

    table_to_cluster = {t1.id: cluster_id, t2.id: cluster_id}
    # table_ids order is deliberately the REVERSE of canonical (a_id, b_id) ordering --
    # with chunk_size=1, a_id ends up in the second chunk and b_id in the first, the
    # exact placement the old table_a_id-only filter would have missed.
    table_ids = [b_id, a_id] if a_id != b_id else [a_id]

    result = _max_pair_similarity_by_cluster(session, table_ids, table_to_cluster, chunk_size=1)

    assert result.get(cluster_id) == 0.77
