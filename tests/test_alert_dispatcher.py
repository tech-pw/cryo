"""Tests for SLA/data-quality breach detection and owner/subscriber alert dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import (
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
