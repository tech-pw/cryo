"""Bytes freed by cleanup actions the engine cannot size (#538).

Spark's expire_snapshots reports file counts, and remove_orphan_files reports paths, not
sizes. Sizing them on the cluster needs a second listing, which has OOM'd Spark. The
scanner already holds the sizes: the metadata cost pass knows which snapshots reference
each file, and the orphan listing knows each object's size and age. This module turns
the stored scan data into an estimate for the action's own cutoff. It reads no live
``$files`` and does no listing.

Every reclaim audit row with bytes carries ``result_metrics.bytes_source``:

* ``engine``: the engine reported the figure (Trino ``$files`` diff, native engine, or a
  Spark procedure that measured bytes or deleted nothing).
* ``scan_estimate``: priced from the last scan's ledger. Files that died after that scan
  are missing, so this errs low.
* ``scan_confirmed``: the next scan saw dead or orphan bytes drop and replaced the
  estimate with that drop. The original estimate stays in ``bytes_estimate``.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, TableCost

logger = logging.getLogger(__name__)

BYTES_SOURCE_ENGINE = "engine"
BYTES_SOURCE_SCAN_ESTIMATE = "scan_estimate"
BYTES_SOURCE_SCAN_CONFIRMED = "scan_confirmed"
BYTES_SOURCES = (BYTES_SOURCE_ENGINE, BYTES_SOURCE_SCAN_ESTIMATE, BYTES_SOURCE_SCAN_CONFIRMED)

EXPIRE = "expire_snapshots"
ORPHANS = "remove_orphan_files"
_ESTIMATED_ACTIONS = frozenset({EXPIRE, ORPHANS})
_SUCCESS = frozenset({"OK", "succeeded"})

_OLDER_THAN = re.compile(r"older_than\s*=>\s*TIMESTAMP\s*'([^']+)'", re.IGNORECASE)
_RETAIN_LAST = re.compile(r"retain_last\s*=>\s*(\d+)", re.IGNORECASE)
_THRESHOLD = re.compile(r"retention_threshold\s*=>\s*'(\d+)([dh])'", re.IGNORECASE)


def estimate_expire_bytes(
    ledger: Mapping[str, Any] | None, *, older_than: datetime, retain_last: int
) -> int | None:
    """Bytes an expiry with this cutoff frees, from the scanner's expire ledger.

    A file is freed when every snapshot holding it is expired: its newest holder is older
    than ``older_than`` and is not among the ``retain_last`` newest snapshots.
    """
    if not isinstance(ledger, Mapping):
        return None
    timestamps = sorted(int(t) for t in ledger.get("snapshot_ts_ms") or [])
    rows = ledger.get("dead_bytes_by_last_ref_ms")
    if rows is None:
        return None
    cutoff_ms = int(older_than.timestamp() * 1000)
    keep = max(int(retain_last), 1)
    if timestamps:
        if keep >= len(timestamps):
            return 0
        cutoff_ms = min(cutoff_ms, timestamps[-keep])
    return sum(int(size) for last_ref, size in rows if int(last_ref) < cutoff_ms)


def estimate_orphan_bytes(ledger: Mapping[str, Any] | None, *, older_than: datetime) -> int | None:
    """Orphan bytes from the last listing in whole UTC days older than the cutoff."""
    if not isinstance(ledger, Mapping) or ledger.get("bytes_by_day") is None:
        return None
    cutoff = int(older_than.timestamp())
    return sum(int(row[1]) for row in ledger["bytes_by_day"] if int(row[0]) + 86400 <= cutoff)


def cutoff_from_sql(sql: str | None, *, now: datetime) -> tuple[datetime | None, int | None]:
    """(older_than, retain_last) as rendered by either engine's SQL; None when absent."""
    if not sql:
        return None, None
    older_than: datetime | None = None
    if match := _OLDER_THAN.search(sql):
        try:
            older_than = datetime.fromisoformat(match.group(1).strip()).replace(tzinfo=UTC)
        except ValueError:
            older_than = None
    elif match := _THRESHOLD.search(sql):
        amount, unit = int(match.group(1)), match.group(2).lower()
        older_than = now - (timedelta(hours=amount) if unit == "h" else timedelta(days=amount))
    retain = _RETAIN_LAST.search(sql)
    return older_than, int(retain.group(1)) if retain else None


def apply_reclaim_measurement(
    session: Session,
    row: AuditLog,
    *,
    sql: str | None,
    now: datetime | None = None,
    default_retention_days: int = 7,
    default_retain_last: int = 1,
    estimate_orphans: bool | None = None,
) -> None:
    """Label the row's bytes with their source, or fill them from the last scan."""
    if row.status not in _SUCCESS:
        return
    metrics = dict(row.result_metrics or {})
    if row.actual_bytes_freed is not None:
        metrics.setdefault("bytes_source", BYTES_SOURCE_ENGINE)
        row.result_metrics = metrics
        return
    if row.action not in _ESTIMATED_ACTIONS or row.table_id is None:
        return
    if row.action == ORPHANS:
        if estimate_orphans is None:
            from cryo.config import get_settings

            estimate_orphans = get_settings().maintenance_measure_orphan_bytes
        if not estimate_orphans:
            return
    now = now or datetime.now(UTC)
    try:
        estimate = _estimate(
            session,
            row.table_id,
            row.action,
            sql=sql,
            now=now,
            default_retention_days=default_retention_days,
            default_retain_last=default_retain_last,
        )
    except Exception:
        logger.warning("reclaim estimate failed for table %s", row.table_id, exc_info=True)
        return
    if estimate is None:
        return
    bytes_estimate, scan_at = estimate
    row.actual_bytes_freed = bytes_estimate
    metrics["bytes_source"] = BYTES_SOURCE_SCAN_ESTIMATE
    metrics["bytes_estimate"] = bytes_estimate
    if scan_at is not None:
        metrics["bytes_scan_at"] = scan_at
    row.result_metrics = metrics


def _estimate(
    session: Session,
    table_id: uuid.UUID,
    action: str,
    *,
    sql: str | None,
    now: datetime,
    default_retention_days: int,
    default_retain_last: int,
) -> tuple[int, str | None] | None:
    cost = session.get(TableCost, table_id)
    raw = cost.raw if cost is not None and isinstance(cost.raw, dict) else None
    if cost is None or raw is None:
        return None
    older_than, retain_last = cutoff_from_sql(sql, now=now)
    older_than = older_than or now - timedelta(days=default_retention_days)
    if action == EXPIRE:
        metadata = raw.get("metadata")
        ledger = metadata.get("expire_ledger") if isinstance(metadata, dict) else None
        value = estimate_expire_bytes(
            ledger, older_than=older_than, retain_last=retain_last or default_retain_last
        )
        scan_at: datetime | None = cost.scan_at
    else:
        ledger = raw.get("orphan_ledger")
        value = estimate_orphan_bytes(ledger, older_than=older_than)
        scan_at = cost.last_full_cost_scan_at
    if value is None:
        return None
    if scan_at is not None and scan_at.tzinfo is None:
        scan_at = scan_at.replace(tzinfo=UTC)
    return value, scan_at.isoformat() if scan_at is not None else None


def confirm_scan_estimates(
    session: Session,
    *,
    table_id: uuid.UUID,
    previous_scan_at: datetime | None,
    scan_at: datetime,
    dead_before: int | None,
    dead_after: int | None,
    orphan_before: int | None,
    orphan_after: int | None,
    orphan_relisted: bool,
) -> int:
    """Replace a scan estimate with the drop the next scan measured. Returns rows confirmed.

    Only when exactly one estimated row of that action ran between the two scans: with
    more, the drop cannot be split between them. A drop of zero or less (writes outran
    the cleanup) leaves the estimate in place and marks it inconclusive.
    """
    if previous_scan_at is None:
        return 0
    checks = [(EXPIRE, dead_before, dead_after)]
    if orphan_relisted:
        checks.append((ORPHANS, orphan_before, orphan_after))
    confirmed = 0
    for action, before, after in checks:
        if before is None or after is None:
            continue
        rows = [
            row
            for row in session.scalars(
                select(AuditLog).where(
                    AuditLog.table_id == table_id,
                    AuditLog.action == action,
                    AuditLog.ts > previous_scan_at,
                    AuditLog.ts <= scan_at,
                    AuditLog.status.in_(tuple(_SUCCESS)),
                    AuditLog.actual_bytes_freed.isnot(None),
                )
            )
            if (row.result_metrics or {}).get("bytes_source") == BYTES_SOURCE_SCAN_ESTIMATE
        ]
        if len(rows) != 1:
            continue
        row = rows[0]
        metrics = dict(row.result_metrics or {})
        drop = int(before) - int(after)
        if drop <= 0:
            metrics["bytes_confirm"] = "inconclusive"
        else:
            metrics["bytes_source"] = BYTES_SOURCE_SCAN_CONFIRMED
            metrics["bytes_confirmed_at"] = scan_at.isoformat()
            row.actual_bytes_freed = drop
            confirmed += 1
        row.result_metrics = metrics
    return confirmed
