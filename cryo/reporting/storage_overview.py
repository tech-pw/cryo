"""Fleet storage overview for the home page: where the lake's bytes are, what Cryo freed in
the last 24 hours, and how storage moved over the last 60 days.

Every query here is a single bounded aggregate:

- the breakdown sums ``table_cost`` (one row per table) joined to active ``tables``;
- the 24-hour window filters ``audit_log`` on ``ts`` (``ix_audit_log_ts``);
- the trend and cumulative figures read ``fleet_impact_history`` (one row per day,
  unique on ``report_date``).

Measured and estimated numbers are kept apart. Bytes freed come from ``actual_bytes_freed``
on audit rows (measured); the breakdown comes from scanner cost accounting (estimated, with
``listed_bytes`` marking tables whose storage was confirmed by an S3 listing).

Two inputs are feature-detected so this module works before and after they land:
``AuditLog.bytes_freed_source`` (engine / scan_estimate / listing_confirmed) and a
``TableCost.raw["bucket_versioning"]`` object. Neither is required.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, FleetImpactHistory, Table, TableCost
from cryo.reporting.impact_signals import realized_reclaim_conditions

logger = logging.getLogger(__name__)

TREND_DAYS = 60
TIB = 1024**4
GIB = 1024**3
# Bucket-versioning findings are capped; the home chip only needs to name a few buckets.
_MAX_VERSIONING_FINDINGS = 20
_MAX_VERSIONING_ROWS = 20_000


@dataclass(frozen=True)
class StorageBreakdown:
    """Fleet-wide bytes by category, from scanner cost accounting (estimates).

    ``retained_history_bytes`` is only knowable where an S3 listing ran: it is listed bytes
    that are neither live, dead, orphaned nor metadata, i.e. files older snapshots still
    reference inside the retention window.
    """

    live_bytes: int
    retained_history_bytes: int
    dead_bytes: int
    orphan_bytes: int
    metadata_bytes: int
    compaction_savings_bytes: int
    tables_total: int
    tables_with_cost: int
    tables_with_listing: int
    price_per_gb_month: float | None

    @property
    def total_bytes(self) -> int:
        return (
            self.live_bytes
            + self.retained_history_bytes
            + self.dead_bytes
            + self.orphan_bytes
            + self.metadata_bytes
        )

    def to_raw(self) -> dict[str, Any]:
        return {**asdict(self), "total_bytes": self.total_bytes}


@dataclass(frozen=True)
class Freed24h:
    bytes_freed: int
    actions_succeeded: int
    actions_measured: int
    tables_cleaned: int
    window_start: datetime
    window_end: datetime
    # Present only when AuditLog carries bytes_freed_source.
    bytes_by_source: dict[str, int] | None = None


@dataclass(frozen=True)
class CumulativeFreed:
    bytes_freed: int
    since: date | None
    days_recorded: int
    days_unmeasured: int


@dataclass(frozen=True)
class TrendPoint:
    day: date
    total_bytes: int | None
    live_bytes: int | None
    bytes_freed: int | None
    cumulative_freed_bytes: int
    measurement_state: str


@dataclass(frozen=True)
class VersioningFinding:
    bucket: str
    tables: int


@dataclass(frozen=True)
class StorageOverview:
    generated_at: datetime
    breakdown: StorageBreakdown
    freed_24h: Freed24h
    cumulative: CumulativeFreed
    trend: list[TrendPoint] = field(default_factory=list)
    # None means the scanner does not report versioning yet (field absent), not "no problem".
    versioning_findings: list[VersioningFinding] | None = None


def _int(value: object) -> int:
    # Postgres returns Decimal for SUM(bigint).
    return int(value) if isinstance(value, (int, float, Decimal)) else 0


def fleet_storage_breakdown(session: Session) -> StorageBreakdown:
    listed = TableCost.listed_bytes.isnot(None)
    accounted = (
        TableCost.live_bytes
        + TableCost.dead_bytes
        + TableCost.metadata_bytes
        + func.coalesce(TableCost.orphan_bytes, 0)
    )
    retained = case(
        (listed & (TableCost.listed_bytes > accounted), TableCost.listed_bytes - accounted),
        else_=0,
    )
    row = session.execute(
        select(
            func.count(Table.id),
            func.count(TableCost.table_id),
            func.coalesce(func.sum(case((listed, 1), else_=0)), 0),
            func.coalesce(func.sum(TableCost.live_bytes), 0),
            func.coalesce(func.sum(retained), 0),
            func.coalesce(func.sum(TableCost.dead_bytes), 0),
            func.coalesce(func.sum(case((listed, TableCost.orphan_bytes), else_=0)), 0),
            func.coalesce(func.sum(TableCost.metadata_bytes), 0),
            func.coalesce(func.sum(TableCost.reclaim_optimize_bytes), 0),
            func.avg(TableCost.price_per_gb_month),
        )
        .select_from(Table)
        .outerjoin(TableCost, TableCost.table_id == Table.id)
        .where(Table.is_active.is_(True))
    ).one()
    price = row[9]
    return StorageBreakdown(
        tables_total=_int(row[0]),
        tables_with_cost=_int(row[1]),
        tables_with_listing=_int(row[2]),
        live_bytes=_int(row[3]),
        retained_history_bytes=_int(row[4]),
        dead_bytes=_int(row[5]),
        orphan_bytes=_int(row[6]),
        metadata_bytes=_int(row[7]),
        compaction_savings_bytes=_int(row[8]),
        price_per_gb_month=float(price) if price is not None else None,
    )


def freed_last_24h(session: Session, *, now: datetime) -> Freed24h:
    start = now - timedelta(hours=24)
    window = (AuditLog.ts >= start, AuditLog.ts < now, *realized_reclaim_conditions())
    row = session.execute(
        select(
            func.count(AuditLog.id),
            func.count(AuditLog.actual_bytes_freed),
            func.coalesce(func.sum(AuditLog.actual_bytes_freed), 0),
            func.count(func.distinct(AuditLog.table_id)),
        ).where(*window)
    ).one()
    by_source: dict[str, int] | None = None
    source_column = getattr(AuditLog, "bytes_freed_source", None)
    if source_column is not None:
        by_source = {}
        for source, freed in session.execute(
            select(source_column, func.sum(AuditLog.actual_bytes_freed))
            .where(*window, AuditLog.actual_bytes_freed.isnot(None))
            .group_by(source_column)
        ).all():
            by_source[str(source or "unknown")] = _int(freed)
    return Freed24h(
        actions_succeeded=_int(row[0]),
        actions_measured=_int(row[1]),
        bytes_freed=_int(row[2]),
        tables_cleaned=_int(row[3]),
        window_start=start,
        window_end=now,
        bytes_by_source=by_source,
    )


def cumulative_freed(session: Session, *, before: date | None = None) -> CumulativeFreed:
    conditions = [] if before is None else [FleetImpactHistory.report_date < before]
    measured = FleetImpactHistory.measurement_state != "unmeasured"
    row = session.execute(
        select(
            func.coalesce(func.sum(case((measured, FleetImpactHistory.bytes_freed), else_=0)), 0),
            func.min(FleetImpactHistory.report_date),
            func.count(FleetImpactHistory.id),
            func.coalesce(func.sum(case((measured, 0), else_=1)), 0),
        ).where(*conditions)
    ).one()
    return CumulativeFreed(
        bytes_freed=_int(row[0]),
        since=row[1],
        days_recorded=_int(row[2]),
        days_unmeasured=_int(row[3]),
    )


def storage_trend(session: Session, *, today: date, days: int = TREND_DAYS) -> list[TrendPoint]:
    since = today - timedelta(days=days - 1)
    running = cumulative_freed(session, before=since).bytes_freed
    rows = session.execute(
        select(
            FleetImpactHistory.report_date,
            FleetImpactHistory.bytes_freed,
            FleetImpactHistory.measurement_state,
            FleetImpactHistory.total_size_tb,
            FleetImpactHistory.raw,
        )
        .where(FleetImpactHistory.report_date >= since, FleetImpactHistory.report_date <= today)
        .order_by(FleetImpactHistory.report_date.asc())
        .limit(days)
    ).all()
    points: list[TrendPoint] = []
    for report_date, bytes_freed, state, total_tb, raw in rows:
        storage = raw.get("storage") if isinstance(raw, dict) else None
        storage = storage if isinstance(storage, dict) else {}
        total = storage.get("total_bytes")
        if total is None and total_tb is not None:
            # Days recorded before the storage snapshot existed only kept total_size_tb (TiB).
            total = round(float(total_tb) * TIB)
        live = storage.get("live_bytes")
        measured = state != "unmeasured"
        if measured:
            running += _int(bytes_freed)
        points.append(
            TrendPoint(
                day=report_date,
                total_bytes=_int(total) if total is not None else None,
                live_bytes=_int(live) if live is not None else None,
                bytes_freed=_int(bytes_freed) if measured else None,
                cumulative_freed_bytes=running,
                measurement_state=state,
            )
        )
    return points


def versioning_findings(session: Session) -> list[VersioningFinding] | None:
    """Buckets with versioning on and no noncurrent-version expiration rule.

    Reads ``TableCost.raw["bucket_versioning"]`` (``{"bucket", "status",
    "noncurrent_expiration"}``) for listed tables only. Only that JSON key is selected (never the
    whole ``raw`` blob), with a row cap. Returns None when no table reports the field at all.
    """
    rows = session.execute(
        select(TableCost.raw["bucket_versioning"])
        .join(Table, Table.id == TableCost.table_id)
        .where(Table.is_active.is_(True), TableCost.listed_bytes.isnot(None))
        .limit(_MAX_VERSIONING_ROWS)
    ).scalars()
    seen_field = False
    per_bucket: dict[str, int] = {}
    for info in rows:
        if not isinstance(info, dict):
            continue
        seen_field = True
        status = str(info.get("status") or "").lower()
        if status != "enabled" or info.get("noncurrent_expiration") is True:
            continue
        bucket = str(info.get("bucket") or "unknown")
        per_bucket[bucket] = per_bucket.get(bucket, 0) + 1
    if not seen_field:
        return None
    ordered = sorted(per_bucket.items(), key=lambda item: (-item[1], item[0]))
    return [VersioningFinding(bucket=b, tables=n) for b, n in ordered[:_MAX_VERSIONING_FINDINGS]]


def storage_overview(session: Session, *, now: datetime | None = None) -> StorageOverview:
    now = now or datetime.now(UTC)
    return StorageOverview(
        generated_at=now,
        breakdown=fleet_storage_breakdown(session),
        freed_24h=freed_last_24h(session, now=now),
        cumulative=cumulative_freed(session),
        trend=storage_trend(session, today=now.date()),
        versioning_findings=versioning_findings(session),
    )


def storage_snapshot_for_history(session: Session) -> dict[str, Any] | None:
    """Breakdown to persist on the day's FleetImpactHistory.raw; None if the query fails.

    Runs in a savepoint so a failure here never aborts the daily report's transaction.
    """
    try:
        with session.begin_nested():
            return fleet_storage_breakdown(session).to_raw()
    except SQLAlchemyError:
        logger.warning("storage breakdown snapshot failed", exc_info=True)
        return None
