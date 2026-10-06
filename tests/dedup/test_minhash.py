"""Tests for cryo.intelligence.dedup.minhash -- the MinHash/LSH row-set Jaccard
similarity estimator (docs/TABLE_DEDUPLICATION_DESIGN.md SS4.1). Correctness here matters
more than most modules in this codebase: a wrong similarity estimate feeds directly into
a drop recommendation for real production data.
"""

from __future__ import annotations

import random

from cryo.intelligence.dedup.minhash import (
    compute_minhash_signature,
    lsh_band_keys,
)


def _row_hashes(n: int, *, seed: int) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(63) for _ in range(n)]


def test_identical_row_sets_score_jaccard_1() -> None:
    rows = _row_hashes(5000, seed=1)
    sig_a = compute_minhash_signature(rows)
    sig_b = compute_minhash_signature(rows)

    assert sig_a.jaccard_similarity(sig_b) == 1.0


def test_disjoint_row_sets_score_near_zero() -> None:
    rows_a = _row_hashes(5000, seed=1)
    rows_b = _row_hashes(5000, seed=2)
    # Sanity: the two random samples must not actually overlap for this test to mean
    # anything (astronomically unlikely with 63-bit random hashes, but assert it anyway).
    assert not set(rows_a) & set(rows_b)

    sig_a = compute_minhash_signature(rows_a)
    sig_b = compute_minhash_signature(rows_b)

    # True Jaccard is 0.0 -- MinHash is an estimator, so allow small sampling noise
    # rather than asserting exact 0.0 (128 permutations gives ~8.8% expected error).
    assert sig_a.jaccard_similarity(sig_b) < 0.15


def test_estimated_jaccard_matches_true_jaccard_within_error_bound() -> None:
    """50% true overlap between two 10k-row sets -- MinHash estimate should land within
    the expected O(1/sqrt(128)) ~ 8.8% error band of the true value."""
    shared = _row_hashes(5000, seed=10)
    only_a = _row_hashes(5000, seed=11)
    only_b = _row_hashes(5000, seed=12)

    set_a = shared + only_a
    set_b = shared + only_b
    true_jaccard = len(set(set_a) & set(set_b)) / len(set(set_a) | set(set_b))

    sig_a = compute_minhash_signature(set_a)
    sig_b = compute_minhash_signature(set_b)
    estimated = sig_a.jaccard_similarity(sig_b)

    assert abs(estimated - true_jaccard) < 0.15


def test_signature_is_deterministic_across_calls() -> None:
    """Two independent calls with the same row set must produce byte-identical
    signatures -- required for signatures computed at different times (e.g. table A
    scanned today, table B scanned last week) to remain comparable (see minhash.py's
    _permutation_seeds docstring)."""
    rows = _row_hashes(1000, seed=42)

    sig_1 = compute_minhash_signature(rows)
    sig_2 = compute_minhash_signature(rows)

    assert sig_1.values == sig_2.values


def test_signature_is_order_independent() -> None:
    """Same set, different insertion order -- MinHash over a set must not care about
    iteration order (it's a min over hash values, not a sequence hash)."""
    rows = _row_hashes(1000, seed=7)
    shuffled = list(rows)
    random.Random(99).shuffle(shuffled)

    sig_a = compute_minhash_signature(rows)
    sig_b = compute_minhash_signature(shuffled)

    assert sig_a.values == sig_b.values


def test_empty_row_set_signature_is_self_consistent() -> None:
    sig_a = compute_minhash_signature([])
    sig_b = compute_minhash_signature([])

    # Two empty sets: Jaccard is conventionally 1.0 (both "contain nothing"), and this
    # implementation's max-hash-sentinel signature achieves that via equal signatures.
    assert sig_a.jaccard_similarity(sig_b) == 1.0


def test_jaccard_similarity_rejects_mismatched_signature_lengths() -> None:
    sig_a = compute_minhash_signature(_row_hashes(10, seed=1))
    # Construct a signature with a different length by hand -- this should never happen
    # in real pipeline use (all signatures use the same NUM_PERMUTATIONS), but the
    # explicit check exists precisely to fail loudly rather than silently compare
    # truncated/zipped signatures if that invariant is ever violated.
    from dataclasses import replace

    bad_sig = replace(sig_a, values=sig_a.values[:10])

    import pytest

    with pytest.raises(ValueError, match="signature length mismatch"):
        sig_a.jaccard_similarity(bad_sig)


def test_lsh_band_keys_produces_expected_band_count() -> None:
    sig = compute_minhash_signature(_row_hashes(100, seed=1))
    bands = lsh_band_keys(sig, num_bands=32)

    assert len(bands) == 32
    assert {b.band_index for b in bands} == set(range(32))


def test_lsh_band_keys_identical_signatures_collide_in_every_band() -> None:
    rows = _row_hashes(500, seed=3)
    sig_a = compute_minhash_signature(rows)
    sig_b = compute_minhash_signature(rows)

    bands_a = lsh_band_keys(sig_a, num_bands=32)
    bands_b = lsh_band_keys(sig_b, num_bands=32)

    assert [b.band_hash for b in bands_a] == [b.band_hash for b in bands_b]


def test_lsh_band_keys_rejects_non_divisor_band_count() -> None:
    import pytest

    sig = compute_minhash_signature(_row_hashes(10, seed=1))
    with pytest.raises(ValueError, match="must evenly divide"):
        lsh_band_keys(sig, num_bands=17)  # 128 % 17 != 0
