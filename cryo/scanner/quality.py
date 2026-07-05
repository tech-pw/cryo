"""Zero-scan data quality signals derived from Iceberg manifest-entry column statistics.

No additional data scan: this reuses the ``null_value_counts``/``value_counts``/``lower_bounds``/
``upper_bounds`` fields that already exist on every ``DataFile`` manifest entry the scanner
iterates for other stats (see ``scanner/analyze.py::iter_data_files``) — the Iceberg spec requires
writers to populate these on every data file. Aggregating them costs nothing beyond the walk Cryo
already does.

Reference: "Zero-Scan Data Quality: Leveraging Table Format Metadata for Continuous Observability
at Scale" (FORMATS '26, arXiv:2605.30308). That paper's fuller scope also covers NDV via Puffin
sketches, which stays out of scope here — Puffin sketch reading needs a separate feasibility check
against PyIceberg's current API. Bounds decoding (this file) uses
``pyiceberg.conversions.from_bytes`` with the schema's own field type, verified against PyIceberg
directly (round-tripped ``to_bytes``/``from_bytes`` for a typed field) rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class _ColumnAccum:
    column_name: str
    null_count: int = 0
    value_count: int = 0
    min_value: Any = None
    max_value: Any = None
    ndv_estimate: int = 0


@dataclass(frozen=True)
class ColumnQualityStats:
    field_id: int
    column_name: str
    null_count: int
    value_count: int
    null_rate: float
    min_value: Any = None
    max_value: Any = None
    ndv_estimate: int | None = None


class ColumnQualityAccumulator:
    """Accumulate per-column null/value counts and value bounds across data files."""

    def __init__(self, schema: object | None) -> None:
        self._schema = schema
        self._accum: dict[int, _ColumnAccum] = {}
        self._name_cache: dict[int, str] = {}
        self._type_cache: dict[int, Any] = {}

    def add(self, data_file: object) -> None:
        null_counts = _int_mapping(getattr(data_file, "null_value_counts", None))
        value_counts = _int_mapping(getattr(data_file, "value_counts", None))
        lower_bounds = _raw_mapping(getattr(data_file, "lower_bounds", None))
        upper_bounds = _raw_mapping(getattr(data_file, "upper_bounds", None))
        distinct_counts = _int_mapping(getattr(data_file, "distinct_counts", None))
        puffin_ndv = _puffin_ndv_counts(data_file)

        field_ids = (
            set(null_counts)
            | set(value_counts)
            | set(lower_bounds)
            | set(upper_bounds)
            | set(distinct_counts)
            | set(puffin_ndv)
        )
        for field_id in field_ids:
            accum = self._accum.setdefault(
                field_id, _ColumnAccum(column_name=self._resolve_name(field_id))
            )
            accum.null_count += null_counts.get(field_id, 0)
            accum.value_count += value_counts.get(field_id, 0)
            accum.ndv_estimate = max(
                accum.ndv_estimate,
                distinct_counts.get(field_id, 0),
                puffin_ndv.get(field_id, 0),
            )

            low = self._decode_bound(field_id, lower_bounds.get(field_id))
            if low is not None and (accum.min_value is None or low < accum.min_value):
                accum.min_value = low
            high = self._decode_bound(field_id, upper_bounds.get(field_id))
            if high is not None and (accum.max_value is None or high > accum.max_value):
                accum.max_value = high

            ndv = distinct_counts.get(field_id) or puffin_ndv.get(field_id)
            if ndv is not None:
                accum.ndv_estimate = max(accum.ndv_estimate, ndv)

    def _resolve_name(self, field_id: int) -> str:
        if field_id in self._name_cache:
            return self._name_cache[field_id]
        name = str(field_id)
        field_obj = self._find_field(field_id)
        if field_obj is not None:
            name = str(getattr(field_obj, "name", name))
        self._name_cache[field_id] = name
        return name

    def _find_field(self, field_id: int) -> Any:
        finder = getattr(self._schema, "find_field", None) if self._schema is not None else None
        if not callable(finder):
            return None
        try:
            return finder(field_id)
        except Exception:
            return None

    def _decode_bound(self, field_id: int, raw_value: Any) -> Any:
        """Decode a manifest bound value using the schema's field type.

        Manifest lower/upper bounds are serialized per Iceberg's single-value binary encoding
        (``bytes``), keyed by field id — comparing the raw bytes directly is not meaningful for
        numeric types, so this always decodes through the schema's declared type first.
        """
        if raw_value is None:
            return None
        if not isinstance(raw_value, bytes | bytearray):
            # Already a native value (e.g. a fake/test double, or a future PyIceberg version
            # that decodes eagerly) — use as-is rather than guessing at a byte format.
            return raw_value
        if field_id not in self._type_cache:
            field_obj = self._find_field(field_id)
            self._type_cache[field_id] = getattr(field_obj, "field_type", None)
        field_type = self._type_cache[field_id]
        if field_type is None:
            return None
        try:
            from pyiceberg.conversions import from_bytes

            return from_bytes(field_type, bytes(raw_value))
        except Exception:
            return None

    def finish(self) -> list[ColumnQualityStats]:
        results = []
        for field_id, accum in sorted(self._accum.items()):
            null_rate = (
                round(accum.null_count / accum.value_count, 4) if accum.value_count else 0.0
            )
            results.append(
                ColumnQualityStats(
                    field_id=field_id,
                    column_name=accum.column_name,
                    null_count=accum.null_count,
                    value_count=accum.value_count,
                    null_rate=null_rate,
                    min_value=_jsonable(accum.min_value),
                    max_value=_jsonable(accum.max_value),
                    ndv_estimate=accum.ndv_estimate or None,
                )
            )
        return results


def summarize_column_quality(columns: list[ColumnQualityStats]) -> dict[str, Any]:
    """Table-level rollup for health/anomaly-detection consumption."""
    scored = [c for c in columns if c.value_count > 0]
    if not scored:
        return {"avg_null_rate": 0.0, "max_null_rate": 0.0, "worst_null_column": None}
    avg_null_rate = round(sum(c.null_rate for c in scored) / len(scored), 4)
    worst = max(scored, key=lambda c: c.null_rate)
    return {
        "avg_null_rate": avg_null_rate,
        "max_null_rate": worst.null_rate,
        "worst_null_column": worst.column_name if worst.null_rate > 0 else None,
    }


def _int_mapping(value: Any) -> dict[int, int]:
    result: dict[int, int] = {}
    for key, count in _raw_mapping(value).items():
        if count is None:
            continue
        try:
            result[int(key)] = int(count)
        except (TypeError, ValueError):
            continue
    return result


def _raw_mapping(value: Any) -> dict[int, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        items: Any = value.items()
    else:
        items_fn = getattr(value, "items", None)
        if not callable(items_fn):
            return {}
        try:
            items = items_fn()
        except Exception:
            return {}
    result: dict[int, Any] = {}
    for key, raw_value in items:
        try:
            result[int(key)] = raw_value
        except (TypeError, ValueError):
            continue
    return result


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    if isinstance(value, bytes | bytearray):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _puffin_ndv_counts(data_file: object) -> dict[int, int]:
    """Extract NDV estimates from Puffin blob metadata when present on the data file."""
    result: dict[int, int] = {}
    puffin = getattr(data_file, "puffin_metadata", None) or getattr(data_file, "puffin", None)
    if puffin is None:
        return result
    blobs = puffin if isinstance(puffin, list) else getattr(puffin, "blobs", None)
    if not blobs:
        return result
    for blob in blobs:
        blob_type = str(getattr(blob, "type", "") or getattr(blob, "blob_type", "")).lower()
        if "ndv" not in blob_type and "theta" not in blob_type and "sketch" not in blob_type:
            continue
        field_id = getattr(blob, "fields", None) or getattr(blob, "field_ids", None)
        ndv = getattr(blob, "ndv", None) or getattr(blob, "distinct_count", None)
        if ndv is None:
            continue
        ids: list[int] = []
        if isinstance(field_id, int):
            ids = [field_id]
        elif isinstance(field_id, (list, tuple)):
            ids = [int(i) for i in field_id]
        for fid in ids:
            try:
                result[fid] = max(result.get(fid, 0), int(ndv))
            except (TypeError, ValueError):
                continue
    return result
