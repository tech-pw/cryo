"""Fleet-wide scan outcome split for the daily digest: passed, corrupt, failed, not yet scanned.

Counts every table row on an enabled source, active or not, because the scheduled scan walks
the whole catalog. Deactivated dead entries are exactly the ones that used to fail every run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import Source, Table, TableStats
from cryo.scanner.failure_classifier import scan_failure_is_permanent
from cryo.scanner.persistence import scan_retry_interval

RETRY_TIER_LABELS: dict[timedelta, str] = {
    timedelta(days=1): "daily",
    timedelta(days=7): "weekly",
    timedelta(days=15): "every 15 days",
    timedelta(days=30): "monthly",
}


@dataclass(frozen=True)
class ScanHealth:
    total: int
    passed: int
    # Metadata or manifest gone from S3; a rescan returns the same error until someone
    # repairs or drops the table.
    corrupt: int
    # Anything else: permission denied, timeouts, engine errors. Fixable from our side.
    failed: int
    never_scanned: int
    # Failing tables per retry cadence, in ladder order.
    retry_tiers: dict[str, int] = field(default_factory=dict)


def collect_scan_health(session: Session) -> ScanHealth:
    on_enabled_source = Table.source_id.in_(select(Source.id).where(Source.enabled.is_(True)))
    has_stats = select(TableStats.table_id).where(TableStats.table_id == Table.id).exists()

    healthy_rows = session.execute(
        select(has_stats, func.count(Table.id))
        .where(on_enabled_source, Table.consecutive_scan_failures == 0)
        .group_by(has_stats)
    ).all()
    passed = sum(int(count) for scanned, count in healthy_rows if scanned)
    never_scanned = sum(int(count) for scanned, count in healthy_rows if not scanned)

    corrupt = failed = 0
    tiers = dict.fromkeys(RETRY_TIER_LABELS.values(), 0)
    for streak, error in session.execute(
        select(Table.consecutive_scan_failures, Table.last_scan_error).where(
            on_enabled_source, Table.consecutive_scan_failures > 0
        )
    ):
        if scan_failure_is_permanent(error):
            corrupt += 1
        else:
            failed += 1
        interval = scan_retry_interval(int(streak))
        label = RETRY_TIER_LABELS.get(interval) if interval is not None else None
        if label is not None:
            tiers[label] += 1

    return ScanHealth(
        total=passed + never_scanned + corrupt + failed,
        passed=passed,
        corrupt=corrupt,
        failed=failed,
        never_scanned=never_scanned,
        retry_tiers=tiers,
    )
