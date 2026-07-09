"""Tests for SLA/data-quality breach detection and owner/subscriber alert dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import (
    AuditLog,
    Source,
    Table,
    TableAlertDispatch,
    TableAlertSubscription,
    TableStats,
    TableStatsHistory,
)
from cryo.scheduler.alert_dispatcher import (
    check_table_breach,
    run_alert_dispatch_tick,
)


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _column_quality(*, null_rate: float) -> list[dict]:
    value_count = 1000
    null_count = int(value_count * null_rate / 100)
    return [
        {
            "field_id": 1,
            "column_name": "amount",
            "null_count": null_count,
            "value_count": value_count,
            "null_rate": round(null_count / value_count, 4),
            "lower_bound": 0,
            "upper_bound": 1000,
        }
    ]


def _seed_table(session, *, owner_email: str | None = None) -> Table:
    source = _seed_source(session)
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="finance",
        table_name="orders",
        is_active=True,
        owner_email=owner_email,
    )
    session.add(table)
    session.flush()
    return table


def _seed_breached_stats(session, table: Table, *, scan_at: datetime) -> None:
    """Stale snapshot (freshness breach) + a null-rate spike vs. 10-day baseline."""
    baseline = _column_quality(null_rate=2.0)
    for days_ago in range(10, 0, -1):
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=scan_at - timedelta(days=days_ago),
                small_files_count=10,
                total_size_gb=5.0,
                raw={"column_quality": baseline},
            )
        )
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="C",
            overall_score=65,
            total_files=200,
            small_files_count=20,
            total_size_gb=5.0,
            raw={
                "column_quality": _column_quality(null_rate=25.0),
                "last_snapshot_committed_at": (scan_at - timedelta(hours=48)).isoformat(),
            },
        )
    )
    session.commit()


def test_check_table_breach_reports_freshness_and_null_spike(session):
    table = _seed_table(session)
    scan_at = datetime.now(UTC)
    _seed_breached_stats(session, table, scan_at=scan_at)

    breach = check_table_breach(session, table)

    assert any("Freshness breached" in r for r in breach.reasons)
    assert any("Null-rate spike" in r for r in breach.reasons)


def test_check_table_breach_empty_when_healthy(session):
    table = _seed_table(session)
    scan_at = datetime.now(UTC)
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=98,
            total_files=10,
            small_files_count=0,
            total_size_gb=5.0,
            raw={
                "column_quality": _column_quality(null_rate=0.0),
                "last_snapshot_committed_at": scan_at.isoformat(),
            },
        )
    )
    session.commit()

    breach = check_table_breach(session, table)

    assert breach.reasons == []


def test_run_alert_dispatch_tick_skips_tables_without_owner_or_subscribers(session):
    table = _seed_table(session, owner_email=None)
    _seed_breached_stats(session, table, scan_at=datetime.now(UTC))

    sent = run_alert_dispatch_tick(session)

    assert sent == 0
    assert session.query(TableAlertDispatch).count() == 0


def test_run_alert_dispatch_tick_records_dispatch_for_owned_table(session):
    table = _seed_table(session, owner_email="owner@example.com")
    _seed_breached_stats(session, table, scan_at=datetime.now(UTC))

    sent = run_alert_dispatch_tick(session)

    assert sent == 1
    dispatch = session.query(TableAlertDispatch).one()
    assert dispatch.table_id == table.id
    assert dispatch.recipients == ["owner@example.com"]
    assert any("Freshness breached" in r for r in dispatch.reasons)


def test_run_alert_dispatch_tick_respects_cooldown(session):
    table = _seed_table(session, owner_email="owner@example.com")
    _seed_breached_stats(session, table, scan_at=datetime.now(UTC))

    first = run_alert_dispatch_tick(session)
    second = run_alert_dispatch_tick(session)

    assert first == 1
    assert second == 0
    assert session.query(TableAlertDispatch).count() == 1


def test_run_alert_dispatch_tick_includes_subscribers_without_owner(session):
    table = _seed_table(session, owner_email=None)
    _seed_breached_stats(session, table, scan_at=datetime.now(UTC))
    session.add(TableAlertSubscription(table_id=table.id, email="consumer@example.com"))
    session.commit()

    sent = run_alert_dispatch_tick(session)

    assert sent == 1
    dispatch = session.query(TableAlertDispatch).one()
    assert dispatch.recipients == ["consumer@example.com"]


def _seed_optimize_audit_rows(
    session, table: Table, *, input_files: int, output_files: int, count: int
) -> None:
    """Write ``count`` optimize AuditLog rows so the 30-day ratio aggregates correctly."""
    now = datetime.now(UTC)
    for offset in range(count):
        session.add(
            AuditLog(
                table_id=table.id,
                action="optimize",
                status="ok",
                ts=now - timedelta(days=offset),
                input_files_count=input_files,
                output_files_count=output_files,
            )
        )
    session.commit()


def test_effectiveness_breach_alerts_below_10_to_1(session):
    """Move 13: a sustained ratio below 10:1 should appear as a breach reason."""
    table = _seed_table(session)
    scan_at = datetime.now(UTC)
    _seed_breached_stats(session, table, scan_at=scan_at)
    # 80 input files / 20 output files = 4:1 -- well below the 10:1 threshold.
    _seed_optimize_audit_rows(session, table, input_files=80, output_files=20, count=5)

    breach = check_table_breach(session, table)

    assert any("Compaction effectiveness below 10:1" in r for r in breach.reasons), breach.reasons


def test_effectiveness_breach_silent_at_or_above_20_to_1(session):
    """Move 13: an excellent ratio (>=20:1) should NOT trigger the alert."""
    table = _seed_table(session)
    scan_at = datetime.now(UTC)
    _seed_breached_stats(session, table, scan_at=scan_at)
    # 200 input files / 5 output files = 40:1 -- well above the 20:1 threshold.
    _seed_optimize_audit_rows(session, table, input_files=200, output_files=5, count=5)

    breach = check_table_breach(session, table)

    assert not any("Compaction effectiveness" in r for r in breach.reasons)


def _seed_commit_race_audit_rows(
    session, table: Table, *, count: int, hours_ago: int = 1
) -> None:
    """Write ``count`` FAILED optimize rows whose detail carries the commit-race token."""
    from cryo.engines.native.runner import COMMIT_RACE_LOST_TOKEN

    now = datetime.now(UTC)
    for offset in range(count):
        session.add(
            AuditLog(
                table_id=table.id,
                action="optimize",
                status="FAILED",
                engine="native",
                ts=now - timedelta(hours=hours_ago + offset),
                detail=(
                    f"{COMMIT_RACE_LOST_TOKEN}: rewrite_data_files exhausted 5 checkpoint rounds"
                ),
            )
        )
    session.commit()


def test_commit_race_breach_alerts_after_repeated_losses(session):
    """Scoping doc §3.2: repeated commit-race losses surface as a first-class breach reason."""
    table = _seed_table(session)
    _seed_commit_race_audit_rows(session, table, count=3)

    breach = check_table_breach(session, table)

    assert any("losing the commit race" in r for r in breach.reasons), breach.reasons


def test_commit_race_breach_silent_below_min_occurrences(session):
    """A single loss (below the default min of 2) must not alert -- avoids noise on flukes."""
    table = _seed_table(session)
    _seed_commit_race_audit_rows(session, table, count=1)

    breach = check_table_breach(session, table)

    assert not any("losing the commit race" in r for r in breach.reasons)


def test_commit_race_breach_ignores_stale_losses(session):
    """Losses older than the lookback window don't keep a since-recovered table flagged."""
    table = _seed_table(session)
    _seed_commit_race_audit_rows(session, table, count=3, hours_ago=24 * 30)

    breach = check_table_breach(session, table)

    assert not any("losing the commit race" in r for r in breach.reasons)
