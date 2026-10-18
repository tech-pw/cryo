"""Shared DELETE rendering for expire_data. Dialects only choose identifier quotes."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def expire_data_metadata(operation_type: str, params: dict[str, Any]) -> dict[str, Any]:
    if operation_type != "expire_data":
        return {}
    return {
        "cutoff": params.get("cutoff"),
        "field": params.get("field"),
        "estimated_bytes": params.get("estimated_bytes"),
        "estimated_partitions": params.get("estimated_partitions"),
        "row_level": bool(params.get("row_level")),
    }


def render_expire_data_delete(table_sql: str, params: dict[str, Any], *, quote: str) -> str:
    field = _quote(str(params.get("field") or ""), quote)
    literal = _literal(params)
    return f"DELETE FROM {table_sql} WHERE {field} < {literal}"


def _quote(name: str, style: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"expire_data field is not a simple identifier: {name!r}")
    if style == "trino":
        return f'"{name}"'
    if style == "spark":
        return f"`{name}`"
    raise ValueError(f"unknown identifier quote style: {style}")


def _literal(params: dict[str, Any]) -> str:
    cutoff = _cutoff(params)
    field_type = str(params.get("field_type") or "timestamp")
    if field_type in {"timestamp", "timestamptz"}:
        text = cutoff.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
        return f"TIMESTAMP '{text}'"
    if field_type == "date":
        return f"DATE '{cutoff.date().isoformat()}'"
    if field_type == "long":
        number_format = str(params.get("number_format") or "TIMESTAMP_MS")
        if number_format == "TIMESTAMP_S":
            return str(int(cutoff.timestamp()))
        if number_format == "DATE_DAYS":
            return str((cutoff.date() - date(1970, 1, 1)).days)
        if number_format != "TIMESTAMP_MS":
            raise ValueError(f"unsupported expire_data number format: {number_format}")
        return str(int(cutoff.timestamp() * 1000))
    if field_type == "string":
        pattern = str(params.get("datetime_pattern") or "yyyy-MM-dd")
        if pattern != "yyyy-MM-dd":
            raise ValueError("expire_data string fields only support datetime pattern yyyy-MM-dd")
        return f"'{cutoff.date().isoformat()}'"
    raise ValueError(f"unsupported expire_data field type: {field_type}")


def _cutoff(params: dict[str, Any]) -> datetime:
    raw = params.get("cutoff")
    if not isinstance(raw, str) or not raw:
        raise ValueError("expire_data is missing a planner-computed cutoff")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
