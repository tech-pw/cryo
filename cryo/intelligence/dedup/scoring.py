"""Stage 2: composite similarity scoring for Stage-1 candidate pairs
(docs/TABLE_DEDUPLICATION_DESIGN.md SS4, SS10).

Combines schema similarity, size/row-count statistical similarity, and (optionally, when
a Trino connection is available) a MinHash-estimated row-set Jaccard similarity into a
single weighted composite score in [0, 1]. A simple, stated weighted average is used
rather than a learned/ML model deliberately -- design doc SS10: "a simple, stated
weighting beats an opaque model for a tool that recommends deleting data." Every component
is persisted alongside the composite so a human reviewer (or the frontend similarity page)
can see exactly why a pair scored the way it did, not just a bare number.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryo.intelligence.dedup.fingerprint import CandidatePair, schema_jaccard_similarity
from cryo.intelligence.dedup.minhash import MinHashSignature

# Weights sum to 1.0. Schema similarity weighted highest -- it's the cheapest, most
# reliable signal (structural, not estimated). Row-Jaccard (when available) is weighted
# second -- it's the closest proxy to "do these tables actually hold the same data," but
# is itself an estimate (see minhash.py). Stats similarity is the weakest standalone
# signal (design doc SS9: coincidental schema+size match is a real, explicit false-positive
# risk) so it's weighted lowest and exists mainly to break ties / add corroboration.
_WEIGHT_SCHEMA = 0.35
_WEIGHT_STATS = 0.20
_WEIGHT_ROW_JACCARD = 0.45
# When no row sample was taken (Stage 2 ran metadata-only, e.g. Trino unavailable or the
# pair didn't clear a size/cost gate for sampling), redistribute the row-Jaccard weight
# proportionally across the two cheap signals rather than silently treating "no data" as
# "zero similarity" -- a schema-identical, stats-identical pair with no sample taken yet
# should score as "promising, needs verification," not "clearly unrelated."
_WEIGHT_SCHEMA_NO_SAMPLE = _WEIGHT_SCHEMA / (_WEIGHT_SCHEMA + _WEIGHT_STATS)
_WEIGHT_STATS_NO_SAMPLE = _WEIGHT_STATS / (_WEIGHT_SCHEMA + _WEIGHT_STATS)

# Naming-pattern hint is a tie-breaker/confidence booster only (design doc SS8), added on
# top of the weighted composite and capped so it can never alone push an otherwise-low
# score into "recommend drop" territory -- it can only nudge a pair that's already
# structurally/statistically plausible.
_NAMING_PATTERN_BOOST = 0.08


@dataclass(frozen=True, slots=True)
class ScoreComponents:
    schema_similarity: float
    stats_similarity: float
    row_jaccard_estimate: float | None
    naming_pattern_boost: float
    file_overlap_detected: bool
    composite_score: float

    def to_dict(self) -> dict[str, float | bool | None]:
        """Full breakdown persisted to TableSimilarityPair.score_components -- the
        evidence trail design doc SS7/SS9 requires for anything that can end in a drop
        recommendation."""
        return {
            "schema_similarity": round(self.schema_similarity, 4),
            "stats_similarity": round(self.stats_similarity, 4),
            "row_jaccard_estimate": (
                round(self.row_jaccard_estimate, 4)
                if self.row_jaccard_estimate is not None
                else None
            ),
            "naming_pattern_boost": round(self.naming_pattern_boost, 4),
            "file_overlap_detected": self.file_overlap_detected,
            "composite_score": round(self.composite_score, 4),
        }


def _stats_similarity(pair: CandidatePair) -> float:
    """Cosine-similarity-style comparison over normalized (row_count, byte_size) --
    design doc SS4.3. Two tables that are identical in row count and byte size score 1.0;
    tables that differ by an order of magnitude in either score much lower. This is
    deliberately coarse (just two dimensions, not a full column-stat vector) for the v1
    implementation -- see design doc SS11's open question about a richer column-profile
    vector (distinct counts, null%, top-k values) once Puffin sketch blobs
    (ColumnStatsSketch) are cheaply readable rather than locator-only pointers.
    """
    a, b = pair.table_a, pair.table_b
    size_a, size_b = max(a.total_size_gb, 1e-9), max(b.total_size_gb, 1e-9)
    size_ratio = min(size_a, size_b) / max(size_a, size_b)

    rows_a = a.total_records or 0
    rows_b = b.total_records or 0
    if rows_a == 0 and rows_b == 0:
        row_ratio = 1.0
    else:
        row_ratio = min(rows_a, rows_b) / max(rows_a, rows_b, 1)

    # Simple average of the two ratio dimensions -- both already normalized to [0, 1] by
    # construction (min/max of two positive quantities), so no further scaling needed.
    return (size_ratio + row_ratio) / 2.0


def score_candidate_pair(
    pair: CandidatePair,
    *,
    row_jaccard_estimate: float | None = None,
    file_overlap_detected: bool = False,
) -> ScoreComponents:
    """Compute the Stage 2 composite score for one candidate pair.

    ``row_jaccard_estimate`` should come from comparing two MinHashSignature objects via
    MinHashSignature.jaccard_similarity() (dedup.minhash) -- pass None when no row sample
    was taken (e.g. this pair hasn't been promoted to sampling yet, or Trino was
    unavailable), which reweights the composite across the remaining signals rather than
    penalizing the pair for missing data (see _WEIGHT_*_NO_SAMPLE above).

    ``file_overlap_detected`` (dedup.fingerprint's future S3 file-hash-intersection check,
    design doc SS3.2 / SS11 open question) is not yet wired into the composite's weighted
    sum -- when true it's the single strongest possible signal (proof of physical file
    identity, not statistical similarity) and should short-circuit straight to a
    high-confidence recommendation rather than being diluted into an average. Recorded here
    so the schema/pipeline are ready for it once the S3 ETag/checksum lookup (design doc
    SS11) is implemented.
    """
    schema_sim = schema_jaccard_similarity(pair.table_a, pair.table_b)
    stats_sim = _stats_similarity(pair)

    if row_jaccard_estimate is not None:
        composite = (
            _WEIGHT_SCHEMA * schema_sim
            + _WEIGHT_STATS * stats_sim
            + _WEIGHT_ROW_JACCARD * row_jaccard_estimate
        )
    else:
        composite = _WEIGHT_SCHEMA_NO_SAMPLE * schema_sim + _WEIGHT_STATS_NO_SAMPLE * stats_sim

    naming_boost = _NAMING_PATTERN_BOOST if pair.naming_pattern_hint else 0.0
    composite = min(1.0, composite + naming_boost)

    if file_overlap_detected:
        # Physical file identity is definitive proof, not an estimate -- design doc SS3.2.
        composite = 1.0

    return ScoreComponents(
        schema_similarity=schema_sim,
        stats_similarity=stats_sim,
        row_jaccard_estimate=row_jaccard_estimate,
        naming_pattern_boost=naming_boost,
        file_overlap_detected=file_overlap_detected,
        composite_score=composite,
    )


def row_jaccard_from_signatures(sig_a: MinHashSignature, sig_b: MinHashSignature) -> float:
    """Thin wrapper kept here (rather than requiring every caller to import minhash
    directly) so dedup.scoring stays the single import surface for "how similar are these
    two tables" -- dedup.pipeline only needs to import fingerprint + sampling + scoring."""
    return sig_a.jaccard_similarity(sig_b)
