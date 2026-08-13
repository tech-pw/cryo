"""Tests for cryo.intelligence.dedup.clustering -- union-find grouping and keep/
drop-candidate recommendation (docs/TABLE_DEDUPLICATION_DESIGN.md SS6, SS7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.intelligence.dedup.clustering import (
    SimilarityEdge,
    TableClusterInput,
    cluster_similarity_graph,
    is_stale_by_reads,
    reclaimable_gb_by_cluster,
    recommend_cluster_roles,
)

_NOW = datetime(2026, 8, 13, tzinfo=UTC)


def _table(
    table_id: str,
    *,
    size_gb: float = 100.0,
    age_days: int = 30,
    reads: int = 0,
    naming_hint: bool = False,
    name: str = "orders",
) -> TableClusterInput:
    return TableClusterInput(
        table_id=table_id,
        schema_name="store",
        table_name=name,
        total_size_gb=size_gb,
        created_at=_NOW - timedelta(days=age_days),
        query_count_30d=reads,
        naming_pattern_hint=naming_hint,
    )


def test_two_tables_above_threshold_form_one_cluster() -> None:
    edges = [SimilarityEdge(table_a_id="t1", table_b_id="t2", similarity_score=0.9)]
    result = cluster_similarity_graph(edges, merge_threshold=0.75)

    assert result["t1"] == result["t2"]


def test_edge_below_threshold_does_not_cluster() -> None:
    edges = [SimilarityEdge(table_a_id="t1", table_b_id="t2", similarity_score=0.5)]
    result = cluster_similarity_graph(edges, merge_threshold=0.75)

    assert result == {}


def test_transitive_clustering_via_connected_components() -> None:
    """A-B strong, B-C strong -> A, B, C all in one cluster even with no direct A-C edge
    (design doc SS6: this is the point of connected-components clustering)."""
    edges = [
        SimilarityEdge(table_a_id="a", table_b_id="b", similarity_score=0.9),
        SimilarityEdge(table_a_id="b", table_b_id="c", similarity_score=0.85),
    ]
    result = cluster_similarity_graph(edges, merge_threshold=0.75)

    assert result["a"] == result["b"] == result["c"]


def test_disjoint_clusters_get_different_ids() -> None:
    edges = [
        SimilarityEdge(table_a_id="a", table_b_id="b", similarity_score=0.9),
        SimilarityEdge(table_a_id="x", table_b_id="y", similarity_score=0.9),
    ]
    result = cluster_similarity_graph(edges, merge_threshold=0.75)

    assert result["a"] == result["b"]
    assert result["x"] == result["y"]
    assert result["a"] != result["x"]


def test_no_edges_produces_no_clusters() -> None:
    assert cluster_similarity_graph([], merge_threshold=0.75) == {}


def test_recommend_roles_keeps_the_older_more_read_table() -> None:
    assignments = {"old_canonical": "cluster-1", "new_backup": "cluster-1"}
    tables = {
        "old_canonical": _table("old_canonical", age_days=400, reads=50, name="orders"),
        "new_backup": _table(
            "new_backup", age_days=5, reads=0, naming_hint=True, name="orders_backup"
        ),
    }
    members = recommend_cluster_roles(assignments, tables, now=_NOW)
    roles = {m.table_id: m.role for m in members}

    assert roles["old_canonical"] == "keep"
    assert roles["new_backup"] == "drop_candidate"


def test_recommend_roles_exactly_one_keep_per_cluster() -> None:
    assignments = {"a": "c1", "b": "c1", "c": "c1"}
    tables = {
        "a": _table("a", age_days=100, reads=10),
        "b": _table("b", age_days=50, reads=5),
        "c": _table("c", age_days=10, reads=0),
    }
    members = recommend_cluster_roles(assignments, tables, now=_NOW)

    keeps = [m for m in members if m.role == "keep"]
    assert len(keeps) == 1
    assert len(members) == 3


def test_recommend_roles_actively_read_table_beats_older_unread_table() -> None:
    """Design doc SS7: 'actively read' is a strong keep signal on its own -- an
    actively-queried newer table should outscore an older, completely unread one."""
    assignments = {"old_unused": "c1", "new_active": "c1"}
    tables = {
        "old_unused": _table("old_unused", age_days=500, reads=0),
        "new_active": _table("new_active", age_days=20, reads=200),
    }
    members = recommend_cluster_roles(assignments, tables, now=_NOW)
    roles = {m.table_id: m.role for m in members}

    assert roles["new_active"] == "keep"


def test_naming_pattern_alone_never_determines_role() -> None:
    """Design doc SS8: naming heuristics must never be the sole determinant. A
    `_backup`-named table that's actively read and older should still be kept over an
    unread, newer, non-suffixed sibling."""
    assignments = {"active_backup": "c1", "unread_sibling": "c1"}
    tables = {
        "active_backup": _table(
            "active_backup", age_days=300, reads=500, naming_hint=True, name="orders_backup"
        ),
        "unread_sibling": _table("unread_sibling", age_days=10, reads=0, name="orders2"),
    }
    members = recommend_cluster_roles(assignments, tables, now=_NOW)
    roles = {m.table_id: m.role for m in members}

    assert roles["active_backup"] == "keep"


def test_is_stale_by_reads() -> None:
    stale = _table("t1", reads=0)
    active = _table("t2", reads=1)

    assert is_stale_by_reads(stale) is True
    assert is_stale_by_reads(active) is False


def test_reclaimable_gb_sums_only_drop_candidates() -> None:
    assignments = {"keep_me": "c1", "drop_me_1": "c1", "drop_me_2": "c1"}
    tables = {
        "keep_me": _table("keep_me", size_gb=50.0, age_days=400, reads=10),
        "drop_me_1": _table("drop_me_1", size_gb=30.0, age_days=5, reads=0),
        "drop_me_2": _table("drop_me_2", size_gb=20.0, age_days=5, reads=0),
    }
    members = recommend_cluster_roles(assignments, tables, now=_NOW)
    totals = reclaimable_gb_by_cluster(members, tables)

    assert totals["c1"] == 50.0  # only the two drop_candidates, not the kept 50GB table


def test_clustering_does_not_chain_through_weak_bridge_when_threshold_high() -> None:
    """Design doc SS6's named failure mode: one spurious edge shouldn't merge two
    otherwise-unrelated clusters. With a high merge_threshold, a moderately-scored bridge
    edge should not connect two strongly-connected sub-clusters."""
    edges = [
        SimilarityEdge(table_a_id="a1", table_b_id="a2", similarity_score=0.95),
        SimilarityEdge(table_a_id="b1", table_b_id="b2", similarity_score=0.95),
        # Weak bridge -- below the merge threshold, so a1/a2 and b1/b2 must stay separate.
        SimilarityEdge(table_a_id="a2", table_b_id="b1", similarity_score=0.6),
    ]
    result = cluster_similarity_graph(edges, merge_threshold=0.75)

    assert result["a1"] == result["a2"]
    assert result["b1"] == result["b2"]
    assert result["a1"] != result["b1"]
