"""API routes for the table-deduplication similarity pipeline
(docs/TABLE_DEDUPLICATION_DESIGN.md).

Endpoints (all under ``/api/v1``):

- ``POST /dedup/run``                    -- run Stage 1 + Stage 2 across the fleet, persist
                                             scored pairs and clusters (operator-triggered,
                                             not on an automatic schedule yet -- see design
                                             doc SS11's open questions)
- ``GET  /dedup/clusters``                -- ranked cluster list, for the similarity page's
                                             bubble/cluster graph
- ``GET  /dedup/pairs``                   -- individual scored pairs (filterable), for a
                                             flat table view / debugging a cluster's edges
- ``POST /dedup/verify``                  -- Stage 3 exact Trino verification for one pair,
                                             on-demand only (design doc SS10: too expensive
                                             to run automatically against every high scorer)

Every response includes the full evidence trail behind a score -- design doc SS7/SS9: no
bare numeric score for something that can end in a DROP TABLE, especially given this
session's own gold_gst_slabs incident. Nothing under this router deletes or modifies a
table; role="drop_candidate" is a recommendation label only.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from cryo.api.deps import SessionDep, require_operator, require_viewer
from cryo.api.schemas import (
    SimilarityClusterMemberRead,
    SimilarityClusterRead,
    SimilarityClustersResponse,
    SimilarityPairRead,
    SimilarityPipelineRunResponse,
    SimilarityVerifyRequest,
    SimilarityVerifyResponse,
)
from cryo.db.model_utils import qualified_table_name
from cryo.db.models import Table, TableSimilarityCluster, TableSimilarityPair, TableStats
from cryo.intelligence.dedup.pipeline import run_pipeline
from cryo.intelligence.dedup.verification import verify_pair_exact

router = APIRouter(prefix="/api/v1", tags=["dedup"])

_CHUNK_SIZE = 1000


def _max_pair_similarity_by_cluster(
    session: SessionDep,
    table_ids: list[UUID],
    table_to_cluster: dict[UUID, UUID],
    *,
    chunk_size: int = _CHUNK_SIZE,
) -> dict[UUID, float]:
    """Max pairwise similarity_score per cluster, computed with a bounded number of
    aggregate queries over every pair touching this generation's tables (grouped by
    cluster in Python), not one query per cluster (498 separate unbounded-IN queries on
    the real fleet -- the OOM trigger this whole module was hardened against, see
    list_similarity_clusters' docstring).

    Filters on ``table_a_id.in_(chunk) OR table_b_id.in_(chunk)`` rather than
    table_a_id alone. Pairs are stored canonically ordered (table_a_id < table_b_id,
    see dedup.pipeline's _canonical_pair_order); since the loop iterates every chunk of
    table_ids, filtering on table_a_id alone would still find every pair correctly in
    the end (table_a_id is always present somewhere in table_ids for any pair touching a
    clustered table) -- there was no missed-pair bug here. The OR is kept anyway because
    each chunk's query is then self-contained and its correctness is checkable by
    inspection, without having to reason about "trust that some other iteration of this
    loop covers it" -- worth the (harmless) cost of a pair occasionally being returned
    from more than one chunk, since this only tracks a max(). ``chunk_size`` is a
    parameter (not the module constant directly) so tests can force multiple chunks with
    a small table set.
    """
    max_score_by_cluster: dict[UUID, float] = {}
    for start in range(0, len(table_ids), chunk_size):
        chunk = table_ids[start : start + chunk_size]
        pair_rows = session.execute(
            select(
                TableSimilarityPair.table_a_id,
                TableSimilarityPair.table_b_id,
                TableSimilarityPair.similarity_score,
            ).where(
                (TableSimilarityPair.table_a_id.in_(chunk))
                | (TableSimilarityPair.table_b_id.in_(chunk)),
                TableSimilarityPair.similarity_score.is_not(None),
            )
        ).all()
        for a_id, b_id, score in pair_rows:
            cluster_id = table_to_cluster.get(a_id)
            if cluster_id is None or table_to_cluster.get(b_id) != cluster_id:
                continue
            if score is not None and score > max_score_by_cluster.get(cluster_id, -1.0):
                max_score_by_cluster[cluster_id] = score
    return max_score_by_cluster


@router.post(
    "/dedup/run",
    response_model=SimilarityPipelineRunResponse,
    dependencies=[Depends(require_operator)],
)
def run_dedup_pipeline(
    session: SessionDep,
    sample_rows: Annotated[bool, Query()] = True,
) -> SimilarityPipelineRunResponse:
    """Run Stage 1 (metadata blocking) + Stage 2 (statistical scoring, with MinHash row
    sampling when ``sample_rows`` is true) across the whole fleet, then cluster and persist
    recommendations. Operator-triggered (require_operator, not require_viewer) since this
    runs real Trino queries against production tables when sample_rows=True.
    """
    result = run_pipeline(session, sample_rows=sample_rows)
    session.commit()
    return SimilarityPipelineRunResponse(
        tables_considered=result.tables_considered,
        candidate_pairs=result.candidate_pairs,
        pairs_sampled=result.pairs_sampled,
        pairs_persisted=result.pairs_persisted,
        clusters_found=result.clusters_found,
        cluster_generation=result.cluster_generation,
        errors=result.errors,
    )


@router.get(
    "/dedup/clusters",
    response_model=SimilarityClustersResponse,
    dependencies=[Depends(require_viewer)],
)
def list_similarity_clusters(
    session: SessionDep,
    min_reclaimable_gb: Annotated[float, Query(ge=0)] = 0.0,
) -> SimilarityClustersResponse:
    """Latest-generation cluster list, ranked by reclaimable storage -- the primary data
    source for the similarity page's cluster/bubble graph. Each cluster shows every
    member's role (keep vs drop_candidate) and the reasoning behind it, never a bare score.

    Column-projected and chunked rather than full-entity/unbounded-IN selects --
    confirmed live, 2026-08-13: this endpoint (called on every page load) OOM-killed the
    API pod (6Gi limit) twice within minutes, same root cause already found and fixed in
    cryo.intelligence.dedup.pipeline._replace_clusters (select(Table)/select(TableStats)
    for the whole cluster-membership set, plus a per-cluster max-similarity query -- 498
    of them on the real fleet's data -- each with its own unbounded IN clause). See that
    module's docstring for the fuller investigation writeup this fix reuses.
    """
    latest_generation = (
        session.execute(
            select(TableSimilarityCluster.generation).order_by(
                TableSimilarityCluster.generation.desc()
            )
        )
        .scalars()
        .first()
    )

    if latest_generation is None:
        return SimilarityClustersResponse(clusters=[], generation=None)

    rows = session.execute(
        select(
            TableSimilarityCluster.cluster_id,
            TableSimilarityCluster.table_id,
            TableSimilarityCluster.role,
            TableSimilarityCluster.keep_score,
            TableSimilarityCluster.keep_reason,
            TableSimilarityCluster.computed_at,
        ).where(TableSimilarityCluster.generation == latest_generation)
    ).all()

    table_ids = list({row.table_id for row in rows})
    chunk_size = 1000

    tables_by_id: dict[UUID, tuple[str, str]] = {}
    for start in range(0, len(table_ids), chunk_size):
        chunk = table_ids[start : start + chunk_size]
        for table_id, schema_name, table_name in session.execute(
            select(Table.id, Table.schema_name, Table.table_name).where(Table.id.in_(chunk))
        ).all():
            tables_by_id[table_id] = (schema_name, table_name)

    stats_by_id: dict[UUID, tuple[float, int]] = {}
    for start in range(0, len(table_ids), chunk_size):
        chunk = table_ids[start : start + chunk_size]
        for table_id, total_size_gb, query_count_30d in session.execute(
            select(TableStats.table_id, TableStats.total_size_gb, TableStats.query_count_30d).where(
                TableStats.table_id.in_(chunk)
            )
        ).all():
            stats_by_id[table_id] = (total_size_gb, query_count_30d)

    # table_to_cluster maps each table to its cluster so a pair's two sides (which
    # always belong to the same cluster once both are assigned) resolve to one
    # cluster_id -- a pair with only one side clustered contributes nothing. See
    # _max_pair_similarity_by_cluster's docstring for why this is chunked+OR'd rather
    # than one unbounded query per cluster.
    table_to_cluster: dict[UUID, UUID] = {row.table_id: row.cluster_id for row in rows}
    max_score_by_cluster = _max_pair_similarity_by_cluster(session, table_ids, table_to_cluster)

    by_cluster: dict[UUID, list] = {}
    for row in rows:
        by_cluster.setdefault(row.cluster_id, []).append(row)

    clusters: list[SimilarityClusterRead] = []
    for cluster_id, members_raw in by_cluster.items():
        members: list[SimilarityClusterMemberRead] = []
        total_size = 0.0
        reclaimable = 0.0
        for m in members_raw:
            table = tables_by_id.get(m.table_id)
            stats = stats_by_id.get(m.table_id)
            if table is None:
                continue
            schema_name, table_name = table
            size_gb = stats[0] if stats else 0.0
            total_size += size_gb
            if m.role == "drop_candidate":
                reclaimable += size_gb
            members.append(
                SimilarityClusterMemberRead(
                    table_id=m.table_id,
                    table_name=table_name,
                    schema_name=schema_name,
                    total_size_gb=size_gb,
                    role=m.role,
                    keep_score=m.keep_score,
                    keep_reason=m.keep_reason,
                    query_count_30d=stats[1] if stats else 0,
                )
            )

        if reclaimable < min_reclaimable_gb:
            continue

        clusters.append(
            SimilarityClusterRead(
                cluster_id=cluster_id,
                members=members,
                total_size_gb=round(total_size, 3),
                reclaimable_gb=round(reclaimable, 3),
                max_pair_similarity=max_score_by_cluster.get(cluster_id),
                generation=latest_generation,
                computed_at=members_raw[0].computed_at,
            )
        )

    clusters.sort(key=lambda c: c.reclaimable_gb, reverse=True)

    return SimilarityClustersResponse(
        clusters=clusters,
        generation=latest_generation,
        total_clusters=len(clusters),
        total_tables_in_clusters=sum(len(c.members) for c in clusters),
        total_reclaimable_gb=round(sum(c.reclaimable_gb for c in clusters), 3),
    )


@router.get(
    "/dedup/pairs",
    response_model=list[SimilarityPairRead],
    dependencies=[Depends(require_viewer)],
)
def list_similarity_pairs(
    session: SessionDep,
    min_score: Annotated[float, Query(ge=0, le=1)] = 0.0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[SimilarityPairRead]:
    """Flat pair list above ``min_score``, highest first -- used for a debugging/detail
    table view alongside the cluster graph, and by the frontend's edge list for the bubble
    chart's connecting lines."""
    rows = (
        session.execute(
            select(TableSimilarityPair)
            .where(TableSimilarityPair.similarity_score >= min_score)
            .order_by(TableSimilarityPair.similarity_score.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

    table_ids = {r.table_a_id for r in rows} | {r.table_b_id for r in rows}
    tables_by_id = {
        t.id: t
        for t in session.execute(select(Table).where(Table.id.in_(table_ids))).scalars().all()
    }

    results: list[SimilarityPairRead] = []
    for row in rows:
        table_a = tables_by_id.get(row.table_a_id)
        table_b = tables_by_id.get(row.table_b_id)
        if table_a is None or table_b is None:
            continue
        results.append(
            SimilarityPairRead(
                id=row.id,
                table_a_id=row.table_a_id,
                table_a_name=qualified_table_name(table_a),
                table_b_id=row.table_b_id,
                table_b_name=qualified_table_name(table_b),
                stage=row.stage,
                similarity_score=row.similarity_score,
                schema_similarity=row.schema_similarity,
                stats_similarity=row.stats_similarity,
                row_jaccard_estimate=row.row_jaccard_estimate,
                naming_pattern_boost=row.naming_pattern_boost,
                file_overlap_detected=row.file_overlap_detected,
                verified_relationship=row.verified_relationship,
                exact_row_overlap_pct=row.exact_row_overlap_pct,
                score_components=row.score_components,
                computed_at=row.computed_at,
            )
        )
    return results


@router.post(
    "/dedup/verify",
    response_model=SimilarityVerifyResponse,
    dependencies=[Depends(require_operator)],
)
def verify_similarity_pair(
    request: SimilarityVerifyRequest, session: SessionDep
) -> SimilarityVerifyResponse:
    """Stage 3: run the real exact Trino row-overlap comparison for one pair, on demand.
    Operator-triggered only -- design doc SS10: too expensive to run automatically against
    every high-scoring pair, and this is the one stage that touches real data volume.
    """
    from cryo.db.models import Destination

    table_a = session.get(Table, request.table_a_id)
    table_b = session.get(Table, request.table_b_id)
    if table_a is None or table_b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "table not found")

    dest = (
        session.execute(
            select(Destination).where(Destination.engine == "trino", Destination.enabled.is_(True))
        )
        .scalars()
        .first()
    )
    if dest is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "no enabled Trino destination configured"
        )

    result = verify_pair_exact(
        table_a, table_b, trino_config=dest.config, row_limit=request.row_limit
    )

    pair = session.execute(
        select(TableSimilarityPair).where(
            TableSimilarityPair.table_a_id.in_([request.table_a_id, request.table_b_id]),
            TableSimilarityPair.table_b_id.in_([request.table_a_id, request.table_b_id]),
        )
    ).scalar_one_or_none()
    if pair is not None:
        pair.stage = "verified"
        pair.verified_relationship = result.relationship.value
        pair.exact_row_overlap_pct = max(result.a_rows_found_in_b_pct, result.b_rows_found_in_a_pct)
        session.commit()

    return SimilarityVerifyResponse(
        table_a_id=request.table_a_id,
        table_b_id=request.table_b_id,
        relationship=result.relationship.value,
        a_rows_found_in_b_pct=result.a_rows_found_in_b_pct,
        b_rows_found_in_a_pct=result.b_rows_found_in_a_pct,
        rows_compared_a=result.rows_compared_a,
        rows_compared_b=result.rows_compared_b,
    )
