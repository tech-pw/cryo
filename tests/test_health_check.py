"""Tests for cryo.reporting.health_check -- the daily production health checklist built
2026-08-19 after a single investigation session found three real, independent production bugs
(a batch-deadline blanket-failure, 1,210 tables repeatedly hitting orphaned-catalog errors, and
a fleet-wide approval-gate bug blocking expire_snapshots/remove_orphan_files for two weeks)
that no existing report had ever surfaced. Each check here targets the *structural shape* of
one of those incidents, not the exact bug, so a differently-shaped future incident is still
likely to be caught.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cryo.db.models import (
    Destination,
    MaintenanceControlState,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableCost,
    TableStats,
    TableStatsHistory,
)
from cryo.db.repositories.intents import set_pause_state
from cryo.domain.enums import OperationType
from cryo.reporting.health_check import (
    FleetFileTrend,
    compute_fleet_file_trend,
    run_production_health_checks,
)
from cryo.workflows.approvals import ApprovalRequest, ApprovalStatus

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


def _seed_source_table(session, *, name: str = "t") -> tuple[Source, Table]:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(
        id=uuid4(), source_id=source.id, schema_name="s", table_name=name, is_active=True
    )
    session.add_all([source, table])
    session.flush()
    return source, table


def _seed_run(session, *, status: str, created_at: datetime, trigger: str = "cron") -> MaintenanceRun:
    run = MaintenanceRun(id=uuid4(), kind="scheduled", status=status, trigger=trigger, created_at=created_at)
    session.add(run)
    session.flush()
    return run


def _seed_destination(session) -> Destination:
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="native", config={})
    session.add(destination)
    session.flush()
    return destination


def _seed_job(
    session,
    *,
    run: MaintenanceRun,
    table: Table,
    destination: Destination,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    error: str | None = None,
) -> MaintenanceJob:
    job = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        error=error,
    )
    session.add(job)
    session.flush()
    return job


class TestStuckRuns:
    def test_passes_when_no_old_running_run(self, session) -> None:
        _seed_run(session, status="running", created_at=NOW - timedelta(hours=1))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "stuck_runs")
        assert check.passed is True

    def test_fails_when_run_open_past_threshold(self, session) -> None:
        _seed_run(session, status="running", created_at=NOW - timedelta(days=5))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "stuck_runs")
        assert check.passed is False
        assert check.severity == "critical"
        assert check.evidence["count"] == 1

    def test_ignores_runs_already_finished(self, session) -> None:
        run = _seed_run(session, status="running", created_at=NOW - timedelta(days=5))
        run.status = "succeeded"
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "stuck_runs")
        assert check.passed is True

    def test_large_run_still_landing_jobs_recently_is_not_stuck(self, session) -> None:
        """Live-verified 2026-09-01 regression: a run open 19h with 6,640 total jobs and 195
        finished in the prior 2h is a large run still converging, not a stall. Raw age alone
        would have flagged this identically to a genuinely dead run -- must require BOTH past
        the age threshold AND no recent job completions."""
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW - timedelta(hours=19))
        _seed_job(
            session,
            run=run,
            table=table,
            destination=destination,
            status="succeeded",
            finished_at=NOW - timedelta(minutes=30),
        )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "stuck_runs")
        assert check.passed is True

    def test_old_run_with_no_recent_job_activity_is_stuck(self, session) -> None:
        """The genuine incident shape: a run open well past the age threshold where the most
        recent job activity is also well outside the recency window -- age alone and
        job-recency alone would both catch this; confirms the AND doesn't accidentally require
        stronger evidence than either check alone would need."""
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW - timedelta(days=5))
        _seed_job(
            session,
            run=run,
            table=table,
            destination=destination,
            status="succeeded",
            finished_at=NOW - timedelta(days=4),
        )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "stuck_runs")
        assert check.passed is False
        assert check.evidence["count"] == 1

    def test_recently_active_run_excluded_but_others_still_flagged(self, session) -> None:
        """Mixed scenario: one old run is still converging (must be excluded), another old run
        has gone quiet (must still be flagged) -- confirms the recency filter is applied
        per-run, not fleet-wide."""
        destination = _seed_destination(session)

        _, active_table = _seed_source_table(session, name="active")
        active_run = _seed_run(session, status="running", created_at=NOW - timedelta(hours=20))
        _seed_job(
            session,
            run=active_run,
            table=active_table,
            destination=destination,
            status="succeeded",
            finished_at=NOW - timedelta(minutes=10),
        )

        _, quiet_table = _seed_source_table(session, name="quiet")
        quiet_run = _seed_run(session, status="running", created_at=NOW - timedelta(days=3))
        _seed_job(
            session,
            run=quiet_run,
            table=quiet_table,
            destination=destination,
            status="succeeded",
            finished_at=NOW - timedelta(days=2),
        )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "stuck_runs")
        assert check.passed is False
        assert check.evidence["count"] == 1
        assert str(quiet_run.id) in check.evidence["examples"][0]


class TestStaleJobs:
    def test_passes_when_no_stale_jobs(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW)
        _seed_job(session, run=run, table=table, destination=destination, status="running", started_at=NOW)
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "stale_jobs")
        assert check.passed is True

    def test_fails_when_jobs_stuck_running_past_threshold(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW - timedelta(hours=10))
        _seed_job(
            session,
            run=run,
            table=table,
            destination=destination,
            status="running",
            started_at=NOW - timedelta(hours=10),
        )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "stale_jobs")
        assert check.passed is False
        assert check.evidence["running"] == 1


class TestAdmissionStall:
    def test_passes_when_queue_small(self, session) -> None:
        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "admission_stall")
        assert check.passed is True

    def test_fails_when_large_backlog_and_nothing_completing(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW)
        for _ in range(150):
            _, t = _seed_source_table(session)
            _seed_job(session, run=run, table=t, destination=destination, status="queued")
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "admission_stall")
        assert check.passed is False
        assert check.severity == "critical"
        assert check.evidence["backlog"] == 150

    def test_passes_when_large_backlog_but_draining(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW)
        for _ in range(150):
            _, t = _seed_source_table(session)
            _seed_job(session, run=run, table=t, destination=destination, status="queued")
        _seed_job(
            session,
            run=run,
            table=table,
            destination=destination,
            status="succeeded",
            finished_at=NOW - timedelta(minutes=10),
        )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "admission_stall")
        assert check.passed is True

    def test_passes_with_info_severity_when_intentionally_paused(self, session) -> None:
        """2026-09-04 fix: the exact live incident this closes -- the cost-ceiling breaker
        paused admission exactly as designed, and this check reported the resulting static
        backlog as the *same* critical severity as a genuine unexplained stall (indistinguishable
        in the vitals score from a real incident). A large backlog + zero completions while
        MaintenanceControlState is intentionally paused must report info, not critical, and
        must surface the pause reason so it's clear this is expected, not a mystery."""
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW)
        for _ in range(150):
            _, t = _seed_source_table(session)
            _seed_job(session, run=run, table=t, destination=destination, status="queued")
        set_pause_state(
            session,
            scope_type="global",
            scope_id="global",
            paused=True,
            changed_by="cost_ceiling_checker",
            reason="cost_ceiling_breach: 2026-09-03 spend $101.06 exceeded $100.00 ceiling",
        )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "admission_stall")
        assert check.passed is True
        assert check.severity == "info"
        assert "cost_ceiling_breach" in check.summary

    def test_still_fails_critical_when_stalled_and_not_paused(self, session) -> None:
        """Safety boundary: an explicit non-paused control row (or none at all, covered by
        the other tests in this class) must not suppress the real critical stall detection
        -- only paused=True changes the response."""
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW)
        for _ in range(150):
            _, t = _seed_source_table(session)
            _seed_job(session, run=run, table=t, destination=destination, status="queued")
        set_pause_state(
            session,
            scope_type="global",
            scope_id="global",
            paused=False,
            changed_by="alice@example.com",
            reason="resumed",
        )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "admission_stall")
        assert check.passed is False
        assert check.severity == "critical"


class TestQueueGrowth:
    def test_passes_when_baseline_too_small_and_depth_moderate(self, session) -> None:
        """Live-verified 2026-08-31: a yesterday baseline of 5 turned a real ~20k queue into
        a nonsensical '+412,240%' reading. Below _QUEUE_GROWTH_MIN_BASELINE, a moderate
        absolute depth (<100) should pass -- there's no meaningful percentage AND nothing
        alarming in absolute terms either."""
        destination = _seed_destination(session)
        run_yesterday = _seed_run(session, status="running", created_at=NOW - timedelta(days=2))
        for _ in range(3):
            _, t = _seed_source_table(session)
            _seed_job(session, run=run_yesterday, table=t, destination=destination, status="queued")
        run_today = _seed_run(session, status="running", created_at=NOW)
        for _ in range(10):
            _, t = _seed_source_table(session)
            _seed_job(session, run=run_today, table=t, destination=destination, status="queued")
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "queue_growth")
        assert check.passed is True

    def test_fails_when_baseline_too_small_but_depth_is_large(self, session) -> None:
        """Same tiny-baseline situation, but the absolute depth is genuinely large (>=100) --
        the percentage still can't be trusted, but the real backlog must still surface rather
        than silently passing just because the ratio was undefined."""
        destination = _seed_destination(session)
        run_yesterday = _seed_run(session, status="running", created_at=NOW - timedelta(days=2))
        for _ in range(5):
            _, t = _seed_source_table(session)
            _seed_job(session, run=run_yesterday, table=t, destination=destination, status="queued")
        run_today = _seed_run(session, status="running", created_at=NOW)
        for _ in range(150):
            _, t = _seed_source_table(session)
            _seed_job(session, run=run_today, table=t, destination=destination, status="queued")
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "queue_growth")
        assert check.passed is False
        assert check.evidence["today"] == 155  # 5 still-queued yesterday jobs + 150 new today
        assert "412" not in check.summary  # must not produce a distorted percentage

    def test_percentage_growth_still_used_above_min_baseline(self, session) -> None:
        """Above _QUEUE_GROWTH_MIN_BASELINE, the original percentage-based logic must still
        apply unchanged -- this fix only touches the small-baseline edge case. yesterday_depth
        is 100 (still-queued) -> today_depth is that same 100 plus 200 newly-seeded = 300,
        i.e. +200% growth off a real, non-distorted baseline."""
        destination = _seed_destination(session)
        run_yesterday = _seed_run(session, status="running", created_at=NOW - timedelta(days=2))
        for _ in range(100):
            _, t = _seed_source_table(session)
            _seed_job(session, run=run_yesterday, table=t, destination=destination, status="queued")
        run_today = _seed_run(session, status="running", created_at=NOW)
        for _ in range(200):
            _, t = _seed_source_table(session)
            _seed_job(session, run=run_today, table=t, destination=destination, status="queued")
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "queue_growth")
        assert check.passed is False
        assert check.evidence["today"] == 300
        assert check.evidence["growth_pct"] == 200.0


class TestApprovalMatchRate:
    def test_passes_when_no_approved_rows(self, session) -> None:
        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "approval_match_rate")
        assert check.passed is True

    def test_fails_when_the_real_bug_shape_reproduced(self, session) -> None:
        """Regression test for the exact live bug: an approval row with expires_at=NULL
        (standing grant) plus executed_at set from a prior run must still be counted as
        'approved' by this check's own total, and the real prefetch function must be able to
        match it -- if this check itself regresses back to the old buggy query, this test
        catches it the same way it would have caught the original two-week-long incident."""
        source, table = _seed_source_table(session)
        session.add(
            ApprovalRequest(
                id=uuid4(),
                table_id=table.id,
                operation_type=OperationType.EXPIRE_SNAPSHOTS.value,
                requested_by="fleet-sweep",
                approved_by="ops",
                status=ApprovalStatus.APPROVED,
                operation_params={},
                approved_at=NOW - timedelta(days=10),
                expires_at=None,
                executed_at=NOW - timedelta(days=5),
                execution_job_id=uuid4(),
            )
        )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "approval_match_rate")
        assert check.passed is True
        assert check.evidence == {}

    def test_fails_when_match_rate_is_genuinely_low(self, session) -> None:
        source, table = _seed_source_table(session)
        # A genuinely-expired approval -- correctly excluded, so with only this row the
        # match rate is 0%.
        session.add(
            ApprovalRequest(
                id=uuid4(),
                table_id=table.id,
                operation_type=OperationType.EXPIRE_SNAPSHOTS.value,
                requested_by="user@example.com",
                approved_by="ops",
                status=ApprovalStatus.APPROVED,
                operation_params={},
                approved_at=NOW - timedelta(days=5),
                expires_at=NOW - timedelta(hours=1),
            )
        )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "approval_match_rate")
        assert check.passed is False
        assert check.severity == "critical"
        assert check.evidence["match_rate_pct"] == 0.0


class TestRepeatFailures:
    def test_passes_when_below_threshold(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW)
        for _ in range(3):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="failed",
                finished_at=NOW,
                error="NoSuchKey: the specified key does not exist",
            )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "repeat_failures")
        assert check.passed is True

    def test_fails_when_orphaned_catalog_pattern_repeats(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW)
        for _ in range(8):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="failed",
                finished_at=NOW,
                error="failed to load table: blob NoSuchKey: The specified key does not exist.",
            )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "repeat_failures")
        assert check.passed is False
        assert "orphaned_catalog_entry (NoSuchKey)" in check.evidence
        assert check.evidence["orphaned_catalog_entry (NoSuchKey)"]["failures"] == 8

    def test_fails_when_native_job_failed_repeats(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW)
        for _ in range(6):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="failed",
                finished_at=NOW,
                error="native_job_failed",
            )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "repeat_failures")
        assert check.passed is False
        assert "native_job_failed" in check.evidence

    def test_ignores_failures_outside_the_window(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, status="running", created_at=NOW)
        for _ in range(8):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="failed",
                finished_at=NOW - timedelta(days=10),
                error="NoSuchKey",
            )
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "repeat_failures")
        assert check.passed is True


def _seed_table_stats(
    session, *, table_id, scan_at, total_files, small_files_count, total_size_gb
) -> TableStats:
    stats = TableStats(
        table_id=table_id,
        scan_at=scan_at,
        needs_compaction=False,
        compaction_priority="LOW",
        grade="B",
        overall_score=80,
        total_files=total_files,
        small_files_count=small_files_count,
        total_size_gb=total_size_gb,
    )
    session.add(stats)
    return stats


class TestFleetFileTrend:
    def test_today_only_when_no_history(self, session) -> None:
        source, table = _seed_source_table(session)
        _seed_table_stats(
            session,
            table_id=table.id,
            scan_at=NOW,
            total_files=1000,
            small_files_count=100,
            total_size_gb=50.0,
        )
        session.commit()

        trend = compute_fleet_file_trend(session, now=NOW)
        assert trend.today_total_files == 1000
        assert trend.today_small_files == 100
        assert trend.yesterday_total_files is None
        assert trend.total_files_delta is None
        assert trend.is_worsening is False

    def test_computes_day_over_day_delta(self, session) -> None:
        source, table = _seed_source_table(session)
        _seed_table_stats(
            session,
            table_id=table.id,
            scan_at=NOW,
            total_files=1200,
            small_files_count=300,
            total_size_gb=50.0,
        )
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=NOW - timedelta(days=2),
                total_files=1000,
                small_files_count=100,
                total_size_gb=50.0,
            )
        )
        session.commit()

        trend = compute_fleet_file_trend(session, now=NOW)
        assert trend.total_files_delta == 200
        assert trend.small_files_delta == 200
        assert trend.yesterday_small_file_ratio_pct == 10.0
        assert trend.today_small_file_ratio_pct == 25.0

    def test_uses_latest_history_row_per_table_not_all(self, session) -> None:
        """Regression test: the day-over-day comparison must use each table's single latest
        scan before the cutoff, not sum every historical row (which would massively
        overcount and make the trend meaningless)."""
        source, table = _seed_source_table(session)
        _seed_table_stats(
            session,
            table_id=table.id,
            scan_at=NOW,
            total_files=100,
            small_files_count=10,
            total_size_gb=5.0,
        )
        # Two older history rows for the same table -- only the latest (closest to, but
        # before, the cutoff) should be counted.
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=NOW - timedelta(days=5),
                total_files=50,
                small_files_count=5,
                total_size_gb=2.5,
            )
        )
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=NOW - timedelta(days=2),
                total_files=80,
                small_files_count=8,
                total_size_gb=4.0,
            )
        )
        session.commit()

        trend = compute_fleet_file_trend(session, now=NOW)
        assert trend.yesterday_total_files == 80

    def test_is_worsening_requires_both_signals(self, session) -> None:
        trend_both = FleetFileTrend(
            today_total_files=1200,
            today_small_files=400,
            today_total_size_gb=50.0,
            today_avg_file_size_mb=42.7,
            yesterday_total_files=1000,
            yesterday_small_files=100,
            yesterday_total_size_gb=50.0,
            yesterday_avg_file_size_mb=51.2,
            tables_counted=10,
        )
        assert trend_both.is_worsening is True

        # Ratio worsens but avg size improves -- not worsening (mixed signal).
        trend_mixed = FleetFileTrend(
            today_total_files=1200,
            today_small_files=400,
            today_total_size_gb=100.0,
            today_avg_file_size_mb=85.3,
            yesterday_total_files=1000,
            yesterday_small_files=100,
            yesterday_total_size_gb=50.0,
            yesterday_avg_file_size_mb=51.2,
            tables_counted=10,
        )
        assert trend_mixed.is_worsening is False


def _seed_scan_stats(session, table: Table, *, scan_at: datetime) -> TableStats:
    stats = TableStats(
        table_id=table.id,
        scan_at=scan_at,
        needs_compaction=False,
        compaction_priority="LOW",
        grade="A",
        overall_score=90,
        total_files=10,
        small_files_count=1,
        total_size_gb=1.0,
    )
    session.add(stats)
    session.flush()
    return stats


def _seed_table_cost(session, table: Table, *, scan_at: datetime) -> TableCost:
    cost = TableCost(
        table_id=table.id,
        scan_at=scan_at,
        precision="exact",
        price_per_gb_month=0.023,
        live_bytes=1_000_000,
        dead_bytes=0,
        metadata_bytes=0,
        current_monthly_cost_usd=1.0,
        projected_monthly_cost_usd=0.95,
        projected_savings_usd_month=0.05,
    )
    session.add(cost)
    session.flush()
    return cost


class TestScanFreshness:
    def test_passes_with_no_active_tables(self, session) -> None:
        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_freshness")
        assert check.passed is True

    def test_passes_when_recently_scanned(self, session) -> None:
        _, table = _seed_source_table(session)
        _seed_scan_stats(session, table, scan_at=NOW - timedelta(hours=2))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_freshness")
        assert check.passed is True

    def test_passes_when_a_handful_of_tables_are_stale(self, session) -> None:
        """Below _SCAN_STALE_MIN_COUNT -- normal skew from scan ordering, not a scanner
        outage. Seeds one fresh table (fresh_count=1) and a small number stale, well under
        the 25-table alert threshold."""
        _, fresh_table = _seed_source_table(session, name="fresh")
        _seed_scan_stats(session, fresh_table, scan_at=NOW - timedelta(hours=1))
        for i in range(5):
            _, stale_table = _seed_source_table(session, name=f"stale{i}")
            _seed_scan_stats(session, stale_table, scan_at=NOW - timedelta(hours=200))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_freshness")
        assert check.passed is True

    def test_fails_when_many_active_tables_are_stale_together(self, session) -> None:
        for i in range(30):
            _, table = _seed_source_table(session, name=f"stale{i}")
            _seed_scan_stats(session, table, scan_at=NOW - timedelta(hours=200))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_freshness")
        assert check.passed is False
        assert check.evidence["stale_count"] == 30

    def test_table_with_no_scan_at_all_counts_as_stale(self, session) -> None:
        """An active table that's never been scanned even once (no TableStats row) must still
        count toward stale_count -- it's the clearest possible sign scanning isn't reaching
        this table, not something to silently exclude from the denominator."""
        for i in range(30):
            _seed_source_table(session, name=f"never-scanned{i}")
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_freshness")
        assert check.passed is False
        assert check.evidence["active_total"] == 30
        assert check.evidence["fresh_count"] == 0

    def test_evolving_tables_excluded_from_denominator_and_stale_count(self, session) -> None:
        """Live-verified 2026-08-31: 648 of 755 never-scanned tables in prod were `_evolving`
        -- the known orphaned-catalog-entry class (ace7f4b) whose metadata.json is permanently
        gone by design, deliberately left uncleaned per a standing decision. These must not
        count toward active_total or stale_count, or a permanent, already-known-about class of
        table quietly dominates the score every single day."""
        for i in range(40):
            _seed_source_table(session, name=f"orphan{i}_evolving")
        _, real_table = _seed_source_table(session, name="real_table")
        _seed_scan_stats(session, real_table, scan_at=NOW - timedelta(hours=1))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_freshness")
        assert check.passed is True
        assert check.evidence == {} or check.evidence.get("active_total", 1) == 1


class TestScanFailures:
    def test_passes_with_no_failing_tables(self, session) -> None:
        _, table = _seed_source_table(session)
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_failures")
        assert check.passed is True

    def test_ignores_tables_below_consecutive_failure_threshold(self, session) -> None:
        _, table = _seed_source_table(session)
        table.consecutive_scan_failures = 2
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_failures")
        assert check.passed is True

    def test_passes_when_a_handful_of_tables_fail_individually(self, session) -> None:
        for i in range(5):
            _, table = _seed_source_table(session, name=f"broken{i}")
            table.consecutive_scan_failures = 4
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_failures")
        assert check.passed is True

    def test_fails_when_a_cluster_of_tables_fail_together(self, session) -> None:
        for i in range(15):
            _, table = _seed_source_table(session, name=f"broken{i}")
            table.consecutive_scan_failures = 4
            table.last_scan_error = "NoSuchKey"
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_failures")
        assert check.passed is False
        assert check.evidence["total_failing"] == 15

    def test_evolving_tables_excluded_even_when_failing(self, session) -> None:
        """Live-verified 2026-08-31: 680 of 817 scan-failing tables in prod were `_evolving`
        -- the same known, deliberately-uncleaned orphan class as scan_freshness. A large
        `_evolving` failure cluster must not trip this check; it would just re-flag the same
        known issue repeat_failures already surfaces on the job side, under a new name."""
        for i in range(20):
            _, table = _seed_source_table(session, name=f"orphan{i}_evolving")
            table.consecutive_scan_failures = 10
            table.last_scan_error = "NoSuchKey"
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_failures")
        assert check.passed is True


class TestCostIngestionFreshness:
    def test_passes_with_no_active_tables(self, session) -> None:
        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "cost_ingestion_freshness")
        assert check.passed is True

    def test_passes_when_no_cost_rows_ingested_yet(self, session) -> None:
        """Zero table_cost rows means ingestion hasn't run for anyone yet -- distinct from
        'ingestion ran and is now stale', which needs at least one row to compare against."""
        _seed_source_table(session)
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "cost_ingestion_freshness")
        assert check.passed is True

    def test_passes_when_recently_priced(self, session) -> None:
        _, table = _seed_source_table(session)
        _seed_table_cost(session, table, scan_at=NOW - timedelta(hours=3))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "cost_ingestion_freshness")
        assert check.passed is True

    def test_fails_when_most_priced_tables_are_stale(self, session) -> None:
        for i in range(10):
            _, table = _seed_source_table(session, name=f"t{i}")
            _seed_table_cost(session, table, scan_at=NOW - timedelta(hours=200))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "cost_ingestion_freshness")
        assert check.passed is False
        assert check.evidence["stale_count"] == 10

    def test_severity_escalates_to_critical_when_almost_all_stale(self, session) -> None:
        for i in range(10):
            _, table = _seed_source_table(session, name=f"t{i}")
            _seed_table_cost(session, table, scan_at=NOW - timedelta(hours=200))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "cost_ingestion_freshness")
        assert check.severity == "critical"

    def test_evolving_tables_excluded_from_stale_denominator(self, session) -> None:
        """Live-verified 2026-08-31: only 66 of 7,306 stale-priced tables in prod were
        `_evolving` -- the opposite finding from the scan checks (cost staleness is a real,
        dominant, non-evolving signal). Still confirms `_evolving` rows themselves don't
        distort the ratio when present."""
        for i in range(20):
            _, table = _seed_source_table(session, name=f"orphan{i}_evolving")
            _seed_table_cost(session, table, scan_at=NOW - timedelta(hours=200))
        _, real_table = _seed_source_table(session, name="real_table")
        _seed_table_cost(session, real_table, scan_at=NOW - timedelta(hours=1))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "cost_ingestion_freshness")
        assert check.passed is True


class TestNamespaceUnusedCost:
    def test_passes_when_token_not_configured(self, session, monkeypatch) -> None:
        monkeypatch.delenv("CRYO_GRAFANA_TOKEN", raising=False)

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "namespace_unused_cost")
        assert check.passed is True
        assert check.severity == "info"

    def test_passes_when_grafana_unreachable(self, session, monkeypatch) -> None:
        monkeypatch.setenv("CRYO_GRAFANA_TOKEN", "fake-token")
        monkeypatch.setattr(
            "cryo.reporting.cost_ceiling.fetch_latest_billed_namespace_cost",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("connection refused")),
        )

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "namespace_unused_cost")
        assert check.passed is True
        assert check.severity == "info"

    def test_passes_when_no_committed_cur_rows_yet(self, session, monkeypatch) -> None:
        monkeypatch.setenv("CRYO_GRAFANA_TOKEN", "fake-token")
        monkeypatch.setattr(
            "cryo.reporting.cost_ceiling.fetch_latest_billed_namespace_cost",
            lambda **kwargs: None,
        )

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "namespace_unused_cost")
        assert check.passed is True
        assert check.severity == "info"

    def test_passes_when_unused_at_or_below_used(self, session, monkeypatch) -> None:
        from cryo.reporting.cost_ceiling import NamespaceCostCheck

        monkeypatch.setenv("CRYO_GRAFANA_TOKEN", "fake-token")
        monkeypatch.setattr(
            "cryo.reporting.cost_ceiling.fetch_latest_billed_namespace_cost",
            lambda **kwargs: NamespaceCostCheck(
                day=NOW.date(), used_cost_usd=50.0, unused_cost_usd=30.0, ceiling_usd=100.0
            ),
        )

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "namespace_unused_cost")
        assert check.passed is True

    def test_fails_when_unused_exceeds_used(self, session, monkeypatch) -> None:
        """Live-verified 2026-09-01: real cryo namespace CUR data, unused $60.78 vs used
        $20.60 -- ~3x. Direct regression for the exact live finding."""
        from cryo.reporting.cost_ceiling import NamespaceCostCheck

        monkeypatch.setenv("CRYO_GRAFANA_TOKEN", "fake-token")
        monkeypatch.setattr(
            "cryo.reporting.cost_ceiling.fetch_latest_billed_namespace_cost",
            lambda **kwargs: NamespaceCostCheck(
                day=NOW.date(), used_cost_usd=20.60, unused_cost_usd=60.78, ceiling_usd=100.0
            ),
        )

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "namespace_unused_cost")
        assert check.passed is False
        assert check.evidence["ratio"] == pytest.approx(2.951, abs=0.01)

    def test_severity_escalates_to_critical_at_3x_ratio(self, session, monkeypatch) -> None:
        from cryo.reporting.cost_ceiling import NamespaceCostCheck

        monkeypatch.setenv("CRYO_GRAFANA_TOKEN", "fake-token")
        monkeypatch.setattr(
            "cryo.reporting.cost_ceiling.fetch_latest_billed_namespace_cost",
            lambda **kwargs: NamespaceCostCheck(
                day=NOW.date(), used_cost_usd=10.0, unused_cost_usd=40.0, ceiling_usd=100.0
            ),
        )

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "namespace_unused_cost")
        assert check.passed is False
        assert check.severity == "critical"

    def test_passes_when_used_cost_is_zero(self, session, monkeypatch) -> None:
        """A $0-used day makes the ratio undefined, not automatically a violation -- must not
        divide by zero or report a false failure."""
        from cryo.reporting.cost_ceiling import NamespaceCostCheck

        monkeypatch.setenv("CRYO_GRAFANA_TOKEN", "fake-token")
        monkeypatch.setattr(
            "cryo.reporting.cost_ceiling.fetch_latest_billed_namespace_cost",
            lambda **kwargs: NamespaceCostCheck(
                day=NOW.date(), used_cost_usd=0.0, unused_cost_usd=5.0, ceiling_usd=100.0
            ),
        )

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "namespace_unused_cost")
        assert check.passed is True


class TestOverallReport:
    def test_all_ok_gives_ok_status(self, session) -> None:
        report = run_production_health_checks(session, now=NOW)
        assert report.overall_status == "OK"
        assert report.critical_count == 0

    def test_critical_check_drives_overall_status(self, session) -> None:
        _seed_run(session, status="running", created_at=NOW - timedelta(days=5))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        assert report.overall_status == "CRITICAL"
        assert report.critical_count >= 1
