"""Read-only daily fleet impact ledger.

``fleet_impact_trend`` does not bound its window and does not return ``raw``.
This route caps the inclusive date span before calling it, then attaches each
day's ``raw`` (including ``bytes_freed_by_engine`` when stored) from the same
bounded window. An unmeasured day's ``bytes_freed`` of 0 is passed through with
``measurement_state`` unchanged — it is not a measured zero.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.api.deps import SessionDep, require_viewer
from cryo.db.models import FleetImpactHistory
from cryo.reporting.impact_history import FleetImpactHistoryRow, fleet_impact_trend

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
