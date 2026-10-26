"""Tombstone registry rows for tables that are gone from the catalog (#120).

Runs once per scan (chunk 0), against the full discovery listing, before chunking. Safety
rules, in order:

* Only sources that discovery listed are considered. A source whose discovery failed has no
  candidates and is skipped.
* Out-of-allowlist schemas are never considered (``deactivate_out_of_scope_tables`` owns
  them). A schema-scoped scan only looks at its schemas; table-scoped scans don't reconcile.
* A registry row only counts as missing after its schema is listed again, explicitly, and the
  table is still absent. If that confirmation call fails, the schema is skipped. If it lists
  zero tables, the schema is skipped unless the namespace itself is confirmed gone.
* Mass-removal guard per source: above ``scan_missing_table_max_count`` removals, or above
  ``scan_missing_table_max_fraction`` of the source's registered tables, nothing is removed
  and the abort is audited.

Removal is a tombstone: ``removed_from_catalog_at`` / ``removed_reason`` set, ``is_active``
false, queued and deferred jobs cancelled (running jobs finish), one audit row per table.
A tombstoned table that is listed again is restored. Nothing is hard-deleted.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.config import Settings, get_settings
from cryo.db.models import AuditLog, MaintenanceJob, Source, Table
from cryo.db.repositories.sources import source_allowed_schemas
from cryo.domain.enums import JobStatus

logger = logging.getLogger(__name__)

REMOVED_JOB_ERROR = "table removed from catalog"
_CANCELLABLE = (JobStatus.QUEUED.value, JobStatus.DEFERRED.value)


@dataclass
class ReconcileResult:
    removed: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class _SourceListing:
    source: Source
    catalog: Any
    names_by_schema: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))


def _norm(value: str) -> str:
    return (value or "").strip().lower()


def _is_no_such_namespace(exc: BaseException) -> bool:
    return any(
        "nosuchnamespace" in cls.__name__.lower() or "nosuchobject" in cls.__name__.lower()
        for cls in type(exc).__mro__
    )


def reconcile_missing_tables(
    session: Session,
    candidates: Iterable[Any],
    *,
    scan_at: datetime,
    settings: Settings | None = None,
    schema_names: Collection[str] | None = None,
    bounded: Callable[[Callable[[], Any], str], Any] | None = None,
) -> ReconcileResult:
    """Tombstone missing tables / restore re-listed ones for every listed source."""
    settings = settings or get_settings()
    result = ReconcileResult()
    if not settings.scan_remove_missing_tables_enabled:
        return result
    call = bounded or (lambda fn, _description: fn())

    listings: dict[uuid.UUID, _SourceListing] = {}
    for candidate in candidates:
        source = candidate.source
        listing = listings.get(source.id)
        if listing is None:
            listing = listings[source.id] = _SourceListing(source=source, catalog=candidate.catalog)
        listing.names_by_schema[_norm(candidate.schema_name)].add(_norm(candidate.table_name))

    requested = {_norm(name) for name in schema_names} if schema_names else None
    for listing in listings.values():
        try:
            _reconcile_source(session, listing, requested, scan_at, settings, call, result)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("Missing-table reconcile failed for source %s", listing.source.name)
            result.skipped.append(f"{listing.source.name}: reconcile_error")
    return result


def _reconcile_source(
    session: Session,
    listing: _SourceListing,
    requested: set[str] | None,
    scan_at: datetime,
    settings: Settings,
    call: Callable[[Callable[[], Any], str], Any],
    result: ReconcileResult,
) -> None:
    source = listing.source
    allowed = source_allowed_schemas(source)
    allowed_norm = {_norm(name) for name in allowed} if allowed is not None else None

    def in_scope(schema: str) -> bool:
        key = _norm(schema)
        if allowed_norm is not None and key not in allowed_norm:
            return False
        return requested is None or key in requested

    rows = [
        row
        for row in session.scalars(select(Table).where(Table.source_id == source.id))
        if in_scope(row.schema_name)
    ]
    live = [row for row in rows if row.removed_from_catalog_at is None]
    tombstoned = [row for row in rows if row.removed_from_catalog_at is not None]

    for row in tombstoned:
        if _norm(row.table_name) in listing.names_by_schema.get(_norm(row.schema_name), ()):
            _restore(session, source, row, result)

    suspects: dict[str, list[Table]] = defaultdict(list)
    for row in live:
        if _norm(row.table_name) not in listing.names_by_schema.get(_norm(row.schema_name), ()):
            suspects[row.schema_name].append(row)
    if not suspects:
        return

    namespaces: set[str] | None = None
    confirmed: list[tuple[Table, str]] = []
    for schema, schema_rows in sorted(suspects.items()):
        try:
            names = call(
                partial(listing.catalog.list_tables_in_namespace, schema),
                f"confirm tables({source.name}/{schema})",
            )
        except Exception as exc:
            if _is_no_such_namespace(exc):
                confirmed.extend((row, schema) for row in schema_rows)
                continue
            logger.warning("Not reconciling %s/%s: re-listing failed: %s", source.name, schema, exc)
            result.skipped.append(f"{source.name}/{schema}: namespace_list_failed")
            continue
        listed = {_norm(name) for name in names}
        if not listed:
            if namespaces is None:
                namespaces = _list_namespaces(listing.catalog, source, call)
            if namespaces is None or "." in schema or _norm(schema) in namespaces:
                result.skipped.append(f"{source.name}/{schema}: schema_listed_empty")
                continue
        confirmed.extend(
            (row, schema) for row in schema_rows if _norm(row.table_name) not in listed
        )
    if not confirmed:
        return

    registered = len(live)
    count = len(confirmed)
    over_count = count > settings.scan_missing_table_max_count
    over_fraction = (
        count > settings.scan_missing_table_guard_min_count
        and registered > 0
        and count / registered > settings.scan_missing_table_max_fraction
    )
    if over_count or over_fraction:
        reason = (
            f"guard: {count} of {registered} registered tables missing "
            f"(max {settings.scan_missing_table_max_count} or "
            f"{settings.scan_missing_table_max_fraction:.0%})"
        )
        logger.error("Not removing missing tables for source %s: %s", source.name, reason)
        result.skipped.append(f"{source.name}: {reason}")
        session.add(
            AuditLog(
                action="missing_table_reconcile_aborted",
                status="SKIPPED",
                engine="cryo",
                actor="scanner",
                detail=f"source={source.name} {reason}",
            )
        )
        _count_abort(source.name, "guard")
        return

    removed_ids = [row.id for row, _ in confirmed]
    for row, schema in confirmed:
        row.removed_from_catalog_at = scan_at
        row.removed_reason = f"not_found_in_catalog: {source.name}/{schema}"
        row.is_active = False
        session.add(
            AuditLog(
                table_id=row.id,
                action="table_removed_from_catalog",
                status="OK",
                engine="cryo",
                actor="scanner",
                detail=(
                    f"table={source.name}.{row.schema_name}.{row.table_name} {row.removed_reason}"
                ),
            )
        )
        result.removed.append(f"{source.name}.{row.schema_name}.{row.table_name}")
    _cancel_pending_jobs(session, removed_ids)
    _count_removed(source.name, len(confirmed))


def _restore(session: Session, source: Source, row: Table, result: ReconcileResult) -> None:
    row.removed_from_catalog_at = None
    row.removed_reason = None
    row.consecutive_scan_failures = 0
    session.add(
        AuditLog(
            table_id=row.id,
            action="table_restored_to_catalog",
            status="OK",
            engine="cryo",
            actor="scanner",
            detail=f"table={source.name}.{row.schema_name}.{row.table_name}",
        )
    )
    result.restored.append(f"{source.name}.{row.schema_name}.{row.table_name}")


def _list_namespaces(
    catalog: Any, source: Source, call: Callable[[Callable[[], Any], str], Any]
) -> set[str] | None:
    try:
        return {_norm(name) for name in call(catalog.list_namespaces, f"namespaces({source.name})")}
    except Exception as exc:
        logger.warning("Could not list namespaces for %s: %s", source.name, exc)
        return None


def _cancel_pending_jobs(session: Session, table_ids: list[uuid.UUID]) -> None:
    if not table_ids:
        return
    jobs = list(
        session.scalars(
            select(MaintenanceJob).where(
                MaintenanceJob.table_id.in_(table_ids), MaintenanceJob.status.in_(_CANCELLABLE)
            )
        )
    )
    queued: list[tuple[str, str]] = []
    for job in jobs:
        if job.status == JobStatus.QUEUED.value:
            queued.append((str(job.id), str(job.destination_id)))
        job.status = JobStatus.SKIPPED.value
        job.error = REMOVED_JOB_ERROR
    session.flush()
    if not queued:
        return
    try:
        from cryo.infra.wiring import build_runtime_wiring

        wiring = build_runtime_wiring()
        for job_id, destination_id in queued:
            wiring.queue.cancel_pending(job_id, destination_id)
    except Exception:
        logger.warning("Could not drop queued jobs of removed tables from Redis", exc_info=True)


def _count_removed(source_name: str, count: int) -> None:
    try:
        from cryo.observability.metrics import tables_removed_from_catalog_total

        tables_removed_from_catalog_total.labels(source=source_name).inc(count)
    except Exception:  # pragma: no cover - metrics must never break a scan
        pass


def _count_abort(source_name: str, reason: str) -> None:
    try:
        from cryo.observability.metrics import missing_table_reconcile_aborted_total

        missing_table_reconcile_aborted_total.labels(source=source_name, reason=reason).inc()
    except Exception:  # pragma: no cover
        pass
