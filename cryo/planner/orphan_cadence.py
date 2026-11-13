"""Per-table cadence for remove_orphan_files under the deep default preset.

Orphan removal lists the whole table root, so running it on every table every night costs a
full storage LIST of the fleet each night. Instead a table is cleaned at most once every
``cadence_days``, and tables are staggered by a stable hash of their id so each night
handles about 1/N of the fleet. A table with a large known orphan backlog skips the stagger
wait once, so the initial catch-up starts with the biggest backlogs (the scheduler already
ranks tables by reclaimable bytes, which include orphan bytes, within the run's table cap).

Dates are compared at UTC day granularity: a nightly cron fires at roughly the same time
each day, and the audit row is written when the step ends, so a seconds-level comparison
would push every table one extra day.

Pure module: no I/O, no settings. The caller supplies the last success and the thresholds.
"""

from __future__ import annotations

import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

DEFAULT_ORPHAN_CADENCE_DAYS = 7
DEFAULT_ORPHAN_CATCHUP_MIN_BYTES = 10 * 1024**3
DEFAULT_ORPHAN_MAX_LISTING_KEYS = 5_000_000
# Matches the health checklist's manifest_bloat "yellow" threshold.
DEFAULT_MIN_MANIFESTS = 100
# Files younger than this may belong to an in-flight write; deleting them corrupts the table.
ORPHAN_MIN_RETENTION_DAYS = 7


@dataclass(frozen=True)
class OrphanCadenceDecision:
    run: bool
    next_due: date | None
    note: str | None = None


def stagger_slot(table_id: str, cadence_days: int) -> int:
    """Stable slot in [0, cadence_days). crc32, not hash(): hash() is salted per process."""
    if cadence_days <= 1:
        return 0
    return zlib.crc32(table_id.encode("utf-8")) % cadence_days


def _epoch_day(day: date) -> int:
    return (day - date(1970, 1, 1)).days


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def next_orphan_cleanup_due(
    table_id: str,
    last_success_at: datetime | str | None,
    now: datetime,
    cadence_days: int = DEFAULT_ORPHAN_CADENCE_DAYS,
) -> date:
    """First UTC day on or after today that is both past the cadence and on the table's slot."""
    today = _as_utc(now) or now
    earliest = today.date()
    last = _as_utc(last_success_at)
    if cadence_days <= 0:
        return earliest
    if last is not None:
        earliest = max(earliest, last.date() + timedelta(days=cadence_days))
    offset = (stagger_slot(table_id, cadence_days) - _epoch_day(earliest)) % cadence_days
    return earliest + timedelta(days=offset)


def decide_orphan_cadence(
    *,
    table_id: str,
    last_success_at: datetime | str | None,
    now: datetime,
    cadence_days: int = DEFAULT_ORPHAN_CADENCE_DAYS,
    orphan_backlog_bytes: int | None = None,
    catchup_min_bytes: int = DEFAULT_ORPHAN_CATCHUP_MIN_BYTES,
) -> OrphanCadenceDecision:
    if cadence_days <= 0:
        return OrphanCadenceDecision(run=True, next_due=None)
    today = (_as_utc(now) or now).date()
    last = _as_utc(last_success_at)
    due = next_orphan_cleanup_due(table_id, last, now, cadence_days)
    if last is not None and today < last.date() + timedelta(days=cadence_days):
        return OrphanCadenceDecision(
            run=False,
            next_due=due,
            note=(
                f"Skipped remove_orphan_files: last succeeded {last.date().isoformat()}, "
                f"cadence is every {cadence_days} days. Next orphan cleanup due "
                f"{due.isoformat()}."
            ),
        )
    if due == today:
        return OrphanCadenceDecision(run=True, next_due=today)
    if last is None and (orphan_backlog_bytes or 0) >= catchup_min_bytes > 0:
        return OrphanCadenceDecision(
            run=True,
            next_due=today,
            note=(
                f"remove_orphan_files planned ahead of its stagger slot: never cleaned and "
                f"the last storage listing found {orphan_backlog_bytes:,} orphan bytes."
            ),
        )
    return OrphanCadenceDecision(
        run=False,
        next_due=due,
        note=(
            f"Skipped remove_orphan_files: staggered to spread the fleet's orphan listings "
            f"over {cadence_days} nights. Next orphan cleanup due {due.isoformat()}."
        ),
    )


def orphan_backlog_bytes(stats: Mapping[str, Any]) -> int | None:
    for key in ("orphan_bytes", "reclaim_orphan_bytes"):
        value = stats.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    return None


def estimated_listing_keys(stats: Mapping[str, Any]) -> int | None:
    """Referenced files plus orphans estimated at the table's average file size."""
    total_files = stats.get("total_files")
    if not isinstance(total_files, int) or isinstance(total_files, bool) or total_files <= 0:
        return None
    keys = total_files
    orphan = orphan_backlog_bytes(stats)
    size_gb = stats.get("total_size_gb")
    if orphan and isinstance(size_gb, (int, float)) and size_gb > 0:
        avg_file_bytes = max(1.0, float(size_gb) * 1024**3 / total_files)
        keys += int(orphan / avg_file_bytes)
    return keys
