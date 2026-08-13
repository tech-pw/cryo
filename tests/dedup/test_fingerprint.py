"""Tests for cryo.intelligence.dedup.fingerprint -- Stage 1 metadata-only candidate
generation (docs/TABLE_DEDUPLICATION_DESIGN.md SS3, SS10)."""

from __future__ import annotations

from cryo.intelligence.dedup.fingerprint import (
    SchemaFingerprint,
    generate_candidate_pairs,
    schema_jaccard_similarity,
)

_COMMON_SCHEMA = (
    ("id", "bigint"),
    ("name", "varchar"),
    ("created_at", "timestamp"),
)


def _fp(
    table_id: str,
    schema_name: str,
    table_name: str,
    *,
    columns: tuple[tuple[str, str], ...] = _COMMON_SCHEMA,
    size_gb: float = 100.0,
    records: int = 1_000_000,
    files: int = 50,
) -> SchemaFingerprint:
    return SchemaFingerprint(
        table_id=table_id,
        schema_name=schema_name,
        table_name=table_name,
        unordered_columns=frozenset(columns),
        ordered_columns=tuple(sorted(columns)),
        total_size_gb=size_gb,
        total_records=records,
        total_files=files,
    )


def test_schema_jaccard_identical_schemas_is_1() -> None:
    a = _fp("t1", "store", "orders")
    b = _fp("t2", "store", "orders_backup")
    assert schema_jaccard_similarity(a, b) == 1.0


def test_schema_jaccard_disjoint_schemas_is_0() -> None:
    a = _fp("t1", "store", "orders", columns=(("id", "bigint"),))
    b = _fp("t2", "store", "products", columns=(("sku", "varchar"),))
    assert schema_jaccard_similarity(a, b) == 0.0


def test_schema_jaccard_partial_overlap() -> None:
    a = _fp("t1", "store", "orders", columns=(("id", "bigint"), ("name", "varchar")))
    b = _fp("t2", "store", "orders2", columns=(("id", "bigint"), ("email", "varchar")))
    # intersection={id}, union={id,name,email} -> 1/3
    assert schema_jaccard_similarity(a, b) == 1 / 3


def test_candidate_generation_pairs_identical_schema_same_size() -> None:
    a = _fp("t1", "store", "orders", size_gb=100.0)
    b = _fp("t2", "store", "orders_backup", size_gb=95.0)
    result = generate_candidate_pairs([a, b])

    assert len(result.candidates) == 1
    pair = result.candidates[0]
    assert pair.schema_exact_match is True
    assert pair.naming_pattern_hint is True


def test_candidate_generation_excludes_wildly_different_sizes_with_no_naming_hint() -> None:
    """A 4GB dimension table and a 4TB fact table sharing a schema by coincidence
    (design doc SS9's named false-positive risk) should not survive blocking when
    there's no naming-pattern hint to justify the wide size gap."""
    small = _fp("t1", "store", "dim_status", size_gb=4.0)
    large = _fp("t2", "store", "fact_events", size_gb=4000.0)
    result = generate_candidate_pairs([small, large])

    assert result.candidates == []


def test_candidate_generation_keeps_wide_ratio_when_naming_pattern_matches() -> None:
    """A genuine containment/temp-copy pair can be a small fraction of its parent's size
    -- naming pattern hint should keep it in the candidate set even outside the size-ratio
    band (design doc SS3/SS10: containment pairs must not be excluded by a tight size
    requirement)."""
    parent = _fp("t1", "store", "orders", size_gb=1000.0)
    tiny_temp = _fp("t2", "store", "orders_temp", size_gb=1.0)
    result = generate_candidate_pairs([parent, tiny_temp])

    assert len(result.candidates) == 1
    assert result.candidates[0].naming_pattern_hint is True


def test_candidate_generation_no_pair_for_unrelated_single_table() -> None:
    lone = _fp("t1", "store", "orders")
    result = generate_candidate_pairs([lone])
    assert result.candidates == []


def test_candidate_generation_versioned_naming_pattern() -> None:
    a = _fp("t1", "mview", "gold_dbt_video_stats", size_gb=187.0)
    b = _fp("t2", "mview", "gold_dbt_video_stats_v3", size_gb=406.0)
    result = generate_candidate_pairs([a, b])

    assert len(result.candidates) == 1
    assert result.candidates[0].naming_pattern_hint is True


def test_candidate_generation_naming_pattern_does_not_cross_schemas() -> None:
    """Two tables with the same base name but in DIFFERENT schemas are not a
    parent-copy pair just because the names match -- naming hint must be schema-scoped."""
    a = _fp("t1", "store_us", "orders_backup", size_gb=100.0)
    b = _fp("t2", "store_eu", "orders", size_gb=100.0)
    result = generate_candidate_pairs([a, b])

    # Still paired via exact schema-bucket blocking (same columns, size within ratio), but
    # naming_pattern_hint must be False since schemas differ.
    assert len(result.candidates) == 1
    assert result.candidates[0].naming_pattern_hint is False


def test_candidate_generation_does_not_duplicate_pairs_across_blocking_passes() -> None:
    """A pair that qualifies via BOTH exact-schema-bucket blocking AND the
    naming-pattern-same-schema-name pass must appear exactly once, not twice."""
    a = _fp("t1", "store", "orders", size_gb=100.0)
    b = _fp("t2", "store", "orders_backup", size_gb=95.0)
    result = generate_candidate_pairs([a, b])

    assert len(result.candidates) == 1


def test_total_pairs_possible_is_correct_n_choose_2() -> None:
    fps = [_fp(f"t{i}", "store", f"table_{i}") for i in range(5)]
    result = generate_candidate_pairs(fps)
    assert result.total_pairs_possible == 10  # 5 choose 2
