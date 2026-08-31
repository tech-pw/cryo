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

from cryo.db.models import (
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    Table,
    TableCost,
    TableStats,
    TableStatsHistory,
)
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
            _seed_scan_stats(session, stale_table, scan_at=NOW - timedelta(hours=100))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "scan_freshness")
        assert check.passed is True

    def test_fails_when_many_active_tables_are_stale_together(self, session) -> None:
        for i in range(30):
            _, table = _seed_source_table(session, name=f"stale{i}")
            _seed_scan_stats(session, table, scan_at=NOW - timedelta(hours=100))
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
            _seed_table_cost(session, table, scan_at=NOW - timedelta(hours=100))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "cost_ingestion_freshness")
        assert check.passed is False
        assert check.evidence["stale_count"] == 10

    def test_severity_escalates_to_critical_when_almost_all_stale(self, session) -> None:
        for i in range(10):
            _, table = _seed_source_table(session, name=f"t{i}")
            _seed_table_cost(session, table, scan_at=NOW - timedelta(hours=100))
        session.commit()

        report = run_production_health_checks(session, now=NOW)
        check = next(c for c in report.checks if c.name == "cost_ingestion_freshness")
        assert check.severity == "critical"


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
