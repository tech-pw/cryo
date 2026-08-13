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
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
    # Accumulated in memory and written with one bulk upsert after the loop, not a
    # select-then-insert round trip per pair -- confirmed live, 2026-08-13: against this
    # fleet's real 51,706-pair Stage-2 candidate set, one Postgres round trip per pair
    # (the original _upsert_pair pattern) was slow enough that a single pipeline run
    # exceeded a 90s exec timeout with zero rows committed (the whole run is one
    # transaction; a killed client connection rolls it all back). A single
    # INSERT ... ON CONFLICT DO UPDATE statement over all rows is one round trip
    # regardless of candidate count.
    pending_pairs: list[dict[str, object]] = []

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
        pending_pairs.append(
            _pair_upsert_row(a_id, b_id, final_score, stage="scored" if row_jaccard else "blocked")
        )
        pairs_persisted += 1
        edges.append(
            SimilarityEdge(
                table_a_id=a_id, table_b_id=b_id, similarity_score=final_score.composite_score
            )
        )

    _bulk_upsert_pairs(session, pending_pairs)
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


def _pair_upsert_row(
    a_id: str, b_id: str, score: ScoreComponents, *, stage: str
) -> dict[str, object]:
    return {
        "table_a_id": a_id,
        "table_b_id": b_id,
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


def _bulk_upsert_pairs(session: Session, rows: list[dict[str, object]]) -> None:
    """One INSERT ... ON CONFLICT DO UPDATE per batch, not one round trip per pair --
    see run_pipeline's docstring note on why (a real 90s timeout against 51,706 real
    candidate pairs, zero rows committed, confirmed live 2026-08-13). Batched (not one
    giant statement) to keep a single INSERT's parameter count and lock duration
    reasonable regardless of how large a single pipeline run's candidate set gets.
    """
    if not rows:
        return
    batch_size = 2000
    update_columns = {
        col: getattr(pg_insert(TableSimilarityPair).excluded, col)
        for col in (
            "stage",
            "similarity_score",
            "schema_similarity",
            "stats_similarity",
            "row_jaccard_estimate",
            "naming_pattern_boost",
            "file_overlap_detected",
            "score_components",
            "computed_at",
        )
    }
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        stmt = pg_insert(TableSimilarityPair).values(batch)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_table_similarity_pair",
            set_=update_columns,
        )
        session.execute(stmt)


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
    # fingerprint) for the unscoped-full-fleet-select version of this same class of bug.
    #
    # Chunked into batches of 1000 IDs per query, and column-projected rather than
    # select(TableStats) for full ORM entities -- defensive hardening after a real OOM,
    # 2026-08-13: the first fleet-wide run OOM-killed the API pod (6Gi limit) somewhere
    # inside this function against ~5,245 clustered tables. Isolated component testing
    # (this query alone, the DELETE alone, the insert loop alone, all against the real
    # data) each completed cleanly in under a second with negligible memory delta, and a
    # synthetic local reproduction shaped like the real fleet (giant coincidental-schema
    # bucket, ~1.9M candidates) also ran cleanly on both the pre-fix and post-fix code --
    # the exact trigger was never conclusively isolated, and is suspected to be tied to
    # live prod conditions (connection/session state accumulated across a long-running
    # request, or load on the shared Postgres instance) that neither isolated component
    # tests nor an idle local DB reproduce. This change (chunking + synchronize_session
    # below) is real, defensible hardening regardless -- an unbounded IN-list and
    # identity-map-synchronized bulk delete are both legitimate risk factors even without
    # a fully confirmed root cause -- shipped with closer live monitoring on the next
    # real run rather than further blocking on a repro that wasn't reproducing.
    cluster_table_ids = [tables_by_id[t].id for t in cluster_assignments if t in tables_by_id]
    stats_by_id: dict[Any, tuple[float, int]] = {}
    chunk_size = 1000
    for start in range(0, len(cluster_table_ids), chunk_size):
        chunk = cluster_table_ids[start : start + chunk_size]
        for table_id, total_size_gb, query_count_30d in session.execute(
            select(TableStats.table_id, TableStats.total_size_gb, TableStats.query_count_30d).where(
                TableStats.table_id.in_(chunk)
            )
        ).all():
            stats_by_id[table_id] = (total_size_gb, query_count_30d)

    now = datetime.now(UTC)
    cluster_inputs: dict[str, TableClusterInput] = {}
    for table_id in cluster_assignments:
        table = tables_by_id.get(table_id)
        fp = fingerprints_by_id.get(table_id)
        if table is None or fp is None:
            continue
        stats = stats_by_id.get(table.id)
        total_size_gb, query_count_30d = stats if stats else (fp.total_size_gb, 0)
        cluster_inputs[table_id] = TableClusterInput(
            table_id=table_id,
            schema_name=table.schema_name,
            table_name=table.table_name,
            total_size_gb=total_size_gb,
            created_at=table.first_seen_at,
            query_count_30d=query_count_30d,
            naming_pattern_hint=False,  # already folded into the score that formed the cluster edge
        )

    members = recommend_cluster_roles(cluster_assignments, cluster_inputs, now=now)

    # synchronize_session=False -- also part of the 2026-08-13 hardening above. ORM
    # delete() defaults to synchronize_session="auto" (evaluate, falling back to fetch),
    # which evaluates the WHERE clause against every object currently tracked in the
    # session's identity map -- here that's tables_by_id's ~11,711 full Table entities
    # (run_pipeline) plus everything else accumulated by this point in a long-running
    # session. This DELETE doesn't need in-Python session sync at all (nothing in this
    # function holds a stale reference to a deleted TableSimilarityCluster row
    # afterward), so skipping it removes a real identity-map-scan cost regardless of
    # whether it was the specific trigger for the OOM this hardening responds to.
    session.execute(
        delete(TableSimilarityCluster)
        .where(TableSimilarityCluster.generation < next_generation)
        .execution_options(synchronize_session=False)
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
