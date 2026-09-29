"""Numeric grounding and allowlisted chart blocks. Null is never coerced to zero."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b")
_SIZE = re.compile(
    r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(bytes|byte|KiB|MiB|GiB|TiB|KB|MB|GB|TB|B)\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"\b(\d+(?:\.\d+)?)\s*%")
_NUMBER = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b")

_KINDS = {
    "ranked_bars",
    "time_series",
    "before_after",
    "distribution",
    "stacked_share",
    "run_lanes",
}

_BINARY = {
    "b": 1,
    "byte": 1,
    "bytes": 1,
    "kb": 1024,
    "kib": 1024,
    "mb": 1024**2,
    "mib": 1024**2,
    "gb": 1024**3,
    "gib": 1024**3,
    "tb": 1024**4,
    "tib": 1024**4,
}
_DECIMAL = {"kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4}


def compute_answer_line(
    kind: str, evidence: dict[str, Any], field_map: dict[str, Any]
) -> str | None:
    """Only a tested function may set answer_line. This one does not invent a sentence."""
    del kind, evidence, field_map
    return None


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-6, abs(right) * 1e-6)


def _number(token: str) -> float:
    return float(token.replace(",", ""))


def _walk(value: Any, numbers: list[float], strings: list[str]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, datetime):
        strings.append(value.isoformat())
        strings.append(value.date().isoformat())
        return
    if isinstance(value, int | float):
        numbers.append(float(value))
        return
    if isinstance(value, str):
        strings.append(value)
        try:
            numbers.append(float(value.replace(",", "")))
        except ValueError:
            return
        return
    if isinstance(value, dict):
        for item in value.values():
            _walk(item, numbers, strings)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _walk(item, numbers, strings)


def _scales(unit: str) -> list[int]:
    key = unit.lower()
    scales = []
    if key in _BINARY:
        scales.append(_BINARY[key])
    decimal = _DECIMAL.get(key)
    if decimal is not None and decimal not in scales:
        scales.append(decimal)
    return scales or [1]


def _size_matches(coefficient: float, unit: str, numbers: list[float]) -> bool:
    for scale in _scales(unit):
        target = coefficient * scale
        if any(_close(number, target) for number in numbers):
            return True
    return False


def _percent_matches(value: float, numbers: list[float]) -> bool:
    return any(_close(number, value) or _close(number, value / 100) for number in numbers)


def _date_matches(token: str, strings: list[str]) -> bool:
    day = token[:10]
    return any(day in item or token in item for item in strings)


def check_numbers(text: str, evidence_payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract numbers, percents, byte sizes, and dates. A miss is status=fallback."""
    numbers: list[float] = []
    strings: list[str] = []
    for payload in evidence_payloads:
        _walk(payload, numbers, strings)
    remaining = text
    missing: list[str] = []
    for match in _DATE.finditer(text):
        token = match.group(0)
        if not _date_matches(token, strings):
            missing.append(token)
    remaining = _DATE.sub(" ", remaining)
    for match in _SIZE.finditer(remaining):
        token = match.group(0)
        if not _size_matches(_number(match.group(1)), match.group(2), numbers):
            missing.append(token)
    remaining = _SIZE.sub(" ", remaining)
    for match in _PERCENT.finditer(remaining):
        token = match.group(0)
        if not _percent_matches(_number(match.group(1)), numbers):
            missing.append(token)
    remaining = _PERCENT.sub(" ", remaining)
    for match in _NUMBER.finditer(remaining):
        token = match.group(0)
        if not any(_close(number, _number(token)) for number in numbers):
            missing.append(token)
    if missing:
        return {"status": "fallback", "missing": missing}
    return {"status": "passed", "missing": []}


def _rows(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    raw = evidence.get("rows")
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    result = evidence.get("result")
    if isinstance(result, dict) and isinstance(result.get("rows"), list):
        return [row for row in result["rows"] if isinstance(row, dict)]
    return []


def _known_fields(evidence: dict[str, Any]) -> set[str]:
    fields = set(evidence.keys())
    result = evidence.get("result")
    if isinstance(result, dict):
        fields |= set(result.keys())
    units = evidence.get("units")
    if isinstance(units, dict):
        fields |= set(units.keys())
    for row in _rows(evidence):
        fields |= set(row.keys())
        break
    columns = evidence.get("columns")
    if isinstance(columns, list):
        for column in columns:
            if isinstance(column, str):
                fields.add(column)
            elif isinstance(column, dict) and isinstance(column.get("key"), str):
                fields.add(column["key"])
    return fields


def _mapped_fields(field_map: dict[str, Any]) -> list[str]:
    fields: list[str] = []
    for key in ("label", "value", "x", "before", "after", "lane", "start", "end"):
        value = field_map.get(key)
        if isinstance(value, str):
            fields.append(value)
    for key in ("series", "shares"):
        value = field_map.get(key)
        if isinstance(value, list):
            fields.extend(str(item) for item in value)
    return fields


def _unit_ok(evidence: dict[str, Any], field_map: dict[str, Any]) -> bool:
    requested = field_map.get("unit")
    if not isinstance(requested, str):
        return True
    units = evidence.get("units")
    if not isinstance(units, dict):
        return True
    value_field = field_map.get("value") or field_map.get("before")
    if isinstance(value_field, str) and value_field in units and units[value_field] != requested:
        return False
    series = field_map.get("series")
    if isinstance(series, list):
        for name in series:
            if name in units and units[name] != requested:
                return False
    return True


def _keep_null(value: Any) -> Any:
    return value


def build_chart_block(
    kind: str,
    evidence: dict[str, Any],
    field_map: dict[str, Any],
) -> dict[str, Any] | None:
    """Allowlisted chart. Unknown fields and unit mismatches return None. Null stays null."""
    if kind not in _KINDS:
        return None
    known = _known_fields(evidence)
    needed = _mapped_fields(field_map)
    if any(field not in known for field in needed):
        return None
    if not _unit_ok(evidence, field_map):
        return None
    rows = _rows(evidence)
    series: list[dict[str, Any]] = []
    if kind == "time_series":
        names = [str(item) for item in field_map.get("series") or []]
        if len(names) > 2:
            return None
        x_field = str(field_map.get("x") or "")
        points_cap = rows[:366]
        for name in names:
            series.append(
                {
                    "name": name,
                    "points": [
                        {"x": _keep_null(row.get(x_field)), "y": _keep_null(row.get(name))}
                        for row in points_cap
                    ],
                }
            )
    elif kind == "before_after":
        before = str(field_map["before"])
        after = str(field_map["after"])
        source = rows[0] if rows else evidence
        series.append(
            {
                "name": "before_after",
                "points": [
                    {"x": "before", "y": _keep_null(source.get(before))},
                    {"x": "after", "y": _keep_null(source.get(after))},
                ],
            }
        )
    elif kind == "stacked_share":
        shares = [str(item) for item in field_map.get("shares") or []]
        source = rows[0] if rows else evidence
        series.append(
            {
                "name": "share",
                "points": [{"x": name, "y": _keep_null(source.get(name))} for name in shares],
            }
        )
    elif kind == "run_lanes":
        lane = str(field_map.get("lane") or "lane")
        start = str(field_map.get("start") or "start")
        end = str(field_map.get("end") or "end")
        series.append(
            {
                "name": "lanes",
                "points": [
                    {
                        "lane": _keep_null(row.get(lane)),
                        "start": _keep_null(row.get(start)),
                        "end": _keep_null(row.get(end)),
                    }
                    for row in rows[:200]
                ],
            }
        )
    else:
        label = str(field_map.get("label") or "")
        value = str(field_map.get("value") or "")
        cap = 25 if kind == "ranked_bars" else 200
        series.append(
            {
                "name": value,
                "points": [
                    {"x": _keep_null(row.get(label)), "y": _keep_null(row.get(value))}
                    for row in rows[:cap]
                ],
            }
        )
    table_rows = []
    for row in rows[:200]:
        table_rows.append({key: _keep_null(row.get(key)) for key in needed if key in row})
    evidence_ids = evidence.get("evidence_ids")
    if not isinstance(evidence_ids, list):
        evidence_id = evidence.get("evidence_id")
        evidence_ids = [evidence_id] if isinstance(evidence_id, str) else []
    return {
        "type": "chart",
        "kind": kind,
        "title": str(field_map.get("title") or kind),
        "question": None,
        "answer_line": compute_answer_line(kind, evidence, field_map),
        "unit": field_map.get("unit") if isinstance(field_map.get("unit"), str) else None,
        "window": None,
        "timezone": None,
        "as_of": None,
        "coverage": {
            "points": sum(len(item["points"]) for item in series),
            "rows": len(table_rows),
        },
        "provenance": "measured",
        "series": series,
        "markers": None,
        "target": None,
        "table": {"columns": [{"key": field} for field in needed], "rows": table_rows},
        "evidence_ids": evidence_ids,
    }


def evidence_fallback_table(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    evidence_ids: list[str] = []
    for payload in payloads:
        evidence_id = str(payload.get("evidence_id") or "")
        if evidence_id:
            evidence_ids.append(evidence_id)
        result = payload.get("result")
        if not isinstance(result, dict):
            result = payload
        flat: dict[str, Any] = {}
        for key, value in result.items():
            if isinstance(value, dict | list):
                continue
            flat[str(key)] = value
        if flat:
            rows.append(flat)
    columns = [{"key": key, "label": key} for key in list({key for row in rows for key in row})[:8]]
    return build_ranked_table(columns, rows, len(rows), False, evidence_ids)


def build_ranked_table(
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    total: int,
    truncated: bool,
    evidence_ids: list[str],
) -> dict[str, Any]:
    capped = rows[:200]
    return {
        "type": "ranked_table",
        "columns": columns,
        "rows": capped,
        "total": total,
        "truncated": truncated or len(rows) > 200,
        "cursor": None,
        "open_url": None,
        "csv_url": None,
        "evidence_ids": evidence_ids,
    }
