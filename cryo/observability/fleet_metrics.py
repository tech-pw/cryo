"""Publish fleet gauges from SQL aggregates.

Every number here is a COUNT/SUM/AVG. Nothing loads table, job, or audit rows into
the process. The same values are published by each API replica, so dashboards must
aggregate with max(), not sum().
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from cryo.cost.model import GIB
from cryo.db.models import AuditLog, Table, TableCost, TableStats
from cryo.observability import metrics as m
from cryo.reporting.impact_signals import _SUCCESS_STATUSES

logger = logging.getLogger(__name__)

_GRADES = ("A", "B", "C", "D", "E", "F", "unknown")
_SCAN_WINDOWS = (("1d", timedelta(days=1)), ("7d", timedelta(days=7)), ("30d", timedelta(days=30)))
_RECLAIM_COLUMNS = (
    ("expire_snapshots", TableCost.reclaim_expire_bytes),
    ("remove_orphan_files", TableCost.reclaim_orphan_bytes),
    ("optimize", TableCost.reclaim_optimize_bytes),
    ("optimize_manifests", TableCost.reclaim_manifests_bytes),
)
_REFRESH_SECONDS = 60.0
_refresh_lock = threading.Lock()
_last_refresh_monotonic = 0.0


def publish_fleet_metrics(session: Session, *, now: datetime | None = None) -> None:
    current = now or datetime.now(UTC)
    _publish_scan_and_health(session, current)
    _publish_cost(session, current)
    _publish_realized(session, current)


def refresh_fleet_metrics_cached(*, force: bool = False) -> None:
    """Best-effort refresh for /metrics. A database outage must not fail the scrape."""
    global _last_refresh_monotonic
    if not force and time.monotonic() - _last_refresh_monotonic < _REFRESH_SECONDS:
        return
    if not _refresh_lock.acquire(blocking=False):
        return
    try:
        if not force and time.monotonic() - _last_refresh_monotonic < _REFRESH_SECONDS:
            return
        from cryo.db.session import SessionLocal

        with SessionLocal() as session:
            publish_fleet_metrics(session)
        _last_refresh_monotonic = time.monotonic()
    except Exception:
        logger.warning("fleet metrics refresh failed", exc_info=True)
        _last_refresh_monotonic = time.monotonic()
    finally:
        _refresh_lock.release()


def _publish_scan_and_health(session: Session, now: datetime) -> None:
    scanned = [
        func.coalesce(
            func.sum(case((TableStats.scan_at >= now - window, 1), else_=0)),
            0,
        )
        for _label, window in _SCAN_WINDOWS
    ]
    row = session.execute(
        select(
            func.count(Table.id),
            *scanned,
            func.coalesce(func.sum(TableStats.small_files_count), 0),
            func.coalesce(func.sum(TableStats.reclaimable_bytes), 0),
            func.avg(TableStats.overall_score),
            func.coalesce(func.sum(TableStats.total_size_gb), 0.0),
        )
        .select_from(Table)
        .outerjoin(TableStats)
        .where(Table.is_active.is_(True))
    ).one()
    active = int(row[0] or 0)
    for index, (label, _window) in enumerate(_SCAN_WINDOWS):
        scanned_count = int(row[index + 1] or 0)
        m.scan_coverage_ratio.labels(window=label).set(scanned_count / active if active else 0.0)
    m.datalake_small_files.set(int(row[4] or 0))
    m.reclaimable_bytes.labels(operation="total").set(int(row[5] or 0))
    m.datalake_avg_health_score.set(float(row[6] or 0.0))
    m.datalake_total_size_gb.set(float(row[7] or 0.0))

    counts = dict.fromkeys(_GRADES, 0)
    for grade, count in session.execute(
        select(TableStats.grade, func.count(Table.id))
        .select_from(Table)
        .join(TableStats)
        .where(Table.is_active.is_(True))
        .group_by(TableStats.grade)
    ):
        key = grade if grade in counts else "unknown"
        counts[key] += int(count or 0)
    for grade, count in counts.items():
        m.datalake_grade_tables.labels(grade=grade).set(count)


def _publish_cost(session: Session, now: datetime) -> None:
    reclaim_sums = [func.coalesce(func.sum(column), 0) for _name, column in _RECLAIM_COLUMNS]
    reclaim_usd = [
        func.coalesce(func.sum(column * TableCost.price_per_gb_month), 0.0)
        for _name, column in _RECLAIM_COLUMNS
    ]
    row = session.execute(
        select(
            func.coalesce(func.sum(TableCost.current_monthly_cost_usd), 0.0),
            func.coalesce(func.sum(TableCost.projected_savings_usd_month), 0.0),
            func.coalesce(
                func.sum(
                    TableCost.live_bytes
                    + TableCost.dead_bytes
                    + TableCost.metadata_bytes
                    + func.coalesce(TableCost.orphan_bytes, 0)
                ),
                0,
            ),
            func.coalesce(func.sum(TableCost.orphan_bytes), 0),
            func.count(TableCost.last_full_cost_scan_at),
            func.min(TableCost.last_full_cost_scan_at),
            *reclaim_sums,
            *reclaim_usd,
        )
        .select_from(Table)
        .outerjoin(TableCost)
        .where(Table.is_active.is_(True))
    ).one()
    active = int(session.scalar(select(func.count(Table.id)).where(Table.is_active.is_(True))) or 0)
    m.cost_monthly_estimate_usd.labels(source="fleet", cost_type="storage").set(float(row[0] or 0))
    m.cost_savings_projected_usd.labels(source="fleet").set(float(row[1] or 0))
    m.cost_storage_bytes_total.labels(source="fleet").set(int(row[2] or 0))
    m.orphan_bytes_total.set(int(row[3] or 0))
    scanned = int(row[4] or 0)
    m.orphan_scan_coverage_ratio.set(scanned / active if active else 0.0)
    oldest = row[5]
    if oldest is None:
        m.orphan_scan_oldest_age_seconds.set(0)
    else:
        if oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=UTC)
        m.orphan_scan_oldest_age_seconds.set(max((now - oldest).total_seconds(), 0.0))

    offset = 6
    for index, (name, _column) in enumerate(_RECLAIM_COLUMNS):
        bytes_value = int(row[offset + index] or 0)
        usd = float(row[offset + len(_RECLAIM_COLUMNS) + index] or 0) / GIB
        m.reclaimable_bytes.labels(operation=name).set(bytes_value)
        m.projected_savings_usd.labels(operation=name).set(usd)


# Ratio uses only the operations both sides can represent. expire_snapshots frees space
# only after orphan removal, and convert/rewrite deletes have no projected-byte column.
_RATIO_ACTIONS = ("optimize", "remove_orphan_files")


def _publish_realized(session: Session, now: datetime) -> None:
    window_start = now - timedelta(days=30)
    realized = int(
        session.scalar(
            select(func.coalesce(func.sum(AuditLog.actual_bytes_freed), 0)).where(
                AuditLog.ts >= window_start,
                AuditLog.ts < now,
                AuditLog.action.in_(_RATIO_ACTIONS),
                AuditLog.status.in_(_SUCCESS_STATUSES),
            )
        )
        or 0
    )
    projected = int(
        session.scalar(
            select(
                func.coalesce(
                    func.sum(TableCost.reclaim_orphan_bytes + TableCost.reclaim_optimize_bytes),
                    0,
                )
            )
            .select_from(Table)
            .outerjoin(TableCost)
            .where(Table.is_active.is_(True))
        )
        or 0
    )
    m.realized_bytes_freed.labels(window="30d").set(realized)
    m.realized_vs_projected_ratio.set(realized / projected if projected else 0.0)



