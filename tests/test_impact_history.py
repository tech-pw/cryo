"""Tests for cryo.reporting.impact_history -- the persisted daily FleetImpactHistory row this
session's management-report work exposed a gap for: every "how much did Cryo reclaim" number
had to be re-derived by hand from raw tables, with no durable record of what was true on any
given day. record_daily_impact writes one upserted row per calendar day from an already-built
DailyReportData; fleet_impact_trend reads the resulting history back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from cryo.db.models import (
    AuditLog,
    Destination,
    FleetImpactHistory,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
)
from cryo.reporting.impact_history import fleet_impact_trend, record_daily_impact
from cryo.reporting.impact_signals import compute_impact_signal, day_bounds

NOW = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)


def _seed_source_table(session, *, name: str = "t") -> tuple[Source, Table]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(id=uuid4(), source_id=source.id, schema_name="s", table_name=name, is_active=True)
    session.add_all([source, table])
    session.flush()
    return source, table


def _seed_run(session, *, created_at: datetime) -> MaintenanceRun:
    run = MaintenanceRun(id=uuid4(), kind="scheduled", status="completed", trigger="cron", created_at=created_at)
    session.add(run)
    session.flush()
    return run


def _seed_destination(session) -> Destination:
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="native", config={})
    session.add(destination)
    session.flush()
    return destination


def _seed_job(
    session, *, run: MaintenanceRun, table: Table, destination: Destination, status: str, admitted_at: datetime
) -> MaintenanceJob:
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status=status,
        admitted_at=admitted_at,
    )
    session.add(job)
    session.flush()
    return job


def _log(
    session,
    table: Table,
    *,
    days_ago: int,
    bytes_freed: int | None,
    engine: str | None = None,
    action: str = "remove_orphan_files",
    status: str = "OK",
) -> None:
    day_start, _ = day_bounds(NOW, days_ago)
    session.add(
        AuditLog(
            ts=day_start + timedelta(hours=6),
            table_id=table.id,
            action=action,
            status=status,
            engine=engine,
            actual_bytes_freed=bytes_freed,
        )
    )


@dataclass
class _FakeReportData:
    """Minimal stand-in for daily_report.DailyReportData -- record_daily_impact only reads
    these fields, so a full report build isn't needed to test the persistence layer."""

    impact_signal: object
    total_tables: int = 100
    unhealthy_tables: int = 10
    total_size_tb: float = 5.0
    total_cost_usd: float = 200.0
    projected_savings_usd: float = 50.0


class TestRecordDailyImpact:
    def test_writes_a_row_matching_the_computed_impact_signal(self, session) -> None:
        source, table = _seed_source_table(session)
        _log(session, table, days_ago=1, bytes_freed=5 * 1024**3)
        _log(session, table, days_ago=1, bytes_freed=3 * 1024**3)
        session.commit()

        signal = compute_impact_signal(session, now=NOW)
        report_data = _FakeReportData(impact_signal=signal)

        row = record_daily_impact(session, report_data=report_data, now=NOW)
        session.commit()

        assert row.report_date == day_bounds(NOW, 1)[0].date()
        assert row.bytes_freed == 8 * 1024**3
        assert row.measurement_state == "complete"
        assert row.active_tables == 100
        assert row.tables_grade_d_or_f == 10
        assert abs(row.gb_freed - 8.0) < 0.01

    def test_is_idempotent_on_report_date(self, session) -> None:
        """A second call for the same day (a re-run, a backfill) must update the existing row,
        not create a duplicate -- fleet_impact_trend summing history must never double-count a
        day just because the report happened to run twice."""
        source, table = _seed_source_table(session)
        _log(session, table, days_ago=1, bytes_freed=1024**3)
        session.commit()

        signal_a = compute_impact_signal(session, now=NOW)
        record_daily_impact(session, report_data=_FakeReportData(impact_signal=signal_a), now=NOW)
        session.commit()

        _log(session, table, days_ago=1, bytes_freed=1024**3)
        session.commit()
        signal_b = compute_impact_signal(session, now=NOW)
        record_daily_impact(session, report_data=_FakeReportData(impact_signal=signal_b), now=NOW)
        session.commit()

        rows = fleet_impact_trend(session, since=day_bounds(NOW, 2)[0].date(), until=NOW.date())
        assert len(rows) == 1
        assert rows[0].bytes_freed == 2 * 1024**3

    def test_unmeasured_day_is_recorded_as_unmeasured_not_zero(self, session) -> None:
        """Same discipline as ImpactSignal itself: a day where every succeeded action lacked
        byte evidence must persist measurement_state='unmeasured', not a bare 0 that a later
        summary could mistake for a real zero-reclaim day."""
        source, table = _seed_source_table(session)
        _log(session, table, days_ago=1, bytes_freed=None)
        session.commit()

        signal = compute_impact_signal(session, now=NOW)
        row = record_daily_impact(session, report_data=_FakeReportData(impact_signal=signal), now=NOW)
        session.commit()

        assert row.measurement_state == "unmeasured"
        assert row.bytes_freed == 0

    def test_no_actions_day_is_recorded_distinctly(self, session) -> None:
        """A day with zero successful actions at all is a real, legitimate zero -- must still
        write a row (so the trend has no silent gap) with measurement_state='no_actions'."""
        signal = compute_impact_signal(session, now=NOW)
        row = record_daily_impact(session, report_data=_FakeReportData(impact_signal=signal), now=NOW)
        session.commit()

        assert row.measurement_state == "no_actions"
        assert row.actions_succeeded == 0

    def test_records_job_success_and_failure_counts_for_the_same_day(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, created_at=NOW - timedelta(days=1))
        day_start, _ = day_bounds(NOW, 1)
        admitted = day_start + timedelta(hours=3)
        _seed_job(session, run=run, table=table, destination=destination, status="succeeded", admitted_at=admitted)
        _seed_job(session, run=run, table=table, destination=destination, status="succeeded", admitted_at=admitted)
        _seed_job(session, run=run, table=table, destination=destination, status="failed", admitted_at=admitted)
        session.commit()

        signal = compute_impact_signal(session, now=NOW)
        row = record_daily_impact(session, report_data=_FakeReportData(impact_signal=signal), now=NOW)
        session.commit()

        assert row.jobs_succeeded == 2
        assert row.jobs_failed == 1
        assert abs(row.job_success_rate_pct - (200 / 3)) < 0.01

    def test_splits_measured_reclaim_by_engine(self, session) -> None:
        """A byte figure is attributed to the engine that produced it. A success row with null
        actual_bytes_freed is not measured: it must not increment actions_measured, and it must
        not be stored as zero bytes."""
        _source, table = _seed_source_table(session)
        _log(session, table, days_ago=1, bytes_freed=5 * 1024**3, engine="native")
        _log(session, table, days_ago=1, bytes_freed=3 * 1024**3, engine="native")
        _log(
            session,
            table,
            days_ago=1,
            bytes_freed=2 * 1024**3,
            engine="trino",
            status="succeeded",
        )
        _log(session, table, days_ago=1, bytes_freed=1 * 1024**3, engine=None)
        _log(session, table, days_ago=1, bytes_freed=None, engine="native")
        _log(session, table, days_ago=1, bytes_freed=None, engine="spark")
        _log(session, table, days_ago=1, bytes_freed=9 * 1024**3, engine="native", status="FAILED")
        _log(
            session,
            table,
            days_ago=1,
            bytes_freed=9 * 1024**3,
            engine="trino",
            action="expire_snapshots",
        )
        _log(session, table, days_ago=2, bytes_freed=9 * 1024**3, engine="native")
        session.commit()

        signal = compute_impact_signal(session, now=NOW)
        record_daily_impact(session, report_data=_FakeReportData(impact_signal=signal), now=NOW)
        session.commit()

        persisted = session.execute(
            select(FleetImpactHistory).where(
                FleetImpactHistory.report_date == day_bounds(NOW, 1)[0].date()
            )
        ).scalar_one()
        assert persisted.raw is not None
        assert persisted.raw["measured_reclaim_gb"] == 11.0
        assert persisted.raw["impact_direction"] == "unknown"
        assert persisted.raw["bytes_freed_by_engine"] == {
            "native": {"bytes": 8 * 1024**3, "actions_measured": 2},
            "trino": {"bytes": 2 * 1024**3, "actions_measured": 1},
            "unknown": {"bytes": 1 * 1024**3, "actions_measured": 1},
        }


class TestFleetImpactTrend:
    def test_returns_rows_oldest_first_within_range(self, session) -> None:
        source, table = _seed_source_table(session)
        for days_ago in (3, 2, 1):
            _log(session, table, days_ago=days_ago, bytes_freed=1024**3)
            session.commit()
            signal = compute_impact_signal(session, now=NOW - timedelta(days=days_ago - 1))
            record_daily_impact(
                session,
                report_data=_FakeReportData(impact_signal=signal),
                now=NOW - timedelta(days=days_ago - 1),
            )
            session.commit()

        rows = fleet_impact_trend(session, since=day_bounds(NOW, 4)[0].date(), until=NOW.date())
        dates = [r.report_date for r in rows]
        assert dates == sorted(dates)
        assert len(rows) == 3

    def test_empty_range_returns_empty_list(self, session) -> None:
        rows = fleet_impact_trend(session, since=NOW.date(), until=NOW.date())
        assert rows == []
