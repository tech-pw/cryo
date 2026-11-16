"""Tables a periodic loop finds gone from the catalog (#561).

The scan listing is the source of truth for tombstoning (``cryo.scanner.reconcile_missing``).
Between scans, a timer-driven loader (layout analyzer, property autopilot, commit watcher)
can still pick a table that was dropped, usually a short-lived temp table. Those loaders call
``note_missing_table``: one INFO line, one metric increment, and the table's existing
``maintenance_retry_after`` hold, so no loop retries it every tick and maintenance does not
dispatch it. The next scan's reconcile then tombstones it, or restores it if it came back.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

logger = logging.getLogger(__name__)

TABLE_MISSING_REASON = "table_missing_from_catalog"

# Class names catalogs raise for a table that does not exist: pyiceberg's NoSuchTableError
# (REST, Glue, Hive, SQL all map to it), Spark/Java NoSuchTableException text, Hive's
# NoSuchObjectException and Glue's EntityNotFoundException when a client surfaces them raw.
# NoSuchIcebergTableError (exists, but is not Iceberg) is excluded explicitly below.
_MISSING_NAMES = ("nosuchtable", "nosuchobject", "entitynotfound")


class TableMissingFromCatalogError(LookupError):
    """The catalog says this table does not exist."""


def is_missing_table_error(exc: BaseException | None) -> bool:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, TableMissingFromCatalogError):
            return True
        # pyiceberg's NoSuchIcebergTableError subclasses NoSuchTableError.
        if "nosuchiceberg" in type(exc).__name__.lower():
            return False
        if any(
            needle in cls.__name__.lower() for cls in type(exc).__mro__ for needle in _MISSING_NAMES
        ):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def table_not_held_missing(now: datetime | None = None) -> ColumnElement[bool]:
    """SQL filter: drop tables currently held by ``note_missing_table``."""
    from cryo.db.models import Table

    current = now or datetime.now(UTC)
    # NULL-safe on purpose: a plain == on a NULL reason would make NOT(...) NULL and drop
    # every table that was never held.
    return or_(
        Table.maintenance_missing_metadata_reason.is_distinct_from(TABLE_MISSING_REASON),
        and_(
            Table.maintenance_retry_after.is_not(None),
            Table.maintenance_retry_after <= current,
        ),
    )


def is_held_missing(table: Any, now: datetime | None = None) -> bool:
    """Python twin of ``table_not_held_missing`` for a loaded Table row."""
    if getattr(table, "maintenance_missing_metadata_reason", None) != TABLE_MISSING_REASON:
        return False
    until = getattr(table, "maintenance_retry_after", None)
    if until is None:
        return True
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return bool(until > (now or datetime.now(UTC)))


def note_missing_table(
    session: Session,
    table_id: uuid.UUID | str,
    *,
    component: str,
    table_ref: Any = None,
    commit: bool = True,
) -> None:
    """Log, count and hold one missing table until the next scan reconciles it."""
    from cryo.db.repositories.sources import record_missing_metadata
    from cryo.observability.metrics import table_load_skipped_total

    table_load_skipped_total.labels(component=component, reason="table_missing").inc()
    logger.info(
        "%s: skipping %s, the catalog no longer has it; held until the next scan reconciles it",
        component,
        table_ref or table_id,
    )
    record_missing_metadata(
        session,
        table_id if isinstance(table_id, uuid.UUID) else uuid.UUID(str(table_id)),
        reason=TABLE_MISSING_REASON,
    )
    if commit:
        session.commit()
