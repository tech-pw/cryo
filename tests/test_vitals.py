"""Tests for cryo.reporting.vitals -- the single-score "is everything in Cryo working as
expected right now" signal, built 2026-08-31 as the operational-health counterpart to
impact_signals.py's north-star metric. Reuses run_production_health_checks() rather than
re-implementing its checks, so these tests focus on the two pulses this module owns (dispatch
heartbeat, success rate) and the score/status composition on top of them.
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
)
from cryo.reporting.vitals import (
    _SUCCESS_RATE_MIN_TERMINAL_JOBS,
    compute_cryo_vitals,
    cryo_vitals_to_payload,
)

NOW = datetime(2026, 8, 31, 15, 0, tzinfo=UTC)


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
    session,
    *,
    run: MaintenanceRun,
    table: Table,
    destination: Destination,
    status: str,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
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
    )
    session.add(job)
    session.flush()
    return job


class TestDispatchHeartbeat:
    def test_no_jobs_at_all_is_stalled(self, session) -> None:
        session.commit()
        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.dispatch_heartbeat.is_stalled is True
        assert vitals.dispatch_heartbeat.last_activity_at is None

    def test_recent_activity_is_not_stalled(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, created_at=NOW - timedelta(minutes=10))
        _seed_job(
            session,
            run=run,
            table=table,
            destination=destination,
            status="succeeded",
            started_at=NOW - timedelta(minutes=10),
            finished_at=NOW - timedelta(minutes=5),
        )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.dispatch_heartbeat.is_stalled is False
        assert vitals.dispatch_heartbeat.minutes_since == 5.0

    def test_stale_activity_past_threshold_is_stalled(self, session) -> None:
        """Threshold default is 26h (dispatch_heartbeat_stall_minutes), sized for the
        once-daily maintenance CronJob's own idle gap -- a job finished 4h ago must NOT read
        as stalled (that was the old, wrong 90min-threshold behavior); only a gap genuinely
        past the daily schedule's own cadence should."""
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, created_at=NOW - timedelta(hours=30))
        _seed_job(
            session,
            run=run,
            table=table,
            destination=destination,
            status="succeeded",
            started_at=NOW - timedelta(hours=30),
            finished_at=NOW - timedelta(hours=29),
        )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.dispatch_heartbeat.is_stalled is True

    def test_activity_within_daily_cadence_gap_is_not_stalled(self, session) -> None:
        """The exact false-positive confirmed live 2026-09-28: a job that finished several
        hours ago, well within the ~18-26h gap between two nightly maintenance runs, must not
        be flagged -- that quiet gap is the normal shape of a once-daily dispatch source, not
        a stall."""
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, created_at=NOW - timedelta(hours=5))
        _seed_job(
            session,
            run=run,
            table=table,
            destination=destination,
            status="succeeded",
            started_at=NOW - timedelta(hours=5),
            finished_at=NOW - timedelta(hours=4),
        )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.dispatch_heartbeat.is_stalled is False

    def test_stalled_heartbeat_forces_critical_status_even_with_clean_checklist(self, session) -> None:
        """A stalled dispatch heartbeat is exactly the multi-day-stall shape fixed 2026-08-31
        in scheduler/service.py -- this must independently drive status to CRITICAL even when
        health_check.py's own checklist has nothing to flag (e.g. no stuck runs/jobs recorded
        because nothing has run recently enough to go stale by that checklist's own thresholds)."""
        session.commit()
        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.dispatch_heartbeat.is_stalled is True
        assert vitals.status == "CRITICAL"


class TestSuccessRatePulse:
    def test_below_minimum_terminal_jobs_has_no_signal(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, created_at=NOW - timedelta(hours=1))
        for _ in range(3):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="succeeded",
                started_at=NOW - timedelta(minutes=30),
                finished_at=NOW - timedelta(minutes=20),
            )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.success_rate.has_enough_signal is False
        assert vitals.success_rate.success_rate_pct is None

    def test_high_success_rate_no_deduction(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, created_at=NOW - timedelta(hours=1))
        for _ in range(_SUCCESS_RATE_MIN_TERMINAL_JOBS):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="succeeded",
                started_at=NOW - timedelta(minutes=30),
                finished_at=NOW - timedelta(minutes=20),
            )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.success_rate.has_enough_signal is True
        assert vitals.success_rate.success_rate_pct == 100.0
        assert not any("success_rate" in d.reason for d in vitals.deductions)

    def test_low_success_rate_deducts_and_forces_critical(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, created_at=NOW - timedelta(hours=1))
        for _ in range(5):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="succeeded",
                started_at=NOW - timedelta(minutes=30),
                finished_at=NOW - timedelta(minutes=20),
            )
        for _ in range(20):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="failed",
                started_at=NOW - timedelta(minutes=30),
                finished_at=NOW - timedelta(minutes=20),
            )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.success_rate.has_enough_signal is True
        assert vitals.success_rate.success_rate_pct == 20.0
        assert vitals.status == "CRITICAL"
        assert any("success_rate" in d.reason for d in vitals.deductions)

    def test_moderate_success_rate_degrades_not_critical(self, session) -> None:
        """85% is below the 90% warn threshold but above the 75% critical threshold -- should
        land in DEGRADED, not CRITICAL, and dispatch heartbeat must be kept fresh so it isn't
        the thing driving the status instead."""
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, created_at=NOW - timedelta(hours=1))
        for _ in range(17):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="succeeded",
                started_at=NOW - timedelta(minutes=10),
                finished_at=NOW - timedelta(minutes=5),
            )
        for _ in range(3):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="failed",
                started_at=NOW - timedelta(minutes=10),
                finished_at=NOW - timedelta(minutes=5),
            )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.success_rate.success_rate_pct == 85.0
        assert vitals.dispatch_heartbeat.is_stalled is False
        assert vitals.status == "DEGRADED"


class TestGovernorScanCostChecksFlowIntoVitals:
    """Direct regression coverage that health_check.py's scan_freshness/scan_failures/
    cost_ingestion_freshness checks -- added per explicit feedback that vitals should cover
    "governer, cost ingestion, scanning etc", not just job-execution-shaped incidents -- are
    actually wired into the composed vitals score/status, not just present in health_check.py's
    own report in isolation."""

    def test_scanner_stalled_across_fleet_degrades_vitals(self, session) -> None:
        for i in range(30):
            source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
            table = Table(
                id=uuid4(), source_id=source.id, schema_name="s", table_name=f"t{i}", is_active=True
            )
            session.add_all([source, table])
            session.flush()
            session.add(
                TableStats(
                    table_id=table.id,
                    scan_at=NOW - timedelta(hours=200),
                    needs_compaction=False,
                    compaction_priority="LOW",
                    grade="A",
                    overall_score=90,
                    total_files=10,
                    small_files_count=1,
                    total_size_gb=1.0,
                )
            )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        scan_check = next(c for c in vitals.health.checks if c.name == "scan_freshness")
        assert scan_check.passed is False
        assert any("scan_freshness" in d.reason for d in vitals.deductions)
        assert vitals.status in ("DEGRADED", "CRITICAL")

    def test_cost_ingestion_stalled_degrades_vitals(self, session) -> None:
        for i in range(10):
            source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
            table = Table(
                id=uuid4(), source_id=source.id, schema_name="s", table_name=f"t{i}", is_active=True
            )
            session.add_all([source, table])
            session.flush()
            session.add(
                TableCost(
                    table_id=table.id,
                    scan_at=NOW - timedelta(hours=200),
                    precision="exact",
                    price_per_gb_month=0.023,
                    live_bytes=1_000_000,
                    dead_bytes=0,
                    metadata_bytes=0,
                    current_monthly_cost_usd=1.0,
                    projected_monthly_cost_usd=0.95,
                    projected_savings_usd_month=0.05,
                )
            )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        cost_check = next(c for c in vitals.health.checks if c.name == "cost_ingestion_freshness")
        assert cost_check.passed is False
        assert any("cost_ingestion_freshness" in d.reason for d in vitals.deductions)
        assert vitals.status in ("DEGRADED", "CRITICAL")


class TestScoreAndPayload:
    def test_perfectly_healthy_fleet_scores_100_and_healthy_status(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, created_at=NOW - timedelta(hours=1))
        for _ in range(_SUCCESS_RATE_MIN_TERMINAL_JOBS):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="succeeded",
                started_at=NOW - timedelta(minutes=10),
                finished_at=NOW - timedelta(minutes=5),
            )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.score == 100.0
        assert vitals.status == "HEALTHY"
        assert vitals.deductions == []

    def test_score_never_goes_below_zero(self, session) -> None:
        """Every deduction stacked at once (empty fleet: no dispatch activity at all, plus
        whatever health_check.py's own checklist independently flags) must still clamp to a
        valid 0-100 score, not go negative."""
        session.commit()
        vitals = compute_cryo_vitals(session, now=NOW)
        assert 0.0 <= vitals.score <= 100.0

    def test_trino_hot_cost_signal_populates_without_affecting_score(self, session, monkeypatch) -> None:
        """The Trino-hot-tables cost signal answers a different question ("is this specific
        cost lever working") than vitals' score ("is Cryo healthy") -- a worsening cost trend
        must surface in the payload but never move the score/status, which would conflate an
        optional preset's own performance with platform health."""
        from cryo.config import get_settings
        from cryo.db.models import Preset, QueryLog, TableAssignment

        source, table = _seed_source_table(session)
        destination = Destination(id=uuid4(), name="spark-hot", engine="spark_emr", config={})
        session.add(destination)
        session.flush()
        preset = Preset(
            id=uuid4(),
            name=get_settings().trino_hot_preset_name,
            destination_id=destination.id,
            mode="standard",
            operations=["optimize"],
            params={},
            guardrails={},
            enabled=True,
        )
        session.add(preset)
        session.flush()
        session.add(TableAssignment(table_id=table.id, preset_id=preset.id, assigned_by="test", pinned=True))
        session.add(
            QueryLog(
                source_id=source.id,
                table_id=table.id,
                query_id=f"q-{uuid4()}",
                engine="trino",
                executed_at=NOW - timedelta(days=1),
                bytes_read=10 * 1024**3,
            )
        )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.trino_hot_cost is not None
        assert vitals.trino_hot_cost.today.cohort_size == 1
        # Never contributes a deduction, regardless of its own trend -- it answers a different
        # question than the score does (see CryoVitals.trino_hot_cost's own docstring).
        assert not any("trino_hot" in d.reason for d in vitals.deductions)

        payload = cryo_vitals_to_payload(vitals)
        assert payload["trino_hot_cost"]["cohort_size"] == 1
        assert payload["trino_hot_cost"]["preset_name"] == preset.name

    def test_payload_is_json_safe_and_carries_key_fields(self, session) -> None:
        session.commit()
        vitals = compute_cryo_vitals(session, now=NOW)
        payload = cryo_vitals_to_payload(vitals)

        assert payload["status"] == vitals.status
        assert payload["score"] == round(vitals.score, 1)
        assert "generated_at" in payload
        assert "health" in payload and "overall_status" in payload["health"]
        assert "impact" in payload and "gb_freed_yesterday" in payload["impact"]
        # Review finding F10: gb_freed_yesterday alone doesn't say whether it's a complete
        # measurement or a partial subtotal -- measurement_state must accompany it.
        assert payload["impact"]["measurement_state"] == vitals.impact.measurement_state
        assert "dispatch_heartbeat" in payload
        assert "success_rate" in payload
        # No trino-hot-tables preset seeded in this test DB -- must be None, not absent/error.
        assert payload["trino_hot_cost"] is None
        assert vitals.trino_hot_cost is None

        import json

        json.dumps(payload)  # must not raise -- every value JSON-serializable

    def test_headline_format(self, session) -> None:
        source, table = _seed_source_table(session)
        destination = _seed_destination(session)
        run = _seed_run(session, created_at=NOW - timedelta(hours=1))
        for _ in range(_SUCCESS_RATE_MIN_TERMINAL_JOBS):
            _seed_job(
                session,
                run=run,
                table=table,
                destination=destination,
                status="succeeded",
                started_at=NOW - timedelta(minutes=10),
                finished_at=NOW - timedelta(minutes=5),
            )
        session.commit()

        vitals = compute_cryo_vitals(session, now=NOW)
        assert vitals.headline == "HEALTHY — 100/100"
