"""Check telemetry-derived column names against a table's scanned schema (#517).

Sort and z-order recommendations come from columns parsed out of query text, which can pick
up aliases, table names or truncated tokens. Anything offered to a user has to exist on the
table, spelled the way the schema spells it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import TableColumn, TableStats


def table_schema_columns(session: Session, table_id: uuid.UUID) -> list[str]:
    """Column names from the last scan: ``table_columns``, else the scanner's column list."""
    names = list(
        session.scalars(select(TableColumn.column_name).where(TableColumn.table_id == table_id))
    )
    if names:
        return names
    raw = session.scalar(select(TableStats.raw).where(TableStats.table_id == table_id)) or {}
    return [
        str(item["column_name"])
        for item in raw.get("column_quality") or []
        if isinstance(item, dict) and item.get("column_name")
    ]


def resolve_columns(
    candidates: Iterable[str], schema: Sequence[str]
) -> tuple[list[str], list[str]]:
    """Split candidates into (schema spelling of known columns, unknown names).

    Matching is case-insensitive: query telemetry routinely lowercases names, while Iceberg
    binds them case-sensitively, so a match is returned in the schema's casing.
    """
    by_lower = {name.lower(): name for name in schema}
    resolved: list[str] = []
    unknown: list[str] = []
    for candidate in candidates:
        real = by_lower.get(candidate.lower())
        if real is None:
            unknown.append(candidate)
        elif real not in resolved:
            resolved.append(real)
    return resolved, unknown
