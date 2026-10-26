from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, aliased

from cryo.db.models import Source, Table

# Critical vitals action plan, 2026-09-20, P1: how long a table confirmed to have a missing
# catalog metadata/manifest file (is_table_not_found_error) stays excluded from scheduling
# before a bounded verification probe is allowed to re-check it. Deliberately much longer than
# any scan/maintenance cadence -- live-confirmed the same table was re-admitted and re-failed
# on essentially every wave with no repair in between (consecutive_scan_failures=60 observed on
# one such table) -- but still finite, since a catalog re-registration or metadata-location fix
# does happen and must not be permanently invisible to the fleet just because it once 404'd.
_MISSING_METADATA_RETRY_AFTER = timedelta(hours=24)


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
        result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
            update(Table)
            .where(
                Table.source_id == source_id,
                Table.is_active.is_(False),
                Table.removed_from_catalog_at.is_(None),
            )
            .values(is_active=True)
        )
        return int(result.rowcount or 0)

    allowed_set = set(allowed)
    deactivated: CursorResult[Any] = session.execute(  # type: ignore[assignment]
        update(Table)
        .where(
            Table.source_id == source_id,
            Table.is_active.is_(True),
            Table.schema_name.notin_(allowed_set),
        )
        .values(is_active=False)
    )
    reactivated: CursorResult[Any] = session.execute(  # type: ignore[assignment]
        update(Table)
        .where(
            Table.source_id == source_id,
            Table.is_active.is_(False),
            Table.schema_name.in_(allowed_set),
            Table.removed_from_catalog_at.is_(None),
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


def deactivate_orphaned_evolving_catalog_entries(session: Session) -> int:
    """One-time cleanup for a historical table-rename artifact (investigated 2026-08-22..25).

    Found live: 1,371 active tables with ``table_name LIKE '%_evolving'`` and ``location IS
    NULL`` -- e.g. ``ezpay.gold_notifications_evolving`` (location=None) alongside a real,
    correctly-located sibling ``ezpay.gold_notifications``
    (location='s3a://.../notifications_evolving' -- the S3 path itself still carries the old
    "_evolving" name as a directory segment). This is consistent with a historical rename/
    re-registration event where the old catalog entry was never deactivated: something renamed
    the table but the stale ``*_evolving`` row kept getting scanned/dispatched forever after,
    with no location to actually load. Confirmed via the production health checklist's
    repeat_failures check: this is the single worst repeat-failure pattern in the fleet
    (orphaned_catalog_entry/NoSuchKey, 600-3,000+ failures in any 3-day window depending on
    scan cadence, 480-1,260 distinct tables), burning a metastore round-trip and a
    guaranteed-failing scan every cycle for weeks (``consecutive_scan_failures`` observed up to
    151, averaging ~76, ``first_seen_at`` as early as 2026-07-09) for zero possible benefit --
    ``location IS NULL`` means there is nothing Cryo could ever successfully scan here.

    Deactivates (``is_active=False``, the same mechanism ``deactivate_out_of_scope_tables``
    above uses -- excluded from scheduling via ``scheduler/rules.py``'s ``is_active`` gate, not
    deleted, so the row and its history stay inspectable/reversible) only rows matching ALL of:
    table_name ends in ``_evolving``, location IS NULL, AND a same-schema sibling exists under
    the name with the suffix stripped that DOES have a non-null location. That third condition
    is the actual safety boundary -- confirmed live, 100% (1,371/1,371) of the matched rows had
    such a sibling, so this only touches entries with a verified-good replacement already doing
    the real work, never an ambiguous or genuinely-unresolved one.
    """
    stale = aliased(Table)
    sibling = aliased(Table)

    # sibling.table_name + "_evolving" == stale.table_name, via concatenation (portable across
    # Postgres and sqlite -- the test suite's engine) rather than a substring/LEFT() function,
    # which differ across dialects. Equivalent to stripping the suffix off stale.table_name, just
    # expressed the other direction so it works everywhere.
    stale_ids = select(stale.id).where(
        stale.is_active.is_(True),
        stale.table_name.like("%\\_evolving", escape="\\"),
        stale.location.is_(None),
        select(sibling.id)
        .where(
            sibling.schema_name == stale.schema_name,
            (sibling.table_name + "_evolving") == stale.table_name,
            sibling.location.is_not(None),
        )
        .exists(),
    )
    result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
        update(Table).where(Table.id.in_(stale_ids)).values(is_active=False)
    )
    return int(result.rowcount or 0)


def deactivate_tables_by_id(session: Session, table_ids: Sequence[uuid.UUID]) -> int:
    """Deactivate an explicit, caller-verified list of tables (is_active=False) -- not a
    heuristic sweep. For '*_evolving' rows that don't fit deactivate_orphaned_evolving_
    catalog_entries' automated safety condition (location IS NULL): some historical-rename
    orphans kept a non-null `location` pointing at a path whose metadata/manifest files are
    now 404 in S3, so the null-location heuristic above misses them.

    User-requested, 2026-09-09, top 10 by consecutive_scan_failures among active '*_evolving'
    tables: each individually confirmed query_count_30d=0, last_snapshot_committed_at 4-7
    months stale (Feb-Apr), and a live S3 404 on the table's own registered location's
    metadata/manifest -- i.e. verified dead and verified unused per-table, not matched by a
    broadened heuristic. Same reversible mechanism (is_active=False, row/history untouched,
    no S3/file deletion) as the bulk cleanup -- no prod outage risk, nothing currently reads
    these. Caller is responsible for the verification; this function only applies it."""
    if not table_ids:
        return 0
    result: CursorResult[Any] = session.execute(  # type: ignore[assignment]
        update(Table).where(Table.id.in_(table_ids)).values(is_active=False)
    )
    return int(result.rowcount or 0)


def record_missing_metadata(
    session: Session,
    table_id: uuid.UUID,
    *,
    reason: str,
    now: datetime | None = None,
    retry_after: timedelta = _MISSING_METADATA_RETRY_AFTER,
) -> None:
    """Record that this table's registered location's metadata.json/manifest was confirmed
    missing in S3 (caller must have already checked ``cryo.engines.base.is_table_not_found_
    error(error)`` -- this function does no classification of its own) -- containment, NOT
    deactivation. Sets a future retry-after so scheduling stops re-selecting this table for a
    guaranteed-failing maintenance attempt every wave (see
    cryo.scheduler.rules.tables_for_preset's is_active-style gate), while leaving is_active,
    the row, and its history completely untouched -- fully reversible, no data/S3 changes,
    matching the plan's "missing metadata is not permission to deactivate the table."

    Idempotent and safe to call repeatedly: each call just re-arms the retry-after window from
    ``now``, so a table that keeps failing the same way never becomes eligible again until a
    verification probe (not built here -- see record's own "bounded verification probe" note in
    the action plan) or a fresh catalog scan clears it via clear_missing_metadata.
    """
    current = now or datetime.now(UTC)
    session.execute(
        update(Table)
        .where(Table.id == table_id)
        .values(
            maintenance_missing_metadata_reason=reason,
            maintenance_retry_after=current + retry_after,
        )
    )


def clear_missing_metadata(session: Session, table_id: uuid.UUID) -> None:
    """Release the containment -- call this once a fresh scan/probe confirms the table's
    metadata is readable again (e.g. the scanner successfully reads it, or a manual
    verification probe finds the catalog pointer now resolves). Never called automatically by
    record_missing_metadata itself; a caller must have positive evidence of repair first."""
    session.execute(
        update(Table)
        .where(Table.id == table_id)
        .values(maintenance_missing_metadata_reason=None, maintenance_retry_after=None)
    )
