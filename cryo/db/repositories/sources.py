from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cryo.db.models import Source, Table


def normalize_maintenance_schema_names(names: Sequence[str] | None) -> list[str]:
    """Deduplicate and strip; preserve first-seen order."""
    if not names:
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in names:
        name = str(raw).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
    return normalized


def source_allowed_schemas(source: Source) -> set[str] | None:
    """Return the allowlisted schema names, or None when every namespace is in scope."""
    allowed = normalize_maintenance_schema_names(source.maintenance_schema_names or [])
    if not allowed:
        return None
    return set(allowed)


def intersect_requested_schemas(
    source: Source,
    requested_schemas: set[str],
) -> set[str]:
    """Intersect an explicit scan scope with the source allowlist."""
    allowed = source_allowed_schemas(source)
    if allowed is None:
        return requested_schemas
    if not requested_schemas:
        return allowed
    return requested_schemas & allowed


def deactivate_out_of_scope_tables(
    session: Session,
    *,
    source_id: uuid.UUID,
    maintenance_schema_names: Sequence[str],
) -> int:
    """Mark tables outside the new allowlist inactive; empty allowlist re-enables all."""
    allowed = normalize_maintenance_schema_names(maintenance_schema_names)
    if not allowed:
        result = session.execute(
            update(Table)
            .where(Table.source_id == source_id, Table.is_active.is_(False))
            .values(is_active=True)
        )
        return int(result.rowcount or 0)

    allowed_set = set(allowed)
    deactivated = session.execute(
        update(Table)
        .where(
            Table.source_id == source_id,
            Table.is_active.is_(True),
            Table.schema_name.notin_(allowed_set),
        )
        .values(is_active=False)
    )
    reactivated = session.execute(
        update(Table)
        .where(
            Table.source_id == source_id,
            Table.is_active.is_(False),
            Table.schema_name.in_(allowed_set),
        )
        .values(is_active=True)
    )
    return int((deactivated.rowcount or 0) + (reactivated.rowcount or 0))


def table_in_source_scope(table: Table, source: Source) -> bool:
    allowed = source_allowed_schemas(source)
    if allowed is None:
        return True
    return table.schema_name in allowed


def load_sources_by_id(
    session: Session, source_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Source]:
    if not source_ids:
        return {}
    rows = session.scalars(select(Source).where(Source.id.in_(source_ids)))
    return {row.id: row for row in rows}
