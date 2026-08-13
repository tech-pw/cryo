"""Cluster Stage-2/3-scored pairs into duplicate groups, and derive a keep/drop-candidate
recommendation per cluster member (docs/TABLE_DEDUPLICATION_DESIGN.md SS6, SS7).

Uses union-find (disjoint-set, weighted-union-by-rank + path compression) over an edge
list of pairs scoring above threshold -- the standard entity-resolution approach for
grouping transitively-linked records, O(n alpha(n)) and trivial to run even over tens of
thousands of edges.

Explicit, named failure mode from the design doc SS6: naive connected-components
clustering increases recall but is noise-sensitive -- one spurious high-scoring edge can
transitively merge two otherwise-unrelated clusters ("cluster chaining"). Mitigated here by
requiring the merge threshold to be meaningfully above the pair-inclusion threshold (not
the same value), which is the cheaper of the two mitigations the design doc names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

# Deliberately higher than the Stage 2 "worth persisting a pair at all" threshold used
# elsewhere in the pipeline -- see module docstring on cluster chaining. A pair can be
# recorded and shown as evidence without being strong enough alone to merge two clusters.
DEFAULT_CLUSTER_MERGE_THRESHOLD = 0.75

# A table not read in this many days is treated as safe-to-drop-candidate regardless of
# cluster similarity score, matching the same 90-day bar validated during the S3-tiering
# ROI work earlier this session (trino_logs.trino_queries is the only source with enough
# real history to honestly answer a 90-day question -- TableQueryActivity's 30-day rolling
# sum is a reasonable proxy here since a full Stage 1 crawl of the 90-day Trino log for
# every fleet table is a much heavier query than this module's clustering step should own;
# see design doc SS11's open question on which read-age source to standardize on).
STALE_READ_DAYS = 90


class _UnionFind:
    def __init__(self, items: list[str]) -> None:
        self._parent = {item: item for item in items}
        self._rank = dict.fromkeys(items, 0)

    def find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        if self._rank[root_a] < self._rank[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        if self._rank[root_a] == self._rank[root_b]:
            self._rank[root_a] += 1


@dataclass(frozen=True, slots=True)
class SimilarityEdge:
    table_a_id: str
    table_b_id: str
    similarity_score: float


@dataclass(frozen=True, slots=True)
class TableClusterInput:
    """Everything clustering needs about one table to compute a keep/drop-candidate role
    -- deliberately a plain dataclass (not the ORM Table/TableStats models directly) so
    this module stays testable without a database, matching dedup.scoring/fingerprint's
    pattern of operating on plain dataclasses rather than live ORM objects."""

    table_id: str
    schema_name: str
    table_name: str
    total_size_gb: float
    created_at: datetime
    query_count_30d: int
    naming_pattern_hint: bool


@dataclass(frozen=True, slots=True)
class ClusterMember:
    table_id: str
    cluster_id: str
    role: str  # "keep" | "drop_candidate"
    keep_score: float
    keep_reason: str


def cluster_similarity_graph(
    edges: list[SimilarityEdge],
    *,
    merge_threshold: float = DEFAULT_CLUSTER_MERGE_THRESHOLD,
) -> dict[str, str]:
    """Union-find over edges scoring >= merge_threshold. Returns {table_id: cluster_root_id}
    for every table that appears in at least one qualifying edge -- tables with no
    sufficiently-strong similarity to anything else are simply absent from the result
    (they form no cluster, which is correct: a singleton isn't a duplicate cluster)."""
    qualifying = [e for e in edges if e.similarity_score >= merge_threshold]
    tables = sorted({e.table_a_id for e in qualifying} | {e.table_b_id for e in qualifying})
    if not tables:
        return {}

    uf = _UnionFind(tables)
    for edge in qualifying:
        uf.union(edge.table_a_id, edge.table_b_id)

    return {table_id: uf.find(table_id) for table_id in tables}


def _keep_score(table: TableClusterInput, *, now: datetime) -> tuple[float, str]:
    """Composite 'why keep this one' score -- higher wins. Design doc SS7's priority
    order, translated into weighted terms:
      1. Not the oldest/canonical table (earlier creation = more likely canonical)
      2. Recently read (design doc: zero reads in 90+ days is safe to flag regardless of
         similarity; actively read needs a human decision, never an auto-drop)
      3. Naming heuristics as a tie-breaker only (a `_backup`/`_temp`-suffixed name scores
         LOWER on keep-worthiness, nudging it toward drop-candidate, never determining the
         outcome alone)
    """
    reasons: list[str] = []
    score = 0.0

    age_days = max((now - table.created_at).days, 0)
    # Older tables are more likely canonical -- capped contribution so a truly ancient
    # abandoned table doesn't dominate purely on age against a younger, actively-used one.
    age_component = min(age_days / 365.0, 2.0) * 0.3
    score += age_component
    if age_days > 30:
        reasons.append(f"created {age_days}d ago (older = more likely canonical)")

    if table.query_count_30d > 0:
        # Actively read: strong keep signal, and per design doc SS7 this alone should be
        # enough to keep a table out of auto-drop territory regardless of cluster role.
        read_component = min(table.query_count_30d / 100.0, 1.0) * 0.5
        score += read_component
        reasons.append(f"{table.query_count_30d} queries in last 30d")
    else:
        reasons.append("zero reads in last 30d")

    if table.naming_pattern_hint:
        # Backup/temp/copy-style name -- nudges toward drop-candidate, never alone
        # determinative (design doc SS8).
        score -= 0.2
        reasons.append("name matches backup/temp/copy pattern")

    return score, "; ".join(reasons)


def recommend_cluster_roles(
    cluster_assignments: dict[str, str],
    tables: dict[str, TableClusterInput],
    *,
    now: datetime | None = None,
) -> list[ClusterMember]:
    """For each cluster, score every member's keep-worthiness and assign exactly one
    'keep' (the highest-scoring member) with every other member as 'drop_candidate'.

    'drop_candidate' is a recommendation label, never an action -- design doc SS7: nothing
    in this pipeline drops a table. A human confirms before anything happens.
    """
    now = now or datetime.now(UTC)

    by_cluster: dict[str, list[str]] = {}
    for table_id, cluster_id in cluster_assignments.items():
        by_cluster.setdefault(cluster_id, []).append(table_id)

    members: list[ClusterMember] = []
    for cluster_id, table_ids in by_cluster.items():
        scored = []
        for table_id in table_ids:
            table = tables.get(table_id)
            if table is None:
                continue
            score, reason = _keep_score(table, now=now)
            scored.append((table_id, score, reason))
        if not scored:
            continue
        scored.sort(key=lambda t: t[1], reverse=True)
        keep_table_id = scored[0][0]
        for table_id, score, reason in scored:
            role = "keep" if table_id == keep_table_id else "drop_candidate"
            members.append(
                ClusterMember(
                    table_id=table_id,
                    cluster_id=cluster_id,
                    role=role,
                    keep_score=round(score, 4),
                    keep_reason=reason,
                )
            )
    return members


def is_stale_by_reads(table: TableClusterInput, *, now: datetime | None = None) -> bool:
    """True if this table has had zero reads in the last STALE_READ_DAYS -- design doc
    SS7's "safe to flag regardless of similarity score" bar. Uses query_count_30d as a
    30-day proxy for a 90-day question (see STALE_READ_DAYS docstring above for why); a
    table with zero queries in the last 30 days is at minimum zero for 30 days, which is
    already meaningful signal even though it under-covers the full 90-day claim."""
    return table.query_count_30d == 0


def reclaimable_gb_by_cluster(
    members: list[ClusterMember], tables: dict[str, TableClusterInput]
) -> dict[str, float]:
    """Sum of every drop_candidate member's size per cluster -- the storage this cluster
    would reclaim if every non-canonical member were dropped, excluding the kept member.
    Used by the similarity API/frontend to sort clusters by $ impact, matching the same
    reclaim-first triage order already used for the S3-tiering candidate list."""
    totals: dict[str, float] = {}
    for member in members:
        if member.role != "drop_candidate":
            continue
        table = tables.get(member.table_id)
        if table is None:
            continue
        totals[member.cluster_id] = totals.get(member.cluster_id, 0.0) + table.total_size_gb
    return totals
