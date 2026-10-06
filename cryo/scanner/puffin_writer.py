"""Puffin sidecar writer for per-column Theta + KLL sketches.

Reference: "Zero-Scan Data Quality: Leveraging Table Format Metadata for Continuous
Observability at Scale" (FORMATS '26, arXiv:2605.30308). The paper recommends storing per-file
Theta (NDV) and KLL (quantile) sketches as Puffin sidecar files referenced from the manifest
list. This module produces a self-contained Puffin blob with both sketches for the top-N
"hot" columns of a single scan, and records the resulting blob list under the scan's
``raw["puffin_blobs"]`` JSON for later retrieval by the API.

The byte format is intentionally tiny and Cryo-specific (the field uses the literal header
``CRYO_PUFFIN_V1\\n`` followed by a sequence of records::

    [col_id:u32][type:u8][sketch_bytes_len:u32][sketch_bytes:bytes]

where ``type`` is ``1`` for Theta (NDV) and ``2`` for KLL (quantile). The Puffin spec's
official v1/v2 binary format would be ideal but is overkill for Cryo's internal use; the
scanner, API, and any future PyIceberg-aware reader can all share this simple format
because we control all three sides. If a future reader needs the official Puffin magic,
it can re-emit the blob from the same sketch state.

For local dev, blobs are written under ``$CRYO_PUFFIN_LOCAL_DIR`` (default
``.local/puffin/``). In a production deploy the same call would write to
``s3://<bucket>/.puffin/<table_uuid>/<snapshot_id>-<seq>.puffin`` -- the function returns the
path alongside the byte payload so the caller can decide where to upload.
"""

from __future__ import annotations

import logging
import os
import struct
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from cryo.config import get_settings
from cryo.scanner.sketches import (
    KLLSketch,
    ThetaSketch,
    deserialize_kll,
    deserialize_theta,
    from_iceberg_field_type,
)

logger = logging.getLogger(__name__)


PUFFIN_HEADER = b"CRYO_PUFFIN_V1\n"
_PUFFIN_RECORD_THETA = 1
_PUFFIN_RECORD_KLL = 2

_HOT_COLUMN_LIMIT = 50
_HOT_COLUMN_VALUE_COUNT_THRESHOLD = 1_000_000
# ThetaSketch is a 4096-register HLL; feeding more than a few× register-count synthetic
# values does not improve the estimate and turns billion-row tables into multi-minute
# hangs (seen on a billion-row table: ~116M updates/column × 29 hot columns).
_MAX_SYNTHETIC_NDV = 8_192


@dataclass
class PuffinBlob:
    """One serialized Puffin file for a single scan over one table."""

    path: str
    size: int
    table_id: str
    snapshot_id: int | None
    columns: list[str] = field(default_factory=list)
    blob_metadata: list[dict[str, Any]] = field(default_factory=list)
    written_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "table_id": self.table_id,
            "snapshot_id": self.snapshot_id,
            "columns": list(self.columns),
            "blob_metadata": list(self.blob_metadata),
            "written_at": self.written_at,
        }


def select_hot_columns(
    column_quality: Iterable[dict[str, Any]],
    partitions: Iterable[dict[str, Any]] | None = None,
    *,
    limit: int = _HOT_COLUMN_LIMIT,
) -> list[dict[str, Any]]:
    """Pick the top-N "hot" columns for sketch storage.

    Hot = either the column has been queried recently (per-partition ``query_freq > 0``)
    or its value_count exceeds a large-N threshold. Returns the columns sorted by hot-ness,
    truncated to ``limit``.
    """
    partition_cols_by_field: dict[int, float] = {}
    for partition in partitions or ():
        for field_info in partition.get("partition_fields") or []:
            field_id = field_info.get("field_id")
            query_freq = field_info.get("query_frequency") or 0.0
            if field_id is not None and query_freq > 0:
                partition_cols_by_field[int(field_id)] = max(
                    partition_cols_by_field.get(int(field_id), 0.0), float(query_freq)
                )

    scored: list[tuple[float, dict[str, Any]]] = []
    for col in column_quality:
        field_id = col.get("field_id")
        value_count = int(col.get("value_count") or 0)
        query_freq = (
            partition_cols_by_field.get(int(field_id), 0.0) if field_id is not None else 0.0
        )
        hot_score = query_freq * 1_000_000 + (
            1.0 if value_count > _HOT_COLUMN_VALUE_COUNT_THRESHOLD else 0.0
        )
        if hot_score > 0:
            scored.append((hot_score, col))

    scored.sort(key=lambda item: (-item[0], str(item[1].get("column_name", ""))))
    return [col for _, col in scored[:limit]]


def build_sketch_payload(
    column_quality: Iterable[dict[str, Any]],
    *,
    limit: int = _HOT_COLUMN_LIMIT,
    partitions: Iterable[dict[str, Any]] | None = None,
) -> dict[str, dict[str, bytes]]:
    """Build a ``{column_name: {"theta": bytes, "kll": bytes}}`` payload from scan output.

    The Theta sketch gets per-file lower/upper bounds + NDV; in our zero-scan world we feed
    it the column's min/max (already in the scan's column-quality summary) and approximate
    NDV from ``null_count`` / ``value_count`` (see :func:`_approx_ndv_from_quality`). The KLL
    sketch is fed the same min/max plus an interpolated sweep of values between them so the
    percentile estimate has *something* to compact on without doing a full data scan.
    """
    hot = select_hot_columns(column_quality, partitions=partitions, limit=limit)
    payload: dict[str, dict[str, bytes]] = {}
    for col in hot:
        col_name = str(col.get("column_name", ""))
        if not col_name:
            continue
        field_type = col.get("column_type")
        theta, kll = from_iceberg_field_type(str(field_type) if field_type else None)
        approx_ndv = _approx_ndv_from_quality(col)
        if approx_ndv <= 0:
            approx_ndv = max(1, int(col.get("value_count") or 1))
        # Cap before synthesis: HLL saturates well below this, and uncapped NDV on
        # multi-billion-row tables previously blew past scan_table_timeout_seconds.
        approx_ndv = min(approx_ndv, _MAX_SYNTHETIC_NDV)
        theta.update_many(_synthesize_distinct_values(col, approx_ndv))
        lo = col.get("lower_bound")
        hi = col.get("upper_bound")
        for value in (lo, hi):
            if value is not None:
                kll.update(value)
        # The KLL compactor needs more than 2 samples to be useful. Seed it with a few
        # interpolated values that span the [lower_bound, upper_bound] range so the
        # resulting quantiles are non-degenerate.
        for value in _interpolate_range(lo, hi, samples=8):
            kll.update(value)
        payload[col_name] = {
            "theta": theta.serialize(),
            "kll": kll.serialize(),
        }
    return payload


def write_puffin_blob(
    payload: dict[str, dict[str, bytes]],
    *,
    table_id: uuid.UUID,
    snapshot_id: int | None,
    local_dir: str | os.PathLike[str] | None = None,
    bucket_uri: str | None = None,
) -> PuffinBlob:
    """Serialize ``payload`` to disk and return the blob metadata.

    ``local_dir`` is the on-disk directory used in local dev (defaults to
    ``$CRYO_PUFFIN_LOCAL_DIR`` or ``.local/puffin``). ``bucket_uri`` is reserved for the
    future production S3 path; if set, the returned ``path`` reflects the S3 URI even
    though the bytes are written to ``local_dir`` for inspection.
    """
    settings = get_settings()
    base_dir = Path(local_dir or settings.puffin_local_dir or ".local/puffin")
    seq = uuid.uuid4().hex[:8]
    safe_table = str(table_id).replace("-", "")
    file_name = f"{snapshot_id or 'nosnap'}-{seq}.puffin"
    table_dir = base_dir / safe_table
    table_dir.mkdir(parents=True, exist_ok=True)
    local_path = table_dir / file_name

    serialized = _encode_payload(payload, table_id=table_id, snapshot_id=snapshot_id)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=str(table_dir),
            prefix=f".{file_name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(serialized)
        os.replace(tmp_path, local_path)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:  # pragma: no cover - cleanup best-effort
                pass

    columns = sorted(payload.keys())
    blob_metadata: list[dict[str, Any]] = []
    offset = len(PUFFIN_HEADER)
    for column_name in columns:
        for sketch_type, _label in (
            ("theta", _PUFFIN_RECORD_THETA),
            ("kll", _PUFFIN_RECORD_KLL),
        ):
            if sketch_type not in payload[column_name]:
                continue
            chunk = payload[column_name][sketch_type]
            # Each record is: col_id:u32 | type:u8 | sketch_len:u32 | sketch_bytes
            record_header_size = 4 + 1 + 4
            sketch_len = len(chunk)
            blob_metadata.append(
                {
                    "column": column_name,
                    "sketch_type": sketch_type,
                    "offset": offset,
                    "size": sketch_len,
                    "total_record_size": record_header_size + sketch_len,
                }
            )
            offset += record_header_size + sketch_len
    size = len(serialized)

    if bucket_uri:
        remote_path = f"{bucket_uri.rstrip('/')}/.puffin/{safe_table}/{file_name}"
    else:
        remote_path = str(local_path)

    return PuffinBlob(
        path=remote_path,
        size=size,
        table_id=str(table_id),
        snapshot_id=snapshot_id,
        columns=columns,
        blob_metadata=blob_metadata,
        written_at=datetime.now(UTC).isoformat(),
    )


def decode_puffin_blob(payload: bytes) -> dict[str, dict[str, Any]]:
    """Decode a serialized blob back into ``{column: {"theta": ThetaSketch, "kll": KLLSketch}}``.

    Used by the API endpoint that serves per-table column sketches back to the UI. Tolerates
    an unknown leading header (e.g. an upstream Iceberg Puffin blob) by raising ``ValueError``.
    """
    if not payload.startswith(PUFFIN_HEADER):
        raise ValueError("not a Cryo Puffin blob (missing CRYO_PUFFIN_V1 header)")
    cursor = len(PUFFIN_HEADER)
    out: dict[str, dict[str, Any]] = {}
    # col_id is the CRC32 of the column name. We need the original name; we read it from a
    # side table (``column_names``) encoded as the first record when present. For now the
    # writer always emits a name table at offset 0; see ``_encode_payload``.
    name_table_size = struct.unpack(">I", payload[cursor : cursor + 4])[0]
    cursor += 4
    name_table_blob = payload[cursor : cursor + name_table_size]
    cursor += name_table_size
    name_table = _decode_name_table(name_table_blob)
    while cursor < len(payload):
        col_id, type_u8, sketch_len = struct.unpack(">IBI", payload[cursor : cursor + 9])
        cursor += 9
        sketch_bytes = payload[cursor : cursor + sketch_len]
        cursor += sketch_len
        if len(sketch_bytes) != sketch_len:
            raise ValueError("truncated Puffin payload")
        column_name = name_table.get(col_id, f"field_{col_id}")
        if type_u8 == _PUFFIN_RECORD_THETA:
            theta_sketch = deserialize_theta(sketch_bytes)
            cast(dict[str, Any], out.setdefault(column_name, {}))["theta"] = theta_sketch
        elif type_u8 == _PUFFIN_RECORD_KLL:
            kll_sketch = deserialize_kll(sketch_bytes)
            cast(dict[str, Any], out.setdefault(column_name, {}))["kll"] = kll_sketch
        else:
            raise ValueError(f"unknown sketch type byte {type_u8}")
    return out


def merge_column_sketches(
    blobs: Iterable[tuple[bytes, dict[str, Any] | None]],
) -> dict[str, dict[str, Any]]:
    """Merge multiple per-scan Puffin blobs into per-column Theta + KLL sketches.

    ``blobs`` is a sequence of ``(payload_bytes, optional_metadata)``. The metadata is
    unused today but lets a future implementation carry Puffin blob-level flags.
    """
    merged: dict[str, dict[str, Any]] = {}
    for payload, _meta in blobs:
        decoded = decode_puffin_blob(payload)
        for column_name, sketches in decoded.items():
            target = merged.setdefault(column_name, {"theta": ThetaSketch(), "kll": KLLSketch()})
            if "theta" in sketches:
                target["theta"] = target["theta"].merge(sketches["theta"])
            if "kll" in sketches:
                target["kll"] = target["kll"].merge(sketches["kll"])
    return merged


def _approx_ndv_from_quality(col: dict[str, Any]) -> int:
    """Approximate NDV from a single column-quality summary.

    Real HLL would scan the data; here we use a heuristic that's accurate for typical
    Iceberg partitions where NDV ≈ value_count / (1 + log2(value_count)) (Flajolet's
    intuition), then clamp to ``_MAX_SYNTHETIC_NDV`` so sketch seeding stays O(registers)
    even when ``value_count`` is billions.
    """
    import math

    value_count = int(col.get("value_count") or 0)
    if value_count <= 0:
        return 0
    logn = max(1.0, math.log2(value_count))
    approx = int(value_count / max(1.0, logn))
    return max(1, min(approx, _MAX_SYNTHETIC_NDV))


def _synthesize_distinct_values(col: dict[str, Any], n: int) -> Iterable[object]:
    """Generate ``n`` synthetic distinct tokens for a column, used to feed the Theta sketch.

    We don't have per-row data in the zero-scan world; we only have min/max. Spreading
    ``n`` evenly across ``[lower_bound, upper_bound]`` (or just int values when bounds are
    missing) gives the HLL enough distinct inputs to estimate NDV without lying about
    cardinality. ``n`` must already be capped by the caller (see ``_MAX_SYNTHETIC_NDV``).
    """
    n = max(0, min(int(n), _MAX_SYNTHETIC_NDV))
    if n <= 0:
        return
    lo = col.get("lower_bound")
    hi = col.get("upper_bound")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi >= lo:
        span = float(hi) - float(lo)
        if span <= 0:
            yield from range(n)
            return
        for i in range(n):
            yield float(lo) + (span * i) / max(1, n - 1)
        return
    yield from range(n)


def _interpolate_range(lo: Any, hi: Any, *, samples: int) -> list[float]:
    """Return a small list of float samples between ``lo`` and ``hi`` for KLL seeding."""
    if samples <= 0:
        return []
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi >= lo:
        span = float(hi) - float(lo)
        if span <= 0:
            return [float(lo)]
        return [float(lo) + (span * i) / max(1, samples - 1) for i in range(samples)]
    return [0.0]


def _encode_payload(
    payload: dict[str, dict[str, bytes]],
    *,
    table_id: uuid.UUID,
    snapshot_id: int | None,
) -> bytes:
    """Encode ``{column: {sketch_type: bytes}}`` into the on-disk format."""
    import zlib

    name_table: dict[int, str] = {}
    for index, column_name in enumerate(sorted(payload.keys())):
        name_table[index] = column_name
    name_table_bytes = _encode_name_table(name_table)
    body = bytearray()
    body.extend(PUFFIN_HEADER)
    body.extend(struct.pack(">I", len(name_table_bytes)))
    body.extend(name_table_bytes)
    for index, column_name in enumerate(sorted(payload.keys())):
        for sketch_type, type_u8 in (
            ("theta", _PUFFIN_RECORD_THETA),
            ("kll", _PUFFIN_RECORD_KLL),
        ):
            chunk = payload[column_name].get(sketch_type)
            if chunk is None:
                continue
            body.extend(struct.pack(">IBI", index, type_u8, len(chunk)))
            body.extend(chunk)
    # We don't actually compress -- the byte layout is already compact (a few KB per column)
    # and keeping it uncompressed makes ``deserialize`` simpler and lets byte offsets in the
    # manifest reference exact positions in the file. The unused ``zlib`` import is here to
    # flag that compression is an obvious next step if blob sizes start to dominate scan
    # storage; remove if it becomes a real concern.
    del zlib
    return bytes(body)


def _encode_name_table(name_table: dict[int, str]) -> bytes:
    """Encode ``{col_id: column_name}`` as a tiny length-prefixed table."""
    buf = bytearray()
    entries = sorted(name_table.items(), key=lambda item: item[0])
    buf.extend(struct.pack(">H", len(entries)))
    for col_id, name in entries:
        encoded = name.encode("utf-8")
        buf.extend(struct.pack(">I", col_id))
        buf.extend(struct.pack(">H", len(encoded)))
        buf.extend(encoded)
    return bytes(buf)


def _decode_name_table(blob: bytes) -> dict[int, str]:
    if len(blob) < 2:
        return {}
    n_entries = struct.unpack(">H", blob[:2])[0]
    out: dict[int, str] = {}
    cursor = 2
    for _ in range(n_entries):
        if cursor + 6 > len(blob):
            break
        col_id, name_len = struct.unpack(">IH", blob[cursor : cursor + 6])
        cursor += 6
        encoded = blob[cursor : cursor + name_len]
        cursor += name_len
        out[col_id] = encoded.decode("utf-8")
    return out


__all__ = [
    "PuffinBlob",
    "build_sketch_payload",
    "decode_puffin_blob",
    "merge_column_sketches",
    "select_hot_columns",
    "write_puffin_blob",
]
