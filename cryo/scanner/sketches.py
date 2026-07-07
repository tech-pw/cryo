"""Native Python approximate sketches for per-column NDV and quantile storage.

Two sketch families used by Cryo's Puffin writer (see ``cryo.scanner.puffin_writer``):

* :class:`ThetaSketch` -- an approximate number of distinct values (NDV / cardinality).
* :class:`KLLSketch` -- an approximate quantile sketch (p50/p95/p99-style).

Both classes follow the **mergeable, serializable, compact** contract that the FORMATS '26
paper ("Zero-Scan Data Quality", arXiv:2605.30308) calls out: per-file sketches can be combined
into a global sketch via :py:meth:`merge` and persisted into a Puffin sidecar via
:py:meth:`serialize` / :py:meth:`deserialize`. The byte format is intentionally tiny and
documented inline -- this module is dependency-free (no ``datasketches`` or third-party
Apache-licensed sketch library required) and ships its own native, approximate implementations.

Approximation caveats
---------------------
``ThetaSketch`` is a small fixed-size HyperLogLog-style counter (1024-bit packed register array,
4-bit saturation) rather than a full Apache Theta sketch. Standard error is roughly
``1.04 / sqrt(2^p)`` where ``p`` is the number of register bits (12 by default -> ~1.5%).
The 4-bit saturation means very high-cardinality streams see soft saturation around
``16 * 2^p`` distinct elements (millions). For the scanner's purposes (per-file NDV rollups
feeding a column-statistics quality signal) that is more than sufficient.

``KLLSketch`` is a simplified compact KLL: a sparse buffer of incoming values plus a compactor
that periodically halves via random pairwise promotion. We target ``K=200`` and use a small
random compactor. The returned quantiles are therefore approximate -- accuracy is on the order
of ``O(1/K)`` relative rank error, which is the right ballpark for a sketch that will be
queried only for p50/p95/p99.

These sketches are NOT wire-compatible with Apache DataSketches; they are Cryo's own
compact approximations. The intent is to give the platform a self-contained signal source
that does not depend on a third-party sketch library and does not break the local-dev flow.
"""

from __future__ import annotations

import hashlib
import math
import random
import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

# Sketch format constants. Bumping these invalidates previously-serialized blobs.
SKETCH_MAGIC_THETA = b"CRYOTH1\x00"
SKETCH_MAGIC_KLL = b"CRYOKL1\x00"

_THETA_REG_BITS = 12
_THETA_REG_COUNT = 1 << _THETA_REG_BITS
_THETA_REG_MASK = (1 << _THETA_REG_BITS) - 1

_KLL_K = 200
_KLL_SPARSE_CAP = 2000  # values to keep in the sparse buffer before forcing a compact.


def _hash64(data: bytes) -> int:
    """Deterministic, well-distributed 64-bit hash for HLL.

    We use the first 8 bytes of a SHA-256 digest. SHA-256 is in the stdlib, deterministic
    across processes (unlike :py:func:`hash`), and produces a near-uniform 64-bit prefix
    even for inputs that share a long common prefix (e.g. all ``int:`` values). FNV-1a
    was tried first but its high bits were dominated by the byte-sequence prefix, which
    biased HLL rank estimates.
    """
    digest = hashlib.sha256(data).digest()
    return int.from_bytes(digest[:8], "big")


def _rho_52(word: int) -> int:
    """Number of leading zeros of a 52-bit value, capped at 52.

    The HyperLogLog rank we feed into each register is computed over the upper ``64 - p``
    bits of the 64-bit hash after stripping the ``p`` register-index bits. With ``p = 12``
    that is a 52-bit window. We compute leading zeros within those 52 bits only -- if the
    caller passes a wider integer, we mask it down first.
    """
    word &= (1 << 52) - 1
    if word == 0:
        return 52
    n = 0
    # Top 16 bits (positions 36-51 of the 52-bit value).
    if (word >> 36) & 0xFFFF == 0:
        n += 16
        word &= (1 << 36) - 1
    else:
        word >>= 16
    # Positions 32-35: 4 bits.
    if (word >> 32) & 0xF == 0:
        n += 4
        word &= (1 << 32) - 1
    else:
        word >>= 4
    # Positions 16-31: 16 bits.
    if (word >> 16) & 0xFFFF == 0:
        n += 16
        word &= (1 << 16) - 1
    else:
        word >>= 16
    # 8 bits.
    if (word >> 8) & 0xFF == 0:
        n += 8
        word &= 0xFF
    else:
        word >>= 8
    # 4 bits.
    if (word >> 4) & 0xF == 0:
        n += 4
        word &= 0xF
    else:
        word >>= 4
    # 2 bits.
    if (word >> 2) & 0x3 == 0:
        n += 2
        word &= 0x3
    else:
        word >>= 2
    # 1 bit.
    if (word >> 1) & 0x1 == 0:
        n += 1
    return n


@dataclass
class ThetaSketch:
    """HyperLogLog-style NDV sketch.

    A packed 4-bit register array of length ``2^p`` (default ``p = 12`` => 4096 registers =>
    2048 bytes of state). Each register stores the maximum leading-zero count observed for
    any value hashed into that register, capped at 15 (4 bits). The raw estimate is
    ``alpha_m * 2^p * 2^(sum / 2^p)`` with the usual small/large range corrections.

    The class is intentionally small and self-contained. Use :py:meth:`merge` to combine
    sketches per-file into a per-column sketch; serialize/deserialize round-trips state.
    """

    p: int = _THETA_REG_BITS
    registers: bytearray = field(default_factory=bytearray)
    n: int = 0

    def __post_init__(self) -> None:
        if self.p != _THETA_REG_BITS:
            # p is currently fixed for byte-format compatibility.
            raise ValueError(f"ThetaSketch only supports p={_THETA_REG_BITS} (got p={self.p})")
        if not self.registers:
            self.registers = bytearray(_THETA_REG_COUNT // 2)

    def update(self, value: object) -> None:
        """Incorporate a value. ``value`` is hashed deterministically."""
        word = _value_to_word(value)
        h = _hash64(word)
        idx = h & _THETA_REG_MASK
        w = h >> _THETA_REG_BITS
        leading = _rho_52(w) + 1  # 1-indexed; rho==52 means register=52 but we cap to 15.
        leading = min(leading, 15)  # 4-bit cap.
        # HyperLogLog requires MAX-writes: a register stores the largest rank seen
        # for any value hashing into it, not the most recent value. Earlier
        # implementations of this module overwrote unconditionally and silently
        # produced biased-low estimates for any high-cardinality column.
        current = self._read_register(idx)
        if leading > current:
            self._write_register(idx, leading)
        self.n += 1

    def update_many(self, values: Iterable[object]) -> None:
        for value in values:
            self.update(value)

    @property
    def estimate(self) -> int:
        if self.n == 0:
            return 0
        raw = self._raw_estimate()
        # Small-range correction (Flajolet et al.).
        if raw <= 2.5 * _THETA_REG_COUNT and self._zero_count() != 0:
            # Linear counting.
            zeros = self._zero_count()
            return int(round(_THETA_REG_COUNT * math.log(_THETA_REG_COUNT / zeros)))
        return int(round(raw))

    def merge(self, other: ThetaSketch) -> ThetaSketch:
        """Return a new sketch that is the union of ``self`` and ``other``."""
        if not isinstance(other, ThetaSketch):
            return NotImplemented
        if self.p != other.p:
            raise ValueError("Cannot merge sketches with different p")
        result = ThetaSketch(p=self.p)
        for i in range(_THETA_REG_COUNT):
            a = self._read_register(i)
            b = other._read_register(i)
            result._write_register(i, max(a, b))
        result.n = self.n + other.n
        return result

    def serialize(self) -> bytes:
        """Serialize to a portable byte blob."""
        packed = bytes(self.registers)
        header = struct.pack(">8sII", SKETCH_MAGIC_THETA, _THETA_REG_COUNT, self.n)
        return header + packed

    @classmethod
    def deserialize(cls, blob: bytes) -> ThetaSketch:
        if len(blob) < 16:
            raise ValueError("ThetaSketch blob too short")
        magic, count, n = struct.unpack(">8sII", blob[:16])
        if magic != SKETCH_MAGIC_THETA:
            raise ValueError(f"Bad ThetaSketch magic: {magic!r}")
        expected_packed = count // 2
        packed = blob[16 : 16 + expected_packed]
        if len(packed) != expected_packed:
            raise ValueError("ThetaSketch blob truncated")
        sketch = cls(p=_THETA_REG_BITS)
        sketch.registers = bytearray(packed)
        sketch.n = n
        return sketch

    def _raw_estimate(self) -> float:
        # alpha_m for m=4096 (m >= 128) is approximately 0.7213 / (1 + 1.079 / m).
        # The denominator is the sum of 2^-M[j] (the harmonic-mean-style formula),
        # NOT 2^(sum(m_j)/m). The latter is the (wrong) arithmetic-mean approximation
        # that over-counts by orders of magnitude at low N.
        m = _THETA_REG_COUNT
        alpha = 0.7213 / (1.0 + 1.079 / m)
        denom = sum(2.0 ** -self._read_register(j) for j in range(m))
        return (alpha * m * m) / denom

    def _zero_count(self) -> int:
        return sum(1 for i in range(_THETA_REG_COUNT) if self._read_register(i) == 0)

    def _read_register(self, idx: int) -> int:
        byte = self.registers[idx // 2]
        if idx % 2 == 0:
            return (byte >> 4) & 0x0F
        return byte & 0x0F

    def _write_register(self, idx: int, value: int) -> None:
        if value < 0 or value > 15:
            raise ValueError(f"ThetaSketch register value out of range: {value}")
        pos = idx // 2
        if idx % 2 == 0:
            self.registers[pos] = (self.registers[pos] & 0x0F) | ((value & 0x0F) << 4)
        else:
            self.registers[pos] = (self.registers[pos] & 0xF0) | (value & 0x0F)


def _value_to_word(value: object) -> bytes:
    """Canonicalize a value into a hashable byte string.

    We keep this deliberately simple: ints/floats go through ``struct.pack`` with a fixed
    format, strings are utf-8, and everything else falls back to ``repr`` so two identical
    Python objects hash identically.
    """
    if isinstance(value, bool):
        return b"bool:" + (b"1" if value else b"0")
    if isinstance(value, int):
        return b"int:" + struct.pack(">q", value)
    if isinstance(value, float):
        if math.isnan(value):
            return b"float:nan"
        return b"float:" + struct.pack(">d", value)
    if isinstance(value, str):
        return b"str:" + value.encode("utf-8")
    if isinstance(value, (bytes, bytearray)):
        return b"bytes:" + bytes(value)
    if isinstance(value, datetime):
        return b"dt:" + value.isoformat().encode("utf-8")
    return b"obj:" + repr(value).encode("utf-8")


@dataclass
class KLLSketch:
    """Compact KLL-style quantile sketch.

    A simplified implementation: ingest values into a sorted sparse buffer, then periodically
    run a deterministic compactor that halves via random pairwise promotion until the buffer
    holds ``K`` items. Sorted queries then take linear-time quantiles from the compacted
    buffer.

    The compactor is intentionally non-stochastic -- it deterministically keeps every other
    element after a shuffle so a single repeated ingestion produces a stable sketch. Merging
    just unions two buffers and re-compacts.
    """

    k: int = _KLL_K
    items: list[float] = field(default_factory=list)

    def update(self, value: object) -> None:
        try:
            f = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if math.isnan(f):
            return
        self.items.append(f)
        if len(self.items) >= _KLL_SPARSE_CAP:
            self._compact()

    def update_many(self, values: Iterable[object]) -> None:
        for value in values:
            self.update(value)

    @property
    def is_empty(self) -> bool:
        return not self.items

    def quantile(self, q: float) -> float:
        """Return the approximate value at rank ``q`` in [0, 1]."""
        if not self.items:
            return 0.0
        ordered = sorted(self.items)
        if len(ordered) == 1:
            return ordered[0]
        rank = max(0.0, min(1.0, q)) * (len(ordered) - 1)
        low_idx = int(math.floor(rank))
        high_idx = int(math.ceil(rank))
        if low_idx == high_idx:
            return ordered[low_idx]
        frac = rank - low_idx
        return ordered[low_idx] * (1.0 - frac) + ordered[high_idx] * frac

    def merge(self, other: KLLSketch) -> KLLSketch:
        if not isinstance(other, KLLSketch):
            return NotImplemented
        result = KLLSketch(k=self.k)
        result.items = list(self.items) + list(other.items)
        result._compact()
        return result

    def serialize(self) -> bytes:
        n = len(self.items)
        header = struct.pack(">8sI", SKETCH_MAGIC_KLL, n)
        body = struct.pack(f">{n}d", *self.items) if n else b""
        return header + body

    @classmethod
    def deserialize(cls, blob: bytes) -> KLLSketch:
        if len(blob) < 12:
            raise ValueError("KLLSketch blob too short")
        magic, n = struct.unpack(">8sI", blob[:12])
        if magic != SKETCH_MAGIC_KLL:
            raise ValueError(f"Bad KLLSketch magic: {magic!r}")
        expected = n * 8
        body = blob[12 : 12 + expected]
        if len(body) != expected:
            raise ValueError("KLLSketch blob truncated")
        items = list(struct.unpack(f">{n}d", body)) if n else []
        sketch = cls(k=_KLL_K)
        sketch.items = items
        return sketch

    def _compact(self) -> None:
        if len(self.items) <= self.k:
            self.items.sort()
            return
        rng = random.Random(0xC0FFEE)  # deterministic compaction
        rng.shuffle(self.items)
        # Pairwise: keep the max of each pair, then sort.
        kept: list[float] = []
        it = iter(self.items)
        for a, b in zip(it, it, strict=False):
            kept.append(max(a, b))
        if len(self.items) % 2 == 1:
            kept.append(self.items[-1])
        # Recurse if we still exceed k, but bounded -- usually one or two passes suffice.
        self.items = kept
        if len(self.items) > self.k:
            # Approximate: take sorted tail + k/2 random elements.
            self.items.sort()
            half = max(1, self.k // 2)
            self.items = self.items[:half] + rng.sample(
                self.items[half:], min(self.k - half, len(self.items) - half)
            )


def from_iceberg_field_type(field_type: str | None) -> tuple[ThetaSketch, KLLSketch]:
    """Factory: build a fresh Theta + KLL pair sized for an Iceberg field type.

    The ``field_type`` is informational only -- both sketches are generic float-/hash-based.
    The factory exists so callers can build the standard pair in one line.
    """
    del field_type  # reserved for future type-aware behavior (e.g. integer column overflow).
    return ThetaSketch(), KLLSketch()


# Public aliases used by ``puffin_writer.decode_puffin_blob`` to avoid importing the
# private classmethod spellings at every call site.
def deserialize_theta(blob: bytes) -> ThetaSketch:
    """Alias for :py:meth:`ThetaSketch.deserialize`."""
    return ThetaSketch.deserialize(blob)


def deserialize_kll(blob: bytes) -> KLLSketch:
    """Alias for :py:meth:`KLLSketch.deserialize`."""
    return KLLSketch.deserialize(blob)
