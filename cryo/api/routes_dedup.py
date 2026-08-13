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
    """
    latest_generation = session.execute(
        select(TableSimilarityCluster.generation).order_by(
            TableSimilarityCluster.generation.desc()
        )
    ).scalars().first()

    if latest_generation is None:
        return SimilarityClustersResponse(clusters=[], generation=None)

    rows = session.execute(
        select(TableSimilarityCluster).where(
            TableSimilarityCluster.generation == latest_generation
        )
    ).scalars().all()

    table_ids = {row.table_id for row in rows}
    tables_by_id = {
        t.id: t
        for t in session.execute(select(Table).where(Table.id.in_(table_ids))).scalars().all()
    }
    stats_by_id = {
        s.table_id: s
        for s in session.execute(select(TableStats).where(TableStats.table_id.in_(table_ids)))
        .scalars()
        .all()
    }

    by_cluster: dict[UUID, list[TableSimilarityCluster]] = {}
    for row in rows:
        by_cluster.setdefault(row.cluster_id, []).append(row)

    # Pull max pairwise score per cluster's member set for display -- best-effort, one
    # query per cluster is acceptable here since cluster count is expected to stay small
    # (a handful to low hundreds, not thousands) relative to the fleet's table count.
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
            size_gb = stats.total_size_gb if stats else 0.0
            total_size += size_gb
            if m.role == "drop_candidate":
                reclaimable += size_gb
            members.append(
                SimilarityClusterMemberRead(
                    table_id=m.table_id,
                    table_name=table.table_name,
                    schema_name=table.schema_name,
                    total_size_gb=size_gb,
                    role=m.role,  # type: ignore[arg-type]
                    keep_score=m.keep_score,
                    keep_reason=m.keep_reason,
                    query_count_30d=stats.query_count_30d if stats else 0,
                )
            )

        if reclaimable < min_reclaimable_gb:
            continue

        member_table_ids = [m.table_id for m in members_raw]
        max_score = session.execute(
            select(TableSimilarityPair.similarity_score)
            .where(
                TableSimilarityPair.table_a_id.in_(member_table_ids),
                TableSimilarityPair.table_b_id.in_(member_table_ids),
            )
            .order_by(TableSimilarityPair.similarity_score.desc())
        ).scalars().first()

        clusters.append(
            SimilarityClusterRead(
                cluster_id=cluster_id,
                members=members,
                total_size_gb=round(total_size, 3),
                reclaimable_gb=round(reclaimable, 3),
                max_pair_similarity=max_score,
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
    rows = session.execute(
        select(TableSimilarityPair)
        .where(TableSimilarityPair.similarity_score >= min_score)
        .order_by(TableSimilarityPair.similarity_score.desc())
        .limit(limit)
    ).scalars().all()

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

    dest = session.execute(
        select(Destination).where(Destination.engine == "trino", Destination.enabled.is_(True))
    ).scalars().first()
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
