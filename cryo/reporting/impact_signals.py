"""North-star maintenance-impact metric: is Cryo actually shrinking the fleet, day to day?

Built 2026-08-19 after a real gap was found live: "N jobs succeeded" was the only maintenance
signal in the daily report, and `job.status = succeeded` only means the operation ran without
erroring -- it says nothing about whether it moved a real number. Spot-checking 181 tables
with before/after scans found file count *up* on 134 of them and snapshot count up on 121 --
not because maintenance failed, but because those tables kept receiving writes between scans
faster than a single run could offset. That's a cohort-selection question, not a "did this
work" question, and conflating the two hid the real answer.

The real answer already exists, per-job, in ``AuditLog.actual_bytes_freed`` /
``input_files_count`` / ``output_files_count`` -- written by every successful action, not
reconstructed from before/after scans that may be days apart or missing entirely (most tables
aren't re-scanned soon enough after their job for a scan-diff to mean anything). This module
sums that ground-truth field the same way ``cost_signals.py`` already proves out for cost:
same-day-range DoD/WoW comparisons only, never a partial period against a full one (that
distinction produced a genuinely wrong answer once already, not just an imprecise one -- see
that module's docstring).

North-star metric: **net bytes reclaimed per day** (remove_orphan_files + optimize +
convert_deletes' actual_bytes_freed, successful actions only) and, alongside it, **optimize's
file-count reduction ratio** (input_files_count -> output_files_count) since bytes freed alone
doesn't distinguish "we shrank fragmented tables" from "we deleted orphan garbage" -- both are
real wins, but they're different kinds of win worth seeing separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import AuditLog

ImpactDirection = Literal["improving", "worsening", "flat"]
ImpactSignalSource = Literal["dod", "wow"]

# Bytes-freed actions: the whole point of the job is reclaiming space. Excludes
# expire_snapshots (frees storage indirectly, once remove_orphan_files later collects the
# now-unreferenced files -- double-counting it here would credit the same bytes twice) and
# optimize_manifests/apply_table_properties/compute_statistics (no bytes-freed semantics).
_RECLAIM_ACTIONS = ("remove_orphan_files", "optimize", "convert_deletes", "rewrite_position_deletes")
_SUCCESS_STATUSES = ("OK", "succeeded")


@dataclass(frozen=True)
class DailyImpact:
    day_start: datetime
    day_end: datetime
    bytes_freed: int
    actions_succeeded: int
    tables_touched: int
    optimize_files_in: int
    optimize_files_out: int

    @property
    def gb_freed(self) -> float:
        return self.bytes_freed / (1024**3)

    @property
    def optimize_file_reduction_pct(self) -> float | None:
        if self.optimize_files_in <= 0:
            return None
        return (self.optimize_files_in - self.optimize_files_out) / self.optimize_files_in * 100


@dataclass(frozen=True)
class ImpactSignal:
    direction: ImpactDirection
    dod_pct: float | None
    wow_pct: float | None
    primary_signal: ImpactSignalSource | None
    today: DailyImpact
    dod_baseline_gb: float | None
    wow_baseline_gb: float | None


def _daily_impact(session: Session, day_start: datetime, day_end: datetime) -> DailyImpact:
    """Sum AuditLog's real per-action outcomes for one UTC day, [day_start, day_end)."""
    row = session.execute(
        select(
            func.coalesce(func.sum(AuditLog.actual_bytes_freed), 0),
            func.count(AuditLog.id),
            func.count(func.distinct(AuditLog.table_id)),
        ).where(
            AuditLog.ts >= day_start,
            AuditLog.ts < day_end,
            AuditLog.action.in_(_RECLAIM_ACTIONS),
            AuditLog.status.in_(_SUCCESS_STATUSES),
        )
    ).one()
    bytes_freed, actions_succeeded, tables_touched = row

    opt_row = session.execute(
        select(
            func.coalesce(func.sum(AuditLog.input_files_count), 0),
            func.coalesce(func.sum(AuditLog.output_files_count), 0),
        ).where(
            AuditLog.ts >= day_start,
            AuditLog.ts < day_end,
            AuditLog.action == "optimize",
            AuditLog.status.in_(_SUCCESS_STATUSES),
            AuditLog.input_files_count.isnot(None),
            AuditLog.output_files_count.isnot(None),
        )
    ).one()
    files_in, files_out = opt_row

    return DailyImpact(
        day_start=day_start,
        day_end=day_end,
        bytes_freed=int(bytes_freed or 0),
        actions_succeeded=int(actions_succeeded or 0),
        tables_touched=int(tables_touched or 0),
        optimize_files_in=int(files_in or 0),
        optimize_files_out=int(files_out or 0),
    )


def _day_bounds(now: datetime, days_ago: int) -> tuple[datetime, datetime]:
    """UTC calendar-day bounds for `days_ago` days before `now`'s date -- a full day, never a
    partial one, matching cost_signals.py's same-day-range rule."""
    anchor = (now - timedelta(days=days_ago)).date()
    start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _pct_change(current: float, baseline: float | None) -> float | None:
    if not baseline:
        return None
    return (current - baseline) / baseline * 100


def compute_impact_signal(session: Session, *, now: datetime | None = None) -> ImpactSignal:
    """Compute yesterday's real reclaim impact and its DoD/WoW trend.

    Uses yesterday (not today) as "current" for the same reason compute_cost_signal does --
    a report generated mid-day would otherwise compare a partial day against full baselines,
    which looks like a real change but is really just report-timing noise.
    """
    now = now or datetime.now(UTC)
    settings = get_settings()

    today_start, today_end = _day_bounds(now, 1)
    dod_start, dod_end = _day_bounds(now, 2)
    wow_start, wow_end = _day_bounds(now, 8)

    today = _daily_impact(session, today_start, today_end)
    dod_baseline = _daily_impact(session, dod_start, dod_end)
    wow_baseline = _daily_impact(session, wow_start, wow_end)

    dod_pct = _pct_change(today.gb_freed, dod_baseline.gb_freed)
    wow_pct = _pct_change(today.gb_freed, wow_baseline.gb_freed)

    # "Improving" means reclaiming MORE than baseline -- the opposite polarity from
    # cost_signals.py's spike/decline (there, less cost is good; here, more bytes freed is
    # good). A day with genuinely zero reclaim activity and a non-zero baseline is a real
    # regression worth flagging, not treated as "stable."
    direction: ImpactDirection = "flat"
    primary: ImpactSignalSource | None = None
    if dod_pct is not None and abs(dod_pct) >= settings.report_impact_dod_move_pct:
        direction = "improving" if dod_pct > 0 else "worsening"
        primary = "dod"
    elif wow_pct is not None and abs(wow_pct) >= settings.report_impact_wow_move_pct:
        direction = "improving" if wow_pct > 0 else "worsening"
        primary = "wow"

    return ImpactSignal(
        direction=direction,
        dod_pct=dod_pct,
        wow_pct=wow_pct,
        primary_signal=primary,
        today=today,
        dod_baseline_gb=dod_baseline.gb_freed,
        wow_baseline_gb=wow_baseline.gb_freed,
    )
