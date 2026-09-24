"""Zero-scan data quality signals derived from Iceberg manifest-entry column statistics.

No additional data scan: this reuses the ``null_value_counts``/``value_counts``/``lower_bounds``/
``upper_bounds`` fields that already exist on every ``DataFile`` manifest entry the scanner
iterates for other stats (see ``scanner/analyze.py::iter_data_files``) — the Iceberg spec requires
writers to populate these on every data file. Aggregating them costs nothing beyond the walk Cryo
already does.

Reference: "Zero-Scan Data Quality: Leveraging Table Format Metadata for Continuous Observability
at Scale" (FORMATS '26, arXiv:2605.30308). That paper's fuller scope also covers NDV via Puffin
sketches; this was attempted but dropped after verifying against PyIceberg 0.11.1 that ``DataFile``
manifest entries carry no ``distinct_counts`` or Puffin blob reference — that data lives in
``TableMetadata.statistics`` (table/snapshot-level Puffin files), not per manifest entry, and
PyIceberg's own Puffin reader only decodes deletion-vector blobs, not NDV sketches. Bounds decoding
(this file) uses ``pyiceberg.conversions.from_bytes`` with the schema's own field type, verified
against PyIceberg directly (round-tripped ``to_bytes``/``from_bytes`` for a typed field) rather than
assumed.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

# Bounds are kept only for orderable non-text types. Iceberg writers store a column's min/max
# string verbatim (truncated to 16 chars), so for email/phone/name columns the "bound" is a real
# person's value; range-violation checks only need numeric and temporal bounds anyway.
_BOUND_SAFE_TYPES = frozenset(
    {
        "boolean",
        "int",
        "long",
        "float",
        "double",
        "date",
        "time",
        "timestamp",
        "timestamptz",
        "timestamp_ns",
        "timestamptz_ns",
    }
)


def bound_safe_type(type_label: str | None) -> bool:
    if not type_label:
        return False
    return type_label in _BOUND_SAFE_TYPES or type_label.startswith("decimal(")


@dataclass
class _ColumnAccum:
    column_name: str
    column_type: str | None = None
    null_count: int = 0
    value_count: int = 0
    min_value: Any = None
    max_value: Any = None


@dataclass(frozen=True)
class ColumnQualityStats:
    field_id: int
    column_name: str
    null_count: int
    value_count: int
    null_rate: float
    column_type: str | None = None
    min_value: Any = None
    max_value: Any = None


class ColumnQualityAccumulator:
    """Accumulate per-column null/value counts and value bounds across data files."""

    def __init__(self, schema: object | None) -> None:
        self._schema = schema
        self._accum: dict[int, _ColumnAccum] = {}
        self._name_cache: dict[int, str] = {}
        # Holds the raw Iceberg PrimitiveType per field id (used by from_bytes in _decode_bound).
        self._type_cache: dict[int, Any] = {}
        # Holds the rendered type label per field id (used for column_type persistence).
        self._type_label_cache: dict[int, str | None] = {}

    def add(self, data_file: object) -> None:
        null_counts = _int_mapping(getattr(data_file, "null_value_counts", None))
        value_counts = _int_mapping(getattr(data_file, "value_counts", None))
        lower_bounds = _raw_mapping(getattr(data_file, "lower_bounds", None))
        upper_bounds = _raw_mapping(getattr(data_file, "upper_bounds", None))

        field_ids = set(null_counts) | set(value_counts) | set(lower_bounds) | set(upper_bounds)
        for field_id in field_ids:
            accum = self._accum.setdefault(
                field_id, _ColumnAccum(column_name=self._resolve_name(field_id))
            )
            # Capture the Iceberg schema type once per field id, so it isn't only
            # resolved when bounds need decoding (which can miss columns that have
            # nulls but no lower/upper bounds).
            if accum.column_type is None:
                accum.column_type = self._resolve_type(field_id)
            accum.null_count += null_counts.get(field_id, 0)
            accum.value_count += value_counts.get(field_id, 0)

            if not bound_safe_type(accum.column_type):
                continue
            low = self._decode_bound(field_id, lower_bounds.get(field_id))
            if low is not None and (accum.min_value is None or low < accum.min_value):
                accum.min_value = low
            high = self._decode_bound(field_id, upper_bounds.get(field_id))
            if high is not None and (accum.max_value is None or high > accum.max_value):
                accum.max_value = high

    def file_bound_sample(
        self, data_file: object, columns: Collection[str] | None = None
    ) -> dict[str, Any] | None:
        path = getattr(data_file, "file_path", None)
        if callable(path):
            path = path()
        if not isinstance(path, str) or not path:
            return None
        size = getattr(data_file, "file_size_in_bytes", 0)
        if callable(size):
            size = size()
        lower_bounds = _raw_mapping(getattr(data_file, "lower_bounds", None))
        upper_bounds = _raw_mapping(getattr(data_file, "upper_bounds", None))
        lower: dict[str, Any] = {}
        upper: dict[str, Any] = {}
        for field_id in sorted(set(lower_bounds) | set(upper_bounds)):
            if not bound_safe_type(self._resolve_type(field_id)):
                continue
            name = self._resolve_name(field_id)
            if columns is not None and name not in columns:
                continue
            low = self._decode_bound(field_id, lower_bounds.get(field_id))
            high = self._decode_bound(field_id, upper_bounds.get(field_id))
            if low is not None:
                lower[name] = _jsonable(low)
            if high is not None:
                upper[name] = _jsonable(high)
        if not lower or not upper:
            return None
        return {
            "file_path": path,
            "size_bytes": max(0, int(size or 0)),
            "lower_bounds": lower,
            "upper_bounds": upper,
        }

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
            null_rate = round(accum.null_count / accum.value_count, 4) if accum.value_count else 0.0
            results.append(
                ColumnQualityStats(
                    field_id=field_id,
                    column_name=accum.column_name,
                    null_count=accum.null_count,
                    value_count=accum.value_count,
                    null_rate=null_rate,
                    column_type=accum.column_type,
                    min_value=_jsonable(accum.min_value),
                    max_value=_jsonable(accum.max_value),
                )
            )
        return results

    def _resolve_type(self, field_id: int) -> str | None:
        """Resolve the Iceberg field type for ``field_id`` to a short label.

        We cache per field id because the type lookup goes through PyIceberg's schema
        walker and runs once per field. Nested types render in PyIceberg's default
        ``__str__`` form (e.g. ``list<string>``, ``map<string,bigint>``) which is the
        same form used for partition ``col_type`` upstream.
        """
        if field_id in self._type_label_cache:
            return self._type_label_cache[field_id]
        field_obj = self._find_field(field_id)
        if field_obj is None:
            return None
        raw_type = getattr(field_obj, "field_type", None)
        if raw_type is None:
            return None
        try:
            rendered: str | None = str(raw_type) or None
        except Exception:
            return None
        self._type_label_cache[field_id] = rendered
        return rendered


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
