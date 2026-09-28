"""Persist one FleetImpactHistory row per day from the same data the daily report already
computes, and read the resulting trend back out.

Built 2026-09-28 after repeatedly re-deriving the same "how much did Cryo actually reclaim/
cost/fix" numbers by hand from raw AuditLog/TableStats/MaintenanceJob queries for a management
report -- there was no durable record of what was true on any given day, only what could be
recomputed live (and only as far back as AuditLog retention allows). record_daily_impact writes
one row from DailyReportData, already fully computed by generate_daily_report -- no new heavy
queries beyond the job-status counts, which daily_report.py's own maintenance_stats doesn't
carry at job granularity (it's keyed by AuditLog action, not MaintenanceJob status).

Idempotent on report_date: safe to call more than once for the same day (a report re-run, a
backfill) without duplicating history -- the later call's numbers win, matching "the report
reflects current understanding" rather than "the first run that day is permanent."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import FleetImpactHistory, MaintenanceJob
from cryo.reporting.impact_signals import day_bounds, measured_reclaim_gb

# reporting.daily_report imports this module for the write call, so importing DailyReportData
# back would be circular; callers pass the fields this needs instead of the whole dataclass.


@dataclass(frozen=True)
class FleetImpactHistoryRow:
    """Read-side view of one day's persisted row -- mirrors FleetImpactHistory's columns."""

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

    @property
    def gb_freed(self) -> float:
        return self.bytes_freed / (1024**3)

    @property
    def tib_freed(self) -> float:
        return self.bytes_freed / (1024**4)

    @property
    def job_success_rate_pct(self) -> float | None:
        total = self.jobs_succeeded + self.jobs_failed
        if total == 0:
            return None
        return 100.0 * self.jobs_succeeded / total


def _job_status_counts(session: Session, *, day_start: datetime, day_end: datetime) -> tuple[int, int]:
    """Succeeded/failed MaintenanceJob counts for [day_start, day_end) by admitted_at --
    same window compute_impact_signal uses for 'yesterday', so this row's reliability numbers
    and its reclaim numbers describe the same calendar day."""
    rows = session.execute(
        select(MaintenanceJob.status, func.count())
        .where(
            MaintenanceJob.admitted_at >= day_start,
            MaintenanceJob.admitted_at < day_end,
            MaintenanceJob.status.in_(["succeeded", "failed"]),
        )
        .group_by(MaintenanceJob.status)
    ).all()
    counts = {status: int(n) for status, n in rows}
    return counts.get("succeeded", 0), counts.get("failed", 0)


def record_daily_impact(
    session: Session,
    *,
    report_data: "object",  # cryo.reporting.daily_report.DailyReportData, see module docstring
    infra_cost_usd: float | None = None,
    now: datetime | None = None,
) -> FleetImpactHistoryRow:
    """Upsert today's FleetImpactHistory row from an already-built DailyReportData.

    Call this once per daily-report run, after _collect_report_data but before/independent of
    whether Slack or email sending succeeds -- the history record should reflect what Cryo did
    that day regardless of whether the notification reached anyone.
    """
    now = now or datetime.now(UTC)
    impact = report_data.impact_signal
    today = impact.today if impact is not None else None
    day_start, day_end = day_bounds(now, 1)
    jobs_succeeded, jobs_failed = _job_status_counts(session, day_start=day_start, day_end=day_end)

    values = {
        "report_date": day_start.date(),
        "bytes_freed": today.bytes_freed if today is not None else 0,
        "measurement_state": today.measurement_state if today is not None else "no_actions",
        "actions_succeeded": today.actions_succeeded if today is not None else 0,
        "tables_touched": today.tables_touched if today is not None else 0,
        "actions_with_bytes_evidence": today.actions_with_bytes_evidence if today is not None else 0,
        "actions_without_bytes_evidence": (
            today.actions_without_bytes_evidence if today is not None else 0
        ),
        "optimize_files_in": today.optimize_files_in if today is not None else 0,
        "optimize_files_out": today.optimize_files_out if today is not None else 0,
        "active_tables": report_data.total_tables,
        "tables_grade_d_or_f": report_data.unhealthy_tables,
        "total_size_tb": report_data.total_size_tb,
        "total_monthly_cost_usd": report_data.total_cost_usd,
        "projected_savings_usd_month": report_data.projected_savings_usd,
        "jobs_succeeded": jobs_succeeded,
        "jobs_failed": jobs_failed,
        "infra_cost_usd": infra_cost_usd,
        "raw": {
            "measured_reclaim_gb": (
                measured_reclaim_gb(impact) if impact is not None else None
            ),
            "impact_direction": impact.direction if impact is not None else None,
        },
    }

    # ORM select-then-update-or-insert rather than a dialect-specific ON CONFLICT upsert:
    # portable across the Postgres this runs against in prod and the SQLite test fixture,
    # and it goes through the normal unit-of-work flush (autoincrement id assignment included)
    # instead of a raw Core statement that bypasses it.
    existing = session.execute(
        select(FleetImpactHistory).where(FleetImpactHistory.report_date == day_start.date())
    ).scalar_one_or_none()
    if existing is not None:
        for key, value in values.items():
            if key != "report_date":
                setattr(existing, key, value)
        existing.recorded_at = now
        row = existing
    else:
        row = FleetImpactHistory(**values, recorded_at=now)
        session.add(row)
    session.flush()
    return FleetImpactHistoryRow(
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
    )


def fleet_impact_trend(
    session: Session, *, since: date, until: date | None = None
) -> list[FleetImpactHistoryRow]:
    """Persisted daily history in [since, until], oldest first. until defaults to today.

    Days with measurement_state='unmeasured' are included, not filtered out -- callers summing
    bytes_freed across a range must check measurement_state per row first (same discipline as
    ImpactSignal.is_comparable_total) rather than silently summing an unmeasured day's 0 as if
    it were a real zero.
    """
    until = until or datetime.now(UTC).date()
    rows = session.execute(
        select(FleetImpactHistory)
        .where(FleetImpactHistory.report_date >= since, FleetImpactHistory.report_date <= until)
        .order_by(FleetImpactHistory.report_date.asc())
    ).scalars()
    return [
        FleetImpactHistoryRow(
            report_date=r.report_date,
            recorded_at=r.recorded_at,
            bytes_freed=r.bytes_freed,
            measurement_state=r.measurement_state,
            actions_succeeded=r.actions_succeeded,
            tables_touched=r.tables_touched,
            actions_with_bytes_evidence=r.actions_with_bytes_evidence,
            actions_without_bytes_evidence=r.actions_without_bytes_evidence,
            optimize_files_in=r.optimize_files_in,
            optimize_files_out=r.optimize_files_out,
            active_tables=r.active_tables,
            tables_grade_d_or_f=r.tables_grade_d_or_f,
            total_size_tb=r.total_size_tb,
            total_monthly_cost_usd=r.total_monthly_cost_usd,
            projected_savings_usd_month=r.projected_savings_usd_month,
            jobs_succeeded=r.jobs_succeeded,
            jobs_failed=r.jobs_failed,
            infra_cost_usd=r.infra_cost_usd,
        )
        for r in rows
    ]
