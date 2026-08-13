"""Orchestrates the full table-dedup pipeline (docs/TABLE_DEDUPLICATION_DESIGN.md SS10):
Stage 1 candidate generation -> Stage 2 statistical scoring -> clustering -> persisted
recommendations. Stage 3 exact verification is available (dedup.verification) but run
separately/on-demand per pair from the API, not as part of the routine full-fleet sweep --
it's the one stage expensive enough that it should only run against pairs a human has
already looked at and wants confirmed, not blindly against every high scorer.

This module is the only place that touches the database directly among the dedup
submodules -- fingerprint/scoring/clustering/minhash/sampling/verification all operate on
plain dataclasses and stay independently testable without a live session, matching the
rest of this pipeline's design.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cryo.db.models import (
    Destination,
    Table,
    TableSimilarityCluster,
    TableSimilarityPair,
    TableStats,
)
from cryo.intelligence.dedup.clustering import (
    SimilarityEdge,
    TableClusterInput,
    cluster_similarity_graph,
    recommend_cluster_roles,
)
from cryo.intelligence.dedup.fingerprint import (
    BlockingResult,
    CandidatePair,
    SchemaFingerprint,
    build_schema_fingerprints,
    generate_candidate_pairs,
)
from cryo.intelligence.dedup.minhash import MinHashSignature, compute_minhash_signature
from cryo.intelligence.dedup.sampling import fetch_row_sample
from cryo.intelligence.dedup.scoring import (
    ScoreComponents,
    row_jaccard_from_signatures,
    score_candidate_pair,
)

logger = logging.getLogger(__name__)

# Stage 2 only samples rows (the expensive-relative-to-Stage-1 step) for pairs whose
# metadata-only score already clears this bar -- running MinHash sampling on every
# schema-bucket-blocked pair regardless of how weak the metadata signal already looks
# would spend Trino query budget on pairs Stage 1 already suggests are unlikely.
_MIN_METADATA_SCORE_FOR_SAMPLING = 0.55
# Below this, don't bother persisting a Stage-1-only pair at all -- keeps
# table_similarity_pairs from accumulating rows for schema-bucket collisions that are
# obviously unrelated once size/naming are considered.
_MIN_SCORE_TO_PERSIST = 0.30


@dataclass(slots=True)
class PipelineRunResult:
    tables_considered: int
    candidate_pairs: int
    pairs_sampled: int
    pairs_persisted: int
    clusters_found: int
    cluster_generation: int
    errors: list[str]


def _canonical_pair_order(table_a_id: str, table_b_id: str) -> tuple[str, str]:
    """table_a_id < table_b_id by string comparison, always -- so a pair is never
    persisted twice in both directions (see TableSimilarityPair's docstring)."""
    return (table_a_id, table_b_id) if table_a_id < table_b_id else (table_b_id, table_a_id)


def _default_trino_config(session: Session) -> dict | None:
    """Pick the first enabled Trino destination's config -- matches the single-coordinator
    pattern already used throughout this session's interactive Trino queries
    (coordinators_from_config(dest.config); connect(coords[0])). A fleet with multiple
    Trino destinations would need this parameterized per-source; deferred as an open
    question (design doc SS11) until dedup is validated against a single destination.
    """
    dest = (
        session.execute(
            select(Destination).where(Destination.engine == "trino", Destination.enabled.is_(True))
        )
        .scalars()
        .first()
    )
    return dest.config if dest else None


def run_pipeline(
    session: Session,
    *,
    sample_rows: bool = True,
    cluster_merge_threshold: float = 0.75,
) -> PipelineRunResult:
    """Run Stage 1 + Stage 2 across the whole fleet, then cluster and persist
    recommendations. Safe to run repeatedly -- each run replaces the prior generation's
    cluster rows (TableSimilarityCluster.generation) and upserts pair scores, rather than
    accumulating stale state.
    """
    errors: list[str] = []

    fingerprints = build_schema_fingerprints(session)
    blocking: BlockingResult = generate_candidate_pairs(fingerprints)
    logger.info(
        "dedup pipeline: %d tables considered, %d candidate pairs (of %d possible)",
        blocking.total_tables_considered,
        len(blocking.candidates),
        blocking.total_pairs_possible,
    )

    trino_config = _default_trino_config(session) if sample_rows else None
    if sample_rows and trino_config is None:
        errors.append("no enabled Trino destination found -- Stage 2 row sampling skipped")

    tables_by_id: dict[str, Table] = {
        str(t.id): t
        for t in session.execute(
            select(Table).where(Table.id.in_([fp.table_id for fp in fingerprints]))
        ).scalars()
    }

    edges: list[SimilarityEdge] = []
    pairs_sampled = 0
    pairs_persisted = 0
    signature_cache: dict[str, MinHashSignature] = {}

    for candidate in blocking.candidates:
        metadata_score = score_candidate_pair(candidate)

        row_jaccard = None
        if (
            trino_config is not None
            and metadata_score.composite_score >= _MIN_METADATA_SCORE_FOR_SAMPLING
        ):
            try:
                row_jaccard = _sample_and_score(
                    candidate, trino_config, tables_by_id, signature_cache
                )
                pairs_sampled += 1
            except Exception as exc:  # noqa: BLE001 - one bad pair must not abort the batch
                errors.append(
                    f"sampling failed for {candidate.table_a.table_name}/"
                    f"{candidate.table_b.table_name}: {exc}"
                )

        final_score = score_candidate_pair(candidate, row_jaccard_estimate=row_jaccard)

        if final_score.composite_score < _MIN_SCORE_TO_PERSIST:
            continue

        a_id, b_id = _canonical_pair_order(candidate.table_a.table_id, candidate.table_b.table_id)
        _upsert_pair(session, a_id, b_id, final_score, stage="scored" if row_jaccard else "blocked")
        pairs_persisted += 1
        edges.append(
            SimilarityEdge(
                table_a_id=a_id, table_b_id=b_id, similarity_score=final_score.composite_score
            )
        )

    session.flush()

    # Union-find roots are table_id strings (whichever member happened to become the root
    # during union-by-rank) -- remap to fresh UUIDs before persisting, since
    # TableSimilarityCluster.cluster_id is a UUID column and a table_id string is not a
    # meaningful cluster identity on its own (it's an implementation artifact of which
    # table happened to be unioned first).
    raw_assignments = cluster_similarity_graph(edges, merge_threshold=cluster_merge_threshold)
    root_to_uuid: dict[str, str] = {root: str(uuid4()) for root in set(raw_assignments.values())}
    cluster_assignments = {
        table_id: root_to_uuid[root] for table_id, root in raw_assignments.items()
    }
    cluster_generation = _replace_clusters(session, cluster_assignments, tables_by_id, fingerprints)

    return PipelineRunResult(
        tables_considered=blocking.total_tables_considered,
        candidate_pairs=len(blocking.candidates),
        pairs_sampled=pairs_sampled,
        pairs_persisted=pairs_persisted,
        clusters_found=len(set(cluster_assignments.values())),
        cluster_generation=cluster_generation,
        errors=errors,
    )


def _sample_and_score(
    candidate: CandidatePair,
    trino_config: dict,
    tables_by_id: dict[str, Table],
    signature_cache: dict[str, MinHashSignature],
) -> float | None:
    """Fetch (or reuse cached) MinHash signatures for both sides of a candidate pair and
    return their row-Jaccard estimate. Signatures are cached per table_id within a single
    pipeline run -- a table can appear in many candidate pairs (e.g. one popular schema
    bucket), and its signature only needs computing once."""
    table_a = tables_by_id.get(candidate.table_a.table_id)
    table_b = tables_by_id.get(candidate.table_b.table_id)
    if table_a is None or table_b is None:
        return None

    common_columns = (
        sorted(
            name
            for name, _dtype in (
                candidate.table_a.unordered_columns & candidate.table_b.unordered_columns
            )
        )
        or None
    )

    sig_a = signature_cache.get(candidate.table_a.table_id)
    if sig_a is None:
        sample_a = fetch_row_sample(table_a, trino_config=trino_config, columns=common_columns)
        sig_a = compute_minhash_signature(sample_a.row_hashes)
        signature_cache[candidate.table_a.table_id] = sig_a

    sig_b = signature_cache.get(candidate.table_b.table_id)
    if sig_b is None:
        sample_b = fetch_row_sample(table_b, trino_config=trino_config, columns=common_columns)
        sig_b = compute_minhash_signature(sample_b.row_hashes)
        signature_cache[candidate.table_b.table_id] = sig_b

    return row_jaccard_from_signatures(sig_a, sig_b)


def _upsert_pair(
    session: Session, a_id: str, b_id: str, score: ScoreComponents, *, stage: str
) -> None:
    existing = session.execute(
        select(TableSimilarityPair).where(
            TableSimilarityPair.table_a_id == a_id, TableSimilarityPair.table_b_id == b_id
        )
    ).scalar_one_or_none()

    fields = {
        "stage": stage,
        "similarity_score": score.composite_score,
        "schema_similarity": score.schema_similarity,
        "stats_similarity": score.stats_similarity,
        "row_jaccard_estimate": score.row_jaccard_estimate,
        "naming_pattern_boost": score.naming_pattern_boost,
        "file_overlap_detected": score.file_overlap_detected,
        "score_components": score.to_dict(),
        "computed_at": datetime.now(UTC),
    }
    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
    else:
        session.add(TableSimilarityPair(table_a_id=a_id, table_b_id=b_id, **fields))


def _replace_clusters(
    session: Session,
    cluster_assignments: dict[str, str],
    tables_by_id: dict[str, Table],
    fingerprints_list: list[SchemaFingerprint],
) -> int:
    """Wholesale-replace TableSimilarityCluster rows with a new generation -- design doc
    SS6: cluster membership can legitimately change (chaining, splitting) as new pairs are
    scored, so each run produces a clean, explainable snapshot rather than an incrementally
    patched one."""
    prior_max = (
        session.execute(
            select(TableSimilarityCluster.generation).order_by(
                TableSimilarityCluster.generation.desc()
            )
        )
        .scalars()
        .first()
    )
    next_generation = (prior_max or 0) + 1

    fingerprints_by_id = {fp.table_id: fp for fp in fingerprints_list}
    # Scoped to only the tables actually in a cluster, not select(TableStats) for the
    # whole fleet -- see build_schema_fingerprints' docstring (cryo.intelligence.dedup.
    # fingerprint) for why an unscoped full-fleet ORM select of a large table OOM-killed
    # the API pod live, 2026-08-13. Cluster membership is typically a small fraction of
    # the fleet, so this stays cheap without needing the same column-projection rewrite.
    cluster_table_ids = [tables_by_id[t].id for t in cluster_assignments if t in tables_by_id]
    stats_by_id = {
        row.table_id: row
        for row in session.execute(
            select(TableStats).where(TableStats.table_id.in_(cluster_table_ids))
        )
        .scalars()
        .all()
    }

    now = datetime.now(UTC)
    cluster_inputs: dict[str, TableClusterInput] = {}
    for table_id in cluster_assignments:
        table = tables_by_id.get(table_id)
        fp = fingerprints_by_id.get(table_id)
        if table is None or fp is None:
            continue
        stats = stats_by_id.get(table.id)
        cluster_inputs[table_id] = TableClusterInput(
            table_id=table_id,
            schema_name=table.schema_name,
            table_name=table.table_name,
            total_size_gb=stats.total_size_gb if stats else fp.total_size_gb,
            created_at=table.first_seen_at,
            query_count_30d=stats.query_count_30d if stats else 0,
            naming_pattern_hint=False,  # already folded into the score that formed the cluster edge
        )

    members = recommend_cluster_roles(cluster_assignments, cluster_inputs, now=now)

    session.execute(
        delete(TableSimilarityCluster).where(TableSimilarityCluster.generation < next_generation)
    )
    for member in members:
        session.add(
            TableSimilarityCluster(
                cluster_id=member.cluster_id,
                table_id=member.table_id,
                role=member.role,
                keep_score=member.keep_score,
                keep_reason=member.keep_reason,
                generation=next_generation,
                computed_at=now,
            )
        )
    session.flush()
    return next_generation
