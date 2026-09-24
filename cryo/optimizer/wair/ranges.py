"""WAIR workload inputs: query ranges over a sliding window, and comparable key values.

Query ranges come from the Trino query log (``telemetry.iceberg_source``); file bounds from the
scanner's per-file samples. Both are reduced to float sort keys here so a date literal in SQL
(``DATE '2026-09-01'``), a Python-scanned bound (``2026-09-01``) and a Go-scanned bound
(``2026-09-01T00:00:00Z``) compare on one axis.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from cryo.scanner.quality import bound_safe_type

WINDOW_DAYS = 7
MAX_RANGES = 100
MAX_SAMPLE_COLUMNS = 3


def sort_key(value: object) -> float | None:
    """Numeric and ISO date/time values as a float on one axis; anything else is None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.timestamp()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp()
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return sort_key(parsed)


def is_range_literal(value: object) -> bool:
    return value is None or sort_key(value) is not None


def merge_range_window(
    existing: object,
    hits: Mapping[tuple[str, Any, Any], Mapping[str, float]],
    *,
    day: date,
    window_days: int = WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Fold one window's range hits into the stored per-day counts and drop expired days.

    ``hits`` maps ``(column, lower, upper)`` to ``{"queries", "cpu_ms", "input_bytes"}``. The
    stored list is what WAIR reads as its sliding window W (paper §4.2); ``frequency`` is the
    query count inside the window.
    """
    cutoff = (day - timedelta(days=window_days - 1)).isoformat()
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in existing if isinstance(existing, list) else []:
        if not isinstance(item, Mapping) or not isinstance(item.get("column"), str):
            continue
        days = {
            d: dict(v)
            for d, v in (item.get("days") or {}).items()
            if isinstance(v, Mapping) and d >= cutoff
        }
        if days:
            key = (item["column"], repr(item.get("lower")), repr(item.get("upper")))
            merged[key] = {
                "column": item["column"],
                "lower": item.get("lower"),
                "upper": item.get("upper"),
                "days": days,
            }
    today = day.isoformat()
    for (column, lower, upper), counts in hits.items():
        key = (column, repr(lower), repr(upper))
        entry = merged.setdefault(
            key, {"column": column, "lower": lower, "upper": upper, "days": {}}
        )
        bucket = entry["days"].setdefault(today, {"queries": 0, "cpu_ms": 0.0, "input_bytes": 0})
        for field in ("queries", "cpu_ms", "input_bytes"):
            bucket[field] = bucket.get(field, 0) + counts.get(field, 0)
    ranked = []
    for entry in merged.values():
        days = entry["days"].values()
        entry["frequency"] = int(sum(d.get("queries", 0) for d in days))
        entry["cpu_ms"] = float(sum(d.get("cpu_ms", 0) for d in days))
        entry["input_bytes"] = int(sum(d.get("input_bytes", 0) for d in days))
        ranked.append(entry)
    ranked.sort(key=lambda e: (-e["frequency"], e["column"], repr(e["lower"]), repr(e["upper"])))
    return ranked[:MAX_RANGES]


def wair_sample_columns(raw: object, *, max_columns: int = MAX_SAMPLE_COLUMNS) -> list[str]:
    """Columns worth per-file bound samples: queried by range, and numeric/temporal.

    Types come from the table's last scanned ``column_quality``; a column with no recorded type
    is never sampled.
    """
    data = raw if isinstance(raw, Mapping) else {}
    types = {
        str(c.get("column_name")): c.get("column_type")
        for c in data.get("column_quality") or []
        if isinstance(c, Mapping)
    }
    frequency: dict[str, int] = {}
    for item in data.get("wair_query_ranges") or []:
        if isinstance(item, Mapping) and isinstance(item.get("column"), str):
            column = item["column"]
            frequency[column] = frequency.get(column, 0) + int(item.get("frequency") or 0)
    ranked = sorted(frequency, key=lambda c: (-frequency[c], c))
    return [c for c in ranked if bound_safe_type(types.get(c))][:max_columns]
