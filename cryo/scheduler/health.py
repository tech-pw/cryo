"""Scheduler-side health checks for silent data loss (arXiv:2604.20081).

Wires :func:`cryo.scanner.silent_data_loss.detect_silent_data_loss` into the scheduler's
periodic loop. The scheduler is leader-elected (multiple replicas may be running) and is
already Redis-coordinated via :class:`cryo.queue.locks.LockStore`; this module relies on
the same pattern -- a single ``silent_loss`` lock guards the periodic detector so only one
replica runs it at a time.

The detector itself is intentionally simple: it reads the ``cryo.tables`` rows that have
fresh :class:`TableStats` and walks ``$CRYO_SILENT_LOSS_LOCAL_ROOT/<table_uuid>/data/``
against the committed manifest list. A real production deploy would swap
:func:`list_local_storage_files` for an S3 lister.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import SilentDataLossEvent, Table, TableStats
from cryo.scanner.silent_data_loss import (
    CommittedManifestEntry,
    OrphanReport,
    StorageFile,
    commit_orphan_event,
    detect_silent_data_loss,
    list_local_storage_files,
)

logger = logging.getLogger(__name__)


@dataclass
class SilentLossHealthResult:
    """Result of one silent-data-loss health-check sweep."""

    table_id: uuid.UUID
    report: OrphanReport
    event_id: uuid.UUID | None


def check_silent_data_loss(
    session: Session,
    table_id: uuid.UUID,
    *,
    storage_files: Sequence[StorageFile] | None = None,
    committed_entries: Sequence[CommittedManifestEntry] | None = None,
) -> OrphanReport | None:
    """Run the detector for one table and persist the result.

    Returns the :class:`OrphanReport` (or ``None`` if the table has no fresh stats to
    compare against). The function is the synchronous core used by both the periodic
    scheduler hook and the manual API endpoint.
    """
    table = session.get(Table, table_id)
    if table is None:
        return None
    stats = session.get(TableStats, table_id)
    if stats is None:
        return None

    committed = (
        list(committed_entries) if committed_entries is not None else _committed_from_stats(stats)
    )
    storage = (
        list(storage_files)
        if storage_files is not None
        else list_local_storage_files(str(table_id))
    )

    report = detect_silent_data_loss(
        str(table_id),
        storage,
        committed,
        snapshot_id=_snapshot_id_from_stats(stats),
    )

    if not report.is_clean():
        row = commit_orphan_event(session, report)
        if row is not None:
            session.flush()
            logger.info(
                "silent_data_loss: table=%s orphans=%d missing=%d root_cause=%s",
                table_id,
                len(report.orphan_data_files),
                len(report.missing_files),
                report.root_cause,
            )
    return report


def run_silent_data_loss_sweep(
    session: Session,
    *,
    after_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> tuple[list[SilentLossHealthResult], uuid.UUID | None]:
    """Run the detector for a bounded slice of active tables, ordered by id, resuming after
    ``after_id``; intended for the 5-minute scheduler loop.

    Sweeping the whole ~13k-table fleet unbounded in one session accumulates every
    Table/TableStats ORM object in the session's identity map simultaneously -- a real
    production incident: cryo-scheduler OOMKilled roughly every 5 minutes. ``limit`` bounds
    how many tables one call processes; the caller tracks ``after_id`` (the returned cursor)
    across ticks to rotate through the full fleet over several ticks instead of all at once.
    Returns ``(results, next_cursor)`` -- ``next_cursor`` is ``None`` once the sweep reaches
    the end of the fleet, signaling the caller to wrap back to the start.

    The sweep is best-effort: a failure on one table must not abort the rest. Each result
    is returned so the caller can build a summary or push to Slack/PagerDuty.
    """
    settings = get_settings()
    batch_size = limit if limit is not None else settings.silent_loss_batch_size
    stmt = select(Table.id).where(Table.is_active.is_(True)).order_by(Table.id)
    if after_id is not None:
        stmt = stmt.where(Table.id > after_id)
    table_ids = session.scalars(stmt.limit(batch_size)).all()
    next_cursor: uuid.UUID | None = table_ids[-1] if len(table_ids) == batch_size else None

    results: list[SilentLossHealthResult] = []
    for tid in table_ids:
        try:
            report = check_silent_data_loss(session, tid)
            if report is not None and not report.is_clean():
                event_id: uuid.UUID | None = None
                latest = session.scalar(
                    select(SilentDataLossEvent)
                    .where(SilentDataLossEvent.table_id == tid)
                    .order_by(SilentDataLossEvent.detected_at.desc())
                    .limit(1)
                )
                if latest is not None:
                    event_id = latest.id
                results.append(
                    SilentLossHealthResult(table_id=tid, report=report, event_id=event_id)
                )
        except Exception as exc:  # pragma: no cover
            # defensive: one bad table must not abort sweep.
            logger.warning("silent_data_loss: table=%s failed: %s", tid, exc)
        finally:
            # Detach this table's objects (and anything it pulled in) before moving on --
            # without this, the identity map keeps every processed table's ORM objects
            # alive for the rest of the sweep regardless of batch size.
            session.expunge_all()
    return results, next_cursor


def _committed_from_stats(stats: TableStats) -> list[CommittedManifestEntry]:
    """Best-effort committed-manifest view derived from a ``TableStats`` row.

    The ``raw`` JSONB blob carries the per-file inventory that the scanner already
    observed on its last pass; we treat those paths as "what the table claims it has".
    A production deploy would read the current snapshot's manifest list directly from the
    catalog -- this fallback is good enough to drive the detector end-to-end in local dev.
    """
    raw = stats.raw or {}
    file_list = raw.get("committed_files") or []
    if not isinstance(file_list, list):
        return []
    entries: list[CommittedManifestEntry] = []
    for item in file_list:
        if isinstance(item, str):
            entries.append(CommittedManifestEntry(path=item))
        elif isinstance(item, dict) and "path" in item:
            try:
                size = int(item.get("size_bytes") or 0)
            except (TypeError, ValueError):
                size = 0
            entries.append(CommittedManifestEntry(path=str(item["path"]), size_bytes=size))
    return entries


def _snapshot_id_from_stats(stats: TableStats) -> int | None:
    raw = stats.raw or {}
    snapshot_id = raw.get("current_snapshot_id")
    if snapshot_id is None:
        return None
    try:
        return int(snapshot_id)
    except (TypeError, ValueError):
        return None


__all__ = [
    "SilentLossHealthResult",
    "check_silent_data_loss",
    "run_silent_data_loss_sweep",
]
