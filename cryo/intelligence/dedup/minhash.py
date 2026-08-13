"""MinHash signatures and LSH banding for row-set Jaccard similarity estimation.

Pure-Python implementation rather than a `datasketch` dependency: the algorithm itself is
small (one hash family + rolling min per signature slot) and this keeps the dedup pipeline
free of a new pip dependency for something a single module covers, matching how Cryo
already has a native Go engine rather than reaching for a JVM dependency where the
algorithm is simple enough to own directly (see docs/TABLE_DEDUPLICATION_DESIGN.md SS4.1).

MinHash estimates Jaccard similarity J(A,B) = |A∩B| / |A∪B| between two sets without
materializing either set in full: hash every element with k independent hash functions,
keep the minimum hash value per function as that set's signature, and the fraction of
signature positions where two sets agree is an unbiased estimator of J(A,B). Error scales
O(1/sqrt(k)) in the number of hash functions -- 128 is a standard, well-tested choice
balancing estimation error against signature size (128 * 8 bytes = 1KB per table, trivial
to store and compare even for tens of thousands of tables).

For table-level dedup specifically, apply MinHash at ROW granularity (each row's hash of
its concatenated column values is one set element), not column-value granularity -- see
the design doc SS4.1 for why this differs from join-discovery systems like JOSIE, which
MinHash column values instead to solve a different problem (finding joinable columns, not
row-set overlap between two tables).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

# 128 independent hash functions is the standard MinHash configuration (e.g. datasketch's
# own default, and the choice used across the LLM-corpus-dedup literature the design doc's
# research cites) -- expected Jaccard estimation error is O(1/sqrt(128)) ~ 8.8%, tight
# enough to separate true duplicates (which sample-match near 95-100%) from coincidentally
# similar tables (near 0%) without needing a much larger, more expensive signature.
_NUM_PERMUTATIONS = 128
# Mersenne prime just above 2^61 -- large enough that (a*x + b) mod _MERSENNE_PRIME has
# negligible collision probability for any realistic row-hash input, standard choice for
# universal hashing MinHash implementations.
_MERSENNE_PRIME = (1 << 61) - 1
# Sentinel "no value seen yet" for a fresh signature slot -- MUST be >= every possible
# permuted hash value ((a*x + b) mod _MERSENNE_PRIME, i.e. in [0, _MERSENNE_PRIME)), or
# the sentinel is never actually replaced by any real value and every signature ends up
# identical (all-sentinel) regardless of input. Caught live by
# tests/dedup/test_minhash.py::test_disjoint_row_sets_score_near_zero when this was
# originally 2**32-1 -- far smaller than _MERSENNE_PRIME's ~2**61 range, so `permuted <
# signature[i]` was always False and jaccard_similarity always returned 1.0.
_MAX_HASH = _MERSENNE_PRIME


def _stable_hash(value: bytes) -> int:
    """Deterministic 32-bit hash, stable across processes (unlike Python's built-in
    hash() for str/bytes, which is salted per-process by default for security --
    MinHash signatures must be comparable across pipeline runs and across the API/worker
    process boundary, so a salted hash would silently make every signature incomparable)."""
    digest = hashlib.blake2b(value, digest_size=4).digest()
    return int.from_bytes(digest, "big")


def _permutation_seeds(num_permutations: int) -> list[tuple[int, int]]:
    """Deterministic (a, b) coefficient pairs for num_permutations hash functions of the
    form h(x) = (a*x + b) mod _MERSENNE_PRIME. Derived from a fixed seed, not random.random(),
    so two processes computing signatures for the same row set always produce identical
    signatures -- required for signatures computed at different times (e.g. table A scanned
    today, table B scanned last week) to remain comparable.
    """
    seeds: list[tuple[int, int]] = []
    for i in range(num_permutations):
        # blake2b with a distinct per-index salt-like suffix, not a PRNG -- avoids taking a
        # dependency on Python's random module's implementation staying stable across
        # versions, which is not a documented guarantee.
        a = _stable_hash(f"minhash-a-{i}".encode()) % _MERSENNE_PRIME or 1
        b = _stable_hash(f"minhash-b-{i}".encode()) % _MERSENNE_PRIME
        seeds.append((a, b))
    return seeds


_PERMUTATIONS = _permutation_seeds(_NUM_PERMUTATIONS)


@dataclass(frozen=True, slots=True)
class MinHashSignature:
    """A table's MinHash signature: NUM_PERMUTATIONS minimum hash values, one per hash
    function, computed over the table's row-hash set (or a bounded sample of it -- see
    dedup.sampling for how row samples are drawn from Iceberg manifests without a full
    table scan)."""

    values: tuple[int, ...]
    sampled_row_count: int

    def jaccard_similarity(self, other: MinHashSignature) -> float:
        """Estimate of |A∩B| / |A∪B| between the two signatures' underlying row sets.

        This is an ESTIMATE, not an exact value -- accuracy depends on NUM_PERMUTATIONS
        and on how representative each table's sampled rows were of its full row set (see
        design doc SS4.4: error scales O(1/sqrt(sample_size)) separately from the
        O(1/sqrt(k)) MinHash estimation error itself). Treat this as Stage 2 evidence that
        justifies promoting a pair to Stage 3 exact verification, never as the sole basis
        for a drop recommendation.
        """
        if len(self.values) != len(other.values):
            raise ValueError(
                f"signature length mismatch: {len(self.values)} vs {len(other.values)} "
                "-- both signatures must use the same NUM_PERMUTATIONS"
            )
        if not self.values:
            return 0.0
        matches = sum(1 for a, b in zip(self.values, other.values, strict=True) if a == b)
        return matches / len(self.values)


def compute_minhash_signature(row_hashes: list[int]) -> MinHashSignature:
    """Compute a MinHash signature over a set of row hashes.

    ``row_hashes`` should already be deduplicated (a set, conceptually) -- pass
    ``list(set(row_hashes))`` or equivalent if the caller's row sample can contain repeats.
    Each row hash is expected to be a stable 64-bit-or-smaller integer (e.g. from
    ``hash_row`` in dedup.sampling), not Python's salted ``hash()``.
    """
    if not row_hashes:
        return MinHashSignature(values=tuple([_MAX_HASH] * _NUM_PERMUTATIONS), sampled_row_count=0)

    signature = [_MAX_HASH] * _NUM_PERMUTATIONS
    for row_hash in row_hashes:
        for i, (a, b) in enumerate(_PERMUTATIONS):
            permuted = (a * row_hash + b) % _MERSENNE_PRIME
            if permuted < signature[i]:
                signature[i] = permuted
    return MinHashSignature(values=tuple(signature), sampled_row_count=len(row_hashes))


@dataclass(frozen=True, slots=True)
class LSHBand:
    """One LSH band's hash of a signature slice -- two tables sharing a band hash are an
    LSH-candidate pair (see lsh_band_keys' docstring for the collision-probability math)."""

    band_index: int
    band_hash: int


def lsh_band_keys(signature: MinHashSignature, *, num_bands: int = 32) -> list[LSHBand]:
    """Bucket a MinHash signature into ``num_bands`` bands for LSH candidate lookup.

    Splits the signature into num_bands consecutive slices (rows-per-band =
    NUM_PERMUTATIONS / num_bands = 4 with the defaults) and hashes each slice. Two tables
    are LSH-candidates if ANY band hash matches -- this is what turns O(n^2) pairwise
    comparison into near-linear bucketed lookup (design doc SS4.1): instead of comparing
    every table to every other table, only compare tables that collide in at least one
    band.

    With b=32 bands and r=4 rows/band, the probability two signatures with true Jaccard
    similarity s collide in at least one band is 1 - (1 - s^r)^b -- this gives roughly an
    S-curve threshold around s ~ 0.5-0.6 (steep transition: pairs well above threshold
    collide with high probability, pairs well below rarely do). Cryo's Stage 1 blocking
    already narrows candidates via schema+size before LSH ever runs, so LSH here is a
    second-pass narrowing within an already-small candidate set, not the primary blocking
    mechanism against the full ~13k-table fleet.
    """
    if _NUM_PERMUTATIONS % num_bands != 0:
        raise ValueError(
            f"num_bands ({num_bands}) must evenly divide NUM_PERMUTATIONS ({_NUM_PERMUTATIONS})"
        )
    rows_per_band = _NUM_PERMUTATIONS // num_bands
    bands: list[LSHBand] = []
    for band_index in range(num_bands):
        start = band_index * rows_per_band
        band_slice = signature.values[start : start + rows_per_band]
        band_bytes = b"".join(v.to_bytes(8, "big", signed=False) for v in band_slice)
        band_hash = _stable_hash(band_bytes)
        bands.append(LSHBand(band_index=band_index, band_hash=band_hash))
    return bands
