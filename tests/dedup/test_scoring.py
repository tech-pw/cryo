"""Tests for cryo.intelligence.dedup.scoring -- Stage 2 composite similarity scoring
(docs/TABLE_DEDUPLICATION_DESIGN.md SS4, SS10)."""

from __future__ import annotations

import pytest

from cryo.intelligence.dedup.fingerprint import CandidatePair, SchemaFingerprint
from cryo.intelligence.dedup.scoring import score_candidate_pair

_SCHEMA = (("id", "bigint"), ("name", "varchar"))


def _fp(
    table_id: str, size_gb: float, records: int, *, naming: str = "orders"
) -> SchemaFingerprint:
    return SchemaFingerprint(
        table_id=table_id,
        schema_name="store",
        table_name=naming,
        unordered_columns=frozenset(_SCHEMA),
        ordered_columns=tuple(sorted(_SCHEMA)),
        total_size_gb=size_gb,
        total_records=records,
        total_files=10,
    )


def _pair(
    a: SchemaFingerprint, b: SchemaFingerprint, *, naming_hint: bool = False
) -> CandidatePair:
    return CandidatePair(
        table_a=a, table_b=b, schema_exact_match=True, naming_pattern_hint=naming_hint
    )


def test_identical_tables_score_near_1_without_sample() -> None:
    a = _fp("t1", 100.0, 1_000_000)
    b = _fp("t2", 100.0, 1_000_000)
    result = score_candidate_pair(_pair(a, b))

    # schema=1.0, stats=1.0, no sample -> reweighted avg = 1.0 (modulo float precision).
    assert result.composite_score == pytest.approx(1.0)


def test_row_jaccard_estimate_is_weighted_into_composite() -> None:
    a = _fp("t1", 100.0, 1_000_000)
    b = _fp("t2", 100.0, 1_000_000)

    high_overlap = score_candidate_pair(_pair(a, b), row_jaccard_estimate=0.95)
    low_overlap = score_candidate_pair(_pair(a, b), row_jaccard_estimate=0.05)

    assert high_overlap.composite_score > low_overlap.composite_score
    assert high_overlap.row_jaccard_estimate == 0.95
    assert low_overlap.row_jaccard_estimate == 0.05


def test_naming_pattern_boost_is_capped_and_cannot_dominate() -> None:
    """The naming boost must nudge a plausible pair, never manufacture a high score for
    an otherwise weak pair (design doc SS8: naming heuristics are a tie-breaker only)."""
    a = _fp("t1", 100.0, 1_000_000)
    weak_b = SchemaFingerprint(
        table_id="t2",
        schema_name="store",
        table_name="orders_backup",
        unordered_columns=frozenset({("totally", "different")}),
        ordered_columns=(("totally", "different"),),
        total_size_gb=0.001,
        total_records=1,
        total_files=1,
    )
    result = score_candidate_pair(_pair(a, weak_b, naming_hint=True))

    # Even with the naming boost, a pair with near-zero schema/stats similarity must not
    # score anywhere near "recommend drop" territory.
    assert result.composite_score < 0.3


def test_file_overlap_detected_forces_score_to_1() -> None:
    """Physical file-hash overlap is definitive proof, not an estimate -- design doc
    SS3.2: should short-circuit the composite regardless of other signals."""
    a = _fp("t1", 100.0, 1_000_000)
    b = _fp("t2", 1.0, 1)  # deliberately look unrelated on every other signal
    result = score_candidate_pair(_pair(a, b), file_overlap_detected=True)

    assert result.composite_score == 1.0
    assert result.file_overlap_detected is True


def test_component_breakdown_is_fully_populated_for_evidence_trail() -> None:
    """Every score must carry its full component breakdown -- design doc SS7/SS9: no
    bare numeric score for something that can end in a drop recommendation."""
    a = _fp("t1", 100.0, 1_000_000)
    b = _fp("t2", 100.0, 1_000_000)
    result = score_candidate_pair(_pair(a, b), row_jaccard_estimate=0.8)

    components = result.to_dict()
    assert set(components.keys()) == {
        "schema_similarity",
        "stats_similarity",
        "row_jaccard_estimate",
        "naming_pattern_boost",
        "file_overlap_detected",
        "composite_score",
    }
    assert components["row_jaccard_estimate"] == 0.8


def test_wildly_different_sizes_score_low_stats_similarity() -> None:
    a = _fp("t1", 4.0, 10_000)
    b = _fp("t2", 4000.0, 10_000_000)
    result = score_candidate_pair(_pair(a, b))

    assert result.stats_similarity < 0.05


def test_composite_score_never_exceeds_1() -> None:
    a = _fp("t1", 100.0, 1_000_000)
    b = _fp("t2", 100.0, 1_000_000)
    result = score_candidate_pair(_pair(a, b, naming_hint=True), row_jaccard_estimate=1.0)

    assert result.composite_score <= 1.0
