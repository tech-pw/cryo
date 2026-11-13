"""Read-only daily fleet impact ledger.

``fleet_impact_trend`` does not bound its window and does not return ``raw``.
This route caps the inclusive date span before calling it, then attaches each
day's ``raw`` (including ``bytes_freed_by_engine`` when stored) from the same
bounded window. An unmeasured day's ``bytes_freed`` of 0 is passed through with
``measurement_state`` unchanged — it is not a measured zero.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.api.deps import SessionDep, require_viewer
from cryo.db.models import FleetImpactHistory
from cryo.reporting.impact_history import FleetImpactHistoryRow, fleet_impact_trend
from cryo.reporting.storage_overview import GIB, storage_overview

router = APIRouter(prefix="/api/v1", tags=["impact"])

# One persisted row per calendar day. fleet_impact_trend has no cap, so refuse
# a span that would scan an unbounded history. 366 covers the UI's 7/30/90
# presets and a leap-year window without reading the whole table.
_MAX_RANGE_DAYS = 366


class ImpactHistoryRowRead(BaseModel):
    report_date: date
    recorded_at: datetime
    bytes_freed: int
    measurement_state: str
    actions_succeeded: int
    tables_touched: int
    actions_with_bytes_evidence: int
    actions_without_bytes_evidence: int
    # Share of succeeded reclaim actions that reported bytes; None when none succeeded.
    coverage_pct: float | None = None
    optimize_files_in: int
    optimize_files_out: int
    active_tables: int
    tables_grade_d_or_f: int
    total_size_tb: float | None
    total_monthly_cost_usd: float | None
    projected_savings_usd_month: float | None
    jobs_succeeded: int
    jobs_failed: int
    infra_cost_usd: float | None
    raw: dict[str, Any] | None


class ImpactHistoryResponse(BaseModel):
    since: date
    until: date
    rows: list[ImpactHistoryRowRead]


def _bounded_window(since: date, until: date | None) -> tuple[date, date]:
    end = until if until is not None else datetime.now(UTC).date()
    if end < since:
        raise HTTPException(status_code=422, detail="until must be on or after since")
    if (end - since).days > _MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"date range exceeds {_MAX_RANGE_DAYS} days",
        )
    return since, end


def _raw_by_date(
    session: Session, *, since: date, until: date
) -> dict[date, dict[str, Any] | None]:
    found = session.execute(
        select(FleetImpactHistory.report_date, FleetImpactHistory.raw).where(
            FleetImpactHistory.report_date >= since,
            FleetImpactHistory.report_date <= until,
        )
    ).all()
    raw_by_date: dict[date, dict[str, Any] | None] = {}
    for report_date, raw in found:
        raw_by_date[report_date] = raw if isinstance(raw, dict) else None
    return raw_by_date


def _to_read(row: FleetImpactHistoryRow, raw: dict[str, Any] | None) -> ImpactHistoryRowRead:
    return ImpactHistoryRowRead(
        report_date=row.report_date,
        recorded_at=row.recorded_at,
        bytes_freed=row.bytes_freed,
        measurement_state=row.measurement_state,
        actions_succeeded=row.actions_succeeded,
        tables_touched=row.tables_touched,
        actions_with_bytes_evidence=row.actions_with_bytes_evidence,
        actions_without_bytes_evidence=row.actions_without_bytes_evidence,
        coverage_pct=(
            round(row.actions_with_bytes_evidence / row.actions_succeeded * 100, 1)
            if row.actions_succeeded
            else None
        ),
        optimize_files_in=row.optimize_files_in,
        optimize_files_out=row.optimize_files_out,
        active_tables=row.active_tables,
        tables_grade_d_or_f=row.tables_grade_d_or_f,
        total_size_tb=row.total_size_tb,
        total_monthly_cost_usd=row.total_monthly_cost_usd,
        projected_savings_usd_month=row.projected_savings_usd_month,
        jobs_succeeded=row.jobs_succeeded,
        jobs_failed=row.jobs_failed,
        infra_cost_usd=row.infra_cost_usd,
        raw=raw,
    )


@router.get(
    "/impact/history",
    response_model=ImpactHistoryResponse,
    dependencies=[Depends(require_viewer)],
)
def get_impact_history(
    session: SessionDep,
    since: Annotated[date, Query(description="Inclusive start date.")],
    until: Annotated[
        date | None,
        Query(description="Inclusive end date. Defaults to today (UTC)."),
    ] = None,
) -> ImpactHistoryResponse:
    """Persisted daily impact. Unmeasured days keep their measurement_state."""
    window_since, window_until = _bounded_window(since, until)
    rows = fleet_impact_trend(session, since=window_since, until=window_until)
    raw_by_date = _raw_by_date(session, since=window_since, until=window_until)
    return ImpactHistoryResponse(
        since=window_since,
        until=window_until,
        rows=[_to_read(row, raw_by_date.get(row.report_date)) for row in rows],
    )


class StorageBreakdownRead(BaseModel):
    """Estimated bytes by category, from scanner cost accounting."""

    live_bytes: int
    retained_history_bytes: int
    dead_bytes: int
    orphan_bytes: int
    metadata_bytes: int
    total_bytes: int
    compaction_savings_bytes: int
    tables_total: int
    tables_with_cost: int
    tables_with_listing: int
    price_per_gb_month: float | None


class Freed24hRead(BaseModel):
    """Measured: summed actual_bytes_freed on successful reclaim actions."""

    bytes_freed: int
    actions_succeeded: int
    actions_measured: int
    tables_cleaned: int
    window_start: datetime
    window_end: datetime
    bytes_by_source: dict[str, int] | None
    savings_usd_month: float | None


class CumulativeFreedRead(BaseModel):
    bytes_freed: int
    since: date | None
    days_recorded: int
    days_unmeasured: int


class StorageTrendPointRead(BaseModel):
    day: date
    total_bytes: int | None
    live_bytes: int | None
    bytes_freed: int | None
    cumulative_freed_bytes: int
    measurement_state: str


class VersioningFindingRead(BaseModel):
    bucket: str
    tables: int


class StorageOverviewResponse(BaseModel):
    generated_at: datetime
    breakdown: StorageBreakdownRead
    freed_24h: Freed24hRead
    cumulative: CumulativeFreedRead
    trend: list[StorageTrendPointRead]
    versioning_findings: list[VersioningFindingRead] | None


@router.get(
    "/impact/storage-overview",
    response_model=StorageOverviewResponse,
    dependencies=[Depends(require_viewer)],
)
def get_storage_overview(session: SessionDep) -> StorageOverviewResponse:
    """Fleet storage breakdown, last-24h freed bytes, cumulative freed, and a 60-day trend."""
    overview = storage_overview(session)
    breakdown = overview.breakdown
    freed = overview.freed_24h
    price = breakdown.price_per_gb_month
    return StorageOverviewResponse(
        generated_at=overview.generated_at,
        breakdown=StorageBreakdownRead(**breakdown.to_raw()),
        freed_24h=Freed24hRead(
            bytes_freed=freed.bytes_freed,
            actions_succeeded=freed.actions_succeeded,
            actions_measured=freed.actions_measured,
            tables_cleaned=freed.tables_cleaned,
            window_start=freed.window_start,
            window_end=freed.window_end,
            bytes_by_source=freed.bytes_by_source,
            savings_usd_month=(freed.bytes_freed / GIB) * price if price is not None else None,
        ),
        cumulative=CumulativeFreedRead(**asdict(overview.cumulative)),
        trend=[StorageTrendPointRead(**asdict(point)) for point in overview.trend],
        versioning_findings=(
            None
            if overview.versioning_findings is None
            else [VersioningFindingRead(**asdict(f)) for f in overview.versioning_findings]
        ),
    )
