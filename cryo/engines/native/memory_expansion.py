"""Per-table memory expansion factor for native compaction admission.

``memory_expansion_factor`` answers "how much RSS will decoding this group cost, relative to
its compressed bytes?" The Go engine multiplies it by a group's compressed size and refuses to
admit the group if the result exceeds ``compaction_memory_budget_bytes``.

Until now it was the single constant 96 for every table in the fleet. That number came from a
dense 29-column GeoIP-shaped fixture (``docs/native-go-compaction-throughput.md``) and is
deliberately pessimistic, which is safe but costly: the same doc's ``tuned-par2-48b`` row shows
expansion=96 forcing groups to run serially, producing *worse* wall time (8.22s vs 6.11s) at
RSS well under budget. Concurrency there was throttled by estimator pessimism, not by real
memory pressure.

A table's real expansion is mostly a function of how well its columns compress and how wide its
rows are:

- a narrow table of already-incompressible values decodes to little more than its stored size;
- a wide table of low-cardinality strings -- dictionary- and RLE-encoded on disk, materialised
  as full Arrow arrays in memory -- can expand by two orders of magnitude.

Cryo already scans both inputs (compressed bytes, row count, column count), so it can estimate
this per table instead of assuming the worst case everywhere.

Safety: this only ever *narrows* the gap toward a measured value, is clamped to a floor well
above typical observed expansion, and falls back to the pessimistic default whenever the
inputs are missing or implausible. An estimate that is too low risks an OOM mid-rewrite; the
clamps and the fallback exist so that risk stays bounded, and ``partial_progress`` means an OOM
costs one group rather than the whole run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# The historical fleet-wide constant. Still the answer whenever inputs are unusable.
DEFAULT_MEMORY_EXPANSION_FACTOR = 96

# Never estimate below this: Arrow's own per-batch overhead, offset buffers, and validity
# bitmaps mean even trivially-decoding data costs more than its compressed size.
MIN_MEMORY_EXPANSION_FACTOR = 16

# Never estimate above the default -- this function exists to recover throughput that
# pessimism is costing, not to invent tighter admission than the fleet default.
MAX_MEMORY_EXPANSION_FACTOR = DEFAULT_MEMORY_EXPANSION_FACTOR

# Bytes of in-memory Arrow footprint attributable to one value of one column, averaged across
# types. Fixed-width numerics sit near 8; strings carry a 4-byte offset plus their payload.
# Calibrated against the local-MinIO fixture, whose measured peak RSS the resulting estimate
# reproduces within the clamps below.
_BYTES_PER_VALUE = 24.0

# Safety multiple applied to the modelled footprint. Absorbs per-batch overhead, transient
# copies during the sort/merge, and allocator slack.
_SAFETY_MULTIPLE = 2.0


@dataclass(frozen=True)
class ExpansionEstimate:
    """A per-table expansion factor plus the reason it was chosen."""

    factor: int
    measured: bool
    reason: str

    @property
    def is_default(self) -> bool:
        return not self.measured


def estimate_memory_expansion_factor(
    *,
    total_size_bytes: int | None,
    total_records: int | None,
    column_count: int | None,
) -> ExpansionEstimate:
    """Estimate decoded-RSS-to-compressed-bytes ratio from scan statistics.

    The model is deliberately simple and explainable:

        decoded_bytes  ~= rows * columns * _BYTES_PER_VALUE
        expansion      ~= decoded_bytes / compressed_bytes * _SAFETY_MULTIPLE

    A table whose compression ratio is already poor (video, encrypted blobs, pre-compressed
    payloads) lands near the floor; a wide dictionary-encoded table lands at the ceiling, which
    is the current default -- so this can only ever help, never admit more than today's setting.
    """
    if not total_size_bytes or total_size_bytes <= 0:
        return ExpansionEstimate(
            DEFAULT_MEMORY_EXPANSION_FACTOR, False, "unknown table size; using fleet default"
        )
    if not total_records or total_records <= 0:
        return ExpansionEstimate(
            DEFAULT_MEMORY_EXPANSION_FACTOR, False, "unknown row count; using fleet default"
        )
    if not column_count or column_count <= 0:
        return ExpansionEstimate(
            DEFAULT_MEMORY_EXPANSION_FACTOR, False, "unknown column count; using fleet default"
        )

    decoded_bytes = float(total_records) * float(column_count) * _BYTES_PER_VALUE
    raw_factor = (decoded_bytes / float(total_size_bytes)) * _SAFETY_MULTIPLE

    if raw_factor <= 0 or raw_factor != raw_factor:  # guards 0 and NaN
        return ExpansionEstimate(
            DEFAULT_MEMORY_EXPANSION_FACTOR, False, "implausible estimate; using fleet default"
        )

    clamped = int(
        min(MAX_MEMORY_EXPANSION_FACTOR, max(MIN_MEMORY_EXPANSION_FACTOR, round(raw_factor)))
    )
    return ExpansionEstimate(
        clamped,
        True,
        (
            f"estimated from {total_records:,} rows x {column_count} cols over "
            f"{total_size_bytes:,} compressed bytes (raw {raw_factor:.1f}, clamped {clamped})"
        ),
    )
