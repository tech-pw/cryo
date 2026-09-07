"""Space-filling curve keys over rank-normalised coordinates.

Phase 2 simulation needs to answer "if these rows were sorted by a Z-order/Hilbert key, which
rows would land in the same file?" That requires actually computing the key, so this module
implements both curves over integer coordinates.

Two independent decisions are represented here, and the research note is emphatic that the
second matters more than the first (§4, §8):

**The curve.** Z-order interleaves coordinate bits -- cheap, but the curve makes long jumps
between quadrants, so cells that are neighbours in space can land far apart in curve order.
Hilbert rotates and reflects each quadrant so consecutive indices are always adjacent cells.
Published analysis (Moon et al., TKDE 2001) reports Hilbert needing ~24-47% fewer *contiguous
curve runs* for uniform box queries. Curve runs are a locality proxy, not files: the only
public lakehouse file-pruning numbers are ~31% (uniform synthetic) and ~2% (real skewed data).

**The coordinates.** Iceberg encodes raw ordered bytes, so a skewed column wastes most of its
coordinate range -- the high-order bits are near-constant and the curve cannot separate the
dense region. Delta instead maps each value to an approximate rank/range id first, which makes
every marginal roughly uniform. ``rank_normalise`` implements that transform. It is per-column
and marginal by design: it fixes skew, not correlation, which is exactly why the simulator must
sample *joint* rows rather than combining per-column sketches.

Correctness note: both curves here require coordinates in ``[0, 2**bits)``. Callers get that
from ``rank_normalise``; raw values must be bucketed first.
"""

from __future__ import annotations

import bisect
import logging
from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Bits per dimension for simulated curve keys. 16 gives 65,536 buckets per column -- far finer
# than any realistic file count, so the key's resolution never limits simulated pruning, while
# keeping the interleaved integer small enough to sort cheaply.
DEFAULT_CURVE_BITS = 16

# Reserved coordinate for NULL. Iceberg's raw-byte encoding lets NULL consume a real bucket,
# which wastes curve range on null-heavy columns; pinning it at 0 keeps nulls together and out
# of the value range (see research note §4.1's note on null-heavy dimensions).
NULL_COORDINATE = 0


def rank_normalise(
    value: object,
    boundaries: Sequence[object],
    *,
    bits: int = DEFAULT_CURVE_BITS,
) -> int:
    """Map a value to a roughly-uniform integer coordinate via quantile boundaries.

    ``boundaries`` are ascending quantile cut points from a sketch or deterministic sample.
    The returned coordinate is the bucket index rescaled to the full ``2**bits`` range, so a
    heavily skewed column occupies its coordinate space as evenly as a uniform one -- the
    property both curves need and raw-byte encoding does not provide.

    NULL maps to a reserved coordinate rather than competing for a value bucket.
    """
    if value is None:
        return NULL_COORDINATE

    max_coord = (1 << bits) - 1
    if not boundaries:
        # No distribution information: every row collapses to one coordinate, which correctly
        # simulates "this dimension cannot separate anything".
        return max_coord // 2

    try:
        index = bisect.bisect_right(list(boundaries), value)  # type: ignore[call-overload]
    except TypeError:
        # Mixed/unorderable types: treat as unknown rather than crashing the simulation.
        return max_coord // 2

    # Rescale bucket index (0..len(boundaries)) onto 1..max_coord, leaving 0 for NULL.
    span = len(boundaries)
    scaled = 1 + int(index * (max_coord - 1) / span) if span else max_coord // 2
    return max(1, min(max_coord, scaled))


def zorder_key(coordinates: Sequence[int], *, bits: int = DEFAULT_CURVE_BITS) -> int:
    """Interleave coordinate bits, most-significant bit first (Morton order)."""
    key = 0
    for bit in reversed(range(bits)):
        for coord in coordinates:
            key = (key << 1) | ((coord >> bit) & 1)
    return key


def hilbert_key(coordinates: Sequence[int], *, bits: int = DEFAULT_CURVE_BITS) -> int:
    """Hilbert curve index for a point, via the standard Gray-code transform.

    Uses the Skilling / Hamilton algorithm: convert the coordinate axes into a Hilbert
    transpose by walking bit planes from the most significant down, applying the per-plane
    rotation and reflection that keeps consecutive indices spatially adjacent, then interleave
    the transposed axes. This is the same construction Iceberg 1.12.0 uses.
    """
    n = len(coordinates)
    if n == 0:
        return 0
    axes = list(coordinates)

    # --- Inverse undo of the Hilbert transpose (Skilling's "AxestoTranspose"). ---
    m = 1 << (bits - 1)
    # Invert/exchange low bits and standard Gray code.
    q = m
    while q > 1:
        p = q - 1
        for i in range(n):
            if axes[i] & q:
                axes[0] ^= p  # invert
            else:
                t = (axes[0] ^ axes[i]) & p
                axes[0] ^= t
                axes[i] ^= t
        q >>= 1

    # Gray encode.
    for i in range(1, n):
        axes[i] ^= axes[i - 1]
    t = 0
    q = m
    while q > 1:
        if axes[n - 1] & q:
            t ^= q - 1
        q >>= 1
    for i in range(n):
        axes[i] ^= t

    # --- Interleave the transposed axes into a single index. ---
    key = 0
    for bit in reversed(range(bits)):
        for i in range(n):
            key = (key << 1) | ((axes[i] >> bit) & 1)
    return key


def curve_key(
    coordinates: Sequence[int],
    curve: str,
    *,
    bits: int = DEFAULT_CURVE_BITS,
) -> int:
    """Dispatch to the requested curve. Unknown curves fall back to Z-order."""
    if curve == "hilbert":
        return hilbert_key(coordinates, bits=bits)
    return zorder_key(coordinates, bits=bits)


def quantile_boundaries(values: Sequence[object], buckets: int) -> list[object]:
    """Derive ascending quantile cut points from a sample.

    Deterministic given the same sample: the simulation must be reproducible, and a persisted
    boundary set is what makes incremental rewrites stable (research note §4.3 -- recomputing
    boundaries every rewrite moves unchanged rows and causes write amplification).
    """
    usable = [v for v in values if v is not None]
    if not usable or buckets < 2:
        return []
    try:
        ordered = sorted(usable)  # type: ignore[type-var]
    except TypeError:
        return []  # unorderable column: no useful boundaries

    cuts: list[object] = []
    for i in range(1, buckets):
        idx = int(len(ordered) * i / buckets)
        idx = min(idx, len(ordered) - 1)
        candidate = ordered[idx]
        if not cuts or candidate != cuts[-1]:  # collapse ties from heavy hitters
            cuts.append(candidate)
    return cuts
