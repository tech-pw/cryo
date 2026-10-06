"""Retry ladder for tables whose scans keep failing, and the digest's scan-health split.

Live 2026-09-24: ~1,350 of every 6-hourly scan's attempts were tables that had failed dozens of
times in a row (metadata gone from S3), and each scan reactivated ~1,200 dead catalog entries
that the cleanup daemon had just deactivated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from cryo.db.models import Source, Table, TableStats
from cryo.reporting.daily_report import DailyReportData, _build_slack_blocks
from cryo.reporting.scan_health import ScanHealth, collect_scan_health
from cryo.scanner.persistence import (
    record_table_scan_outcome,
    scan_retry_deferred,
    scan_retry_interval,
    upsert_table,
)
from cryo.scanner.run import run_scan

NOW = datetime(2026, 9, 24, 6, 0, tzinfo=UTC)
MISSING_METADATA = (
    'failed to load table: blob (key "bronze/x/t/metadata/00001-a.metadata.json") '
    "(code=NotFound): operation error S3: GetObject, https response error StatusCode: 404"
)


class _FailingCatalog:
    def __init__(self, tables: list[str]) -> None:
        self._tables = tables
        self.loaded: list[str] = []

    def list_tables(self) -> list[tuple[str, str]]:
        return [("ns", name) for name in self._tables]

    def list_tables_in_namespace(self, namespace: str) -> list[str]:
        return list(self._tables) if namespace == "ns" else []

    def load_table(self, schema: str, table_name: str) -> None:
        self.loaded.append(table_name)
        raise RuntimeError(MISSING_METADATA)


def _source(session, *, enabled: bool = True) -> Source:
    source = Source(
        id=uuid4(), name=f"src-{uuid4()}", type="rest", config={"uri": "x"}, enabled=enabled
    )
    session.add(source)
    session.flush()
    return source


def _table(
    session,
    source: Source,
    name: str,
    *,
    streak: int = 0,
    failed_ago: timedelta | None = None,
    error: str | None = MISSING_METADATA,
    active: bool = True,
    scanned: bool = False,
) -> Table:
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="ns",
        table_name=name,
        is_active=active,
        consecutive_scan_failures=streak,
        last_scan_error=error if streak else None,
        last_scan_error_at=NOW - failed_ago if failed_ago is not None else None,
    )
    session.add(table)
    session.flush()
    if scanned:
        session.add(
            TableStats(
                table_id=table.id,
                scan_at=NOW - timedelta(days=1),
                needs_compaction=False,
                compaction_priority="LOW",
                grade="B",
                overall_score=80,
                total_files=10,
                small_files_count=1,
                total_size_gb=1.0,
            )
        )
        session.flush()
    return table


@pytest.mark.parametrize(
    ("streak", "expected_days"),
    [(0, None), (1, 1), (2, 1), (3, 7), (4, 15), (5, 30), (60, 30)],
)
def test_ladder_goes_daily_weekly_fortnightly_monthly(streak: int, expected_days: int | None):
    interval = scan_retry_interval(streak)
    assert (interval.days if interval else None) == expected_days


def test_a_table_is_due_a_little_before_its_interval_so_it_does_not_slip_a_slot():
    table = Table(consecutive_scan_failures=3, last_scan_error_at=NOW - timedelta(days=7))
    assert not scan_retry_deferred(table, now=NOW - timedelta(hours=2))
    assert scan_retry_deferred(table, now=NOW - timedelta(days=1))


def test_scheduled_scan_defers_a_table_that_is_not_due(session):
    source = _source(session)
    _table(session, source, "dead", streak=3, failed_ago=timedelta(days=2))
    session.commit()
    catalog = _FailingCatalog(["dead"])

    summary = run_scan(
        session,
        source_id=source.id,
        chunk_id=0,
        num_chunks=1,
        now=NOW,
        parallel_tables=0,
        source_factory=lambda _s: catalog,
    )

    assert (summary.deferred, summary.unreadable, summary.failed) == (1, 0, 0)
    assert catalog.loaded == [], "a deferred table must not touch the catalog or S3"


def test_a_due_table_is_retried_and_moves_one_rung_down(session):
    source = _source(session)
    table = _table(session, source, "dead", streak=3, failed_ago=timedelta(days=7))
    session.commit()
    table_id = table.id
    catalog = _FailingCatalog(["dead"])

    summary = run_scan(
        session,
        source_id=source.id,
        chunk_id=0,
        num_chunks=1,
        now=NOW,
        parallel_tables=0,
        source_factory=lambda _s: catalog,
    )

    assert (summary.deferred, summary.unreadable) == (0, 1)
    refreshed = session.get(Table, table_id)
    assert refreshed.consecutive_scan_failures == 4
    assert scan_retry_interval(refreshed.consecutive_scan_failures) == timedelta(days=15)


@pytest.mark.parametrize("mode", ["table_ids", "full_scan"])
def test_targeted_and_full_scans_ignore_the_ladder(session, mode: str):
    source = _source(session)
    table = _table(session, source, "dead", streak=5, failed_ago=timedelta(hours=1))
    session.commit()
    catalog = _FailingCatalog(["dead"])
    scope = (
        {"table_ids": [table.id], "source_id": None}
        if mode == "table_ids"
        else {"source_id": source.id, "full_scan": True}
    )

    summary = run_scan(
        session,
        chunk_id=0,
        num_chunks=1,
        now=NOW,
        parallel_tables=0,
        source_factory=lambda _s: catalog,
        **scope,
    )

    assert summary.deferred == 0
    assert catalog.loaded == ["dead"]


def test_discovery_and_failed_scans_do_not_reactivate_a_deactivated_table(session):
    source = _source(session)
    _table(session, source, "dead", streak=40, failed_ago=timedelta(days=40), active=False)
    session.commit()

    run_scan(
        session,
        source_id=source.id,
        chunk_id=0,
        num_chunks=1,
        now=NOW,
        parallel_tables=0,
        source_factory=lambda _s: _FailingCatalog(["dead"]),
    )

    refreshed = upsert_table(session, source_id=source.id, schema_name="ns", table_name="dead")
    assert refreshed.is_active is False
    assert refreshed.consecutive_scan_failures == 41


def test_a_successful_scan_reactivates_the_table(session):
    source = _source(session)
    _table(session, source, "repaired", streak=6, failed_ago=timedelta(days=30), active=False)
    session.commit()

    table = record_table_scan_outcome(
        session,
        source_id=source.id,
        schema_name="ns",
        table_name="repaired",
        error=None,
        scanned_at=NOW,
    )

    assert table.is_active is True
    assert table.consecutive_scan_failures == 0


def test_scan_health_splits_the_catalog_into_passed_corrupt_failed_and_unscanned(session):
    source = _source(session)
    _table(session, source, "ok1", scanned=True)
    _table(session, source, "ok2", scanned=True)
    _table(session, source, "new")
    _table(session, source, "gone", streak=5, failed_ago=timedelta(days=1), active=False)
    _table(session, source, "gone2", streak=3, failed_ago=timedelta(days=1))
    _table(
        session,
        source,
        "denied",
        streak=1,
        failed_ago=timedelta(hours=6),
        error="GetObject, https response error StatusCode: 403",
    )
    _table(session, _source(session, enabled=False), "ignored", scanned=True)
    session.commit()

    health = collect_scan_health(session)

    assert (health.total, health.passed, health.corrupt, health.failed, health.never_scanned) == (
        6,
        2,
        2,
        1,
        1,
    )
    assert health.retry_tiers == {"daily": 1, "weekly": 1, "every 15 days": 0, "monthly": 1}


def test_digest_renders_the_scan_health_split():
    data = DailyReportData(
        report_date=NOW,
        status="CLEAR",
        total_tables=12748,
        healthy_tables=0,
        unhealthy_tables=0,
        tables_needing_compaction=0,
        total_anomalies=0,
        scan_health=ScanHealth(
            total=12970,
            passed=11500,
            corrupt=1391,
            failed=48,
            never_scanned=31,
            retry_tiers={"daily": 37, "weekly": 0, "every 15 days": 1, "monthly": 1401},
        ),
    )

    body = str([b.to_dict() for b in _build_slack_blocks(data, "ignored")])

    for expected in ("Scan Health", "12,970", "11,500", "1,391", "48", "Not scanned yet"):
        assert expected in body
    assert "37 daily · 1 every 15 days · 1,401 monthly" in body
    assert "0 weekly" not in body
