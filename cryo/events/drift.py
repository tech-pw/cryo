"""Per-table drift since the last successful maintenance (#77).

The counters come from snapshot summaries, which give per-commit totals but not per-file
sizes, so ``small_files_added`` is an estimate: every data file of a commit whose average
added file is under the scanner's small-file size counts as small. The estimated score
re-grades the scanned stats with the commit deltas applied, against the scanned score taken
at the first drift commit (``baseline_score``). A scan landing mid-drift already contains
some of the counted files, so the estimate errs towards more drift, never less. It is a
projection the next scan replaces, never a measurement.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from cryo.db.models import TableDrift, TableStats
from cryo.events.watcher import TableCommitted
from cryo.scanner.health import compute_health_score

logger = logging.getLogger(__name__)

_MB = 1024 * 1024


@dataclass(frozen=True)
class DriftReset:
    table_id: uuid.UUID
    first_commit_at: datetime | None
    commits: int


def small_files_in_commit(files_added: int, bytes_added: int, *, small_file_bytes: int) -> int:
    if files_added <= 0 or small_file_bytes <= 0:
        return 0
    return files_added if bytes_added / files_added < small_file_bytes else 0


def writer_commit_count(event: TableCommitted) -> int:
    """Commits in the event that a writer made, as opposed to maintenance."""
    if event.snapshots_added == 0:
        # Watermark-only event: the probe saw a new snapshot but not what it contained.
        return 1
    return max(0, event.snapshots_added - event.maintenance_commits)


def accumulate_drift(
    session: Session,
    *,
    table_id: uuid.UUID,
    stats: TableStats,
    event: TableCommitted,
    small_file_bytes: int,
    now: datetime | None = None,
) -> TableDrift:
    """Add one observed commit batch to the table's drift row (created on first commit)."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    drift = session.get(TableDrift, table_id)
    if drift is None:
        drift = TableDrift(
            table_id=table_id,
            commits=0,
            data_files_added=0,
            small_files_added=0,
            delete_files_added=0,
            bytes_added=0,
            maintenance_commits=0,
            baseline_score=stats.overall_score,
        )
        session.add(drift)
    writer_commits = writer_commit_count(event)
    drift.maintenance_commits = int(drift.maintenance_commits or 0) + event.maintenance_commits
    drift.last_snapshot_id = event.snapshot_id
    if writer_commits == 0:
        return drift
    committed_at = (event.committed_at or event.observed_at or current).astimezone(UTC)
    drift.commits = int(drift.commits or 0) + writer_commits
    drift.data_files_added = int(drift.data_files_added or 0) + event.files_added
    drift.small_files_added = int(drift.small_files_added or 0) + small_files_in_commit(
        event.files_added, event.bytes_added, small_file_bytes=small_file_bytes
    )
    drift.delete_files_added = int(drift.delete_files_added or 0) + event.delete_files_added
    drift.bytes_added = int(drift.bytes_added or 0) + event.bytes_added
    if drift.first_commit_at is None:
        drift.first_commit_at = committed_at
    drift.last_commit_at = committed_at
    if drift.baseline_score is None:
        drift.baseline_score = stats.overall_score
    drift.estimated_score = estimate_score(stats, int(drift.small_files_added), now=current)
    return drift


def estimate_score(stats: TableStats, small_files_added: int, *, now: datetime) -> int | None:
    """Re-grade the scanned stats with the commit deltas already applied to them."""
    try:
        projected: dict[str, Any] = dict(stats.raw or {})
        for column in TableStats.__table__.columns.keys():
            if column != "raw":
                projected[column] = getattr(stats, column, None)
        small_files = int(stats.small_files_count or 0) + small_files_added
        score, _grade, _deductions = compute_health_score(projected, small_files, now=now)
    except Exception as exc:
        logger.debug("Drift score estimate failed for %s: %s", stats.table_id, exc)
        return None
    return int(score)


def reset_drift(
    session: Session,
    table_id: uuid.UUID,
    *,
    at: datetime | None = None,
) -> DriftReset | None:
    """Zero the table's drift after a successful maintenance job.

    Returns what was reset, or None when the table had no drift row.
    """
    drift = session.get(TableDrift, table_id)
    if drift is None:
        return None
    reset = DriftReset(
        table_id=table_id, first_commit_at=drift.first_commit_at, commits=int(drift.commits or 0)
    )
    drift.commits = 0
    drift.data_files_added = 0
    drift.small_files_added = 0
    drift.delete_files_added = 0
    drift.bytes_added = 0
    drift.maintenance_commits = 0
    drift.first_commit_at = None
    drift.last_commit_at = None
    drift.estimated_score = None
    drift.baseline_score = None
    drift.reset_at = (at or datetime.now(UTC)).astimezone(UTC)
    return reset


def small_file_bytes_from_settings() -> int:
    from cryo.config import get_settings

    return int(get_settings().scan_small_file_mb * _MB)
