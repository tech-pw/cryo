"""Tests for cryo/reporting/daily_report.py's fleet-health digest.

Covers the parts that don't need a live LLM provider or Slack webhook: status
classification, the deterministic bottom-line fallback, the real (never-fabricated) size
delta, action-item construction, and the Block Kit structure per
docs/SLACK_REPORT_FORMAT_GUIDE.md's 6-section framework.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryo.db.models import AuditLog, Source, Table, TableCostHistory, TableStats, TableStatsHistory
from cryo.ml.anomaly_detector import AnomalyAlert
from cryo.reporting.daily_report import (
    DailyReportData,
    _build_action_items,
    _build_slack_blocks,
    _classify_status,
    _collect_report_data,
    _deterministic_bottom_line,
    _size_snapshot_asof,
    generate_daily_report,
)


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(session, source: Source, *, name: str, owner_email: str | None = None) -> Table:
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="finance",
        table_name=name,
        is_active=True,
        owner_email=owner_email,
    )
    session.add(table)
    session.flush()
    return table


def _alert(*, table_id, table_name: str, severity: str) -> AnomalyAlert:
    return AnomalyAlert(
        table_id=str(table_id),
        table_name=table_name,
        metric_name="small_files_count",
        current_value=500.0,
        expected_value=50.0,
        deviation_sigma=6.2,
        severity=severity,
        description="small_files_count spiked to 500 (baseline 50)",
        detected_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# _classify_status
# ---------------------------------------------------------------------------


def test_classify_status_clear_when_no_anomalies_and_all_healthy() -> None:
    assert _classify_status(unhealthy_tables=0, anomalies=[]) == "CLEAR"


def test_classify_status_watch_when_unhealthy_tables_but_no_anomalies() -> None:
    assert _classify_status(unhealthy_tables=3, anomalies=[]) == "WATCH"


def test_classify_status_watch_on_medium_severity_anomaly() -> None:
    alerts = [_alert(table_id=uuid4(), table_name="finance.orders", severity="medium")]
    assert _classify_status(unhealthy_tables=0, anomalies=alerts) == "WATCH"


def test_classify_status_critical_on_high_severity_anomaly() -> None:
    alerts = [_alert(table_id=uuid4(), table_name="finance.orders", severity="high")]
    assert _classify_status(unhealthy_tables=0, anomalies=alerts) == "CRITICAL"


def test_classify_status_critical_wins_over_watch() -> None:
    alerts = [
        _alert(table_id=uuid4(), table_name="a", severity="medium"),
        _alert(table_id=uuid4(), table_name="b", severity="high"),
    ]
    assert _classify_status(unhealthy_tables=5, anomalies=alerts) == "CRITICAL"


# ---------------------------------------------------------------------------
# _deterministic_bottom_line
# ---------------------------------------------------------------------------


def test_deterministic_bottom_line_clear_says_no_action() -> None:
    data = DailyReportData(
        report_date=datetime.now(UTC),
        status="CLEAR",
        total_tables=10,
        healthy_tables=10,
        unhealthy_tables=0,
        tables_needing_compaction=0,
        total_anomalies=0,
    )
    line = _deterministic_bottom_line(data)
    assert "No action required" in line
    assert "10" in line


def test_deterministic_bottom_line_watch_reports_unhealthy_count() -> None:
    data = DailyReportData(
        report_date=datetime.now(UTC),
        status="WATCH",
        total_tables=10,
        healthy_tables=7,
        unhealthy_tables=3,
        tables_needing_compaction=2,
        total_anomalies=1,
        critical_anomalies=[{"table": "finance.orders"}],
    )
    line = _deterministic_bottom_line(data)
    assert "3 of 10" in line


def test_deterministic_bottom_line_includes_real_size_delta_only_when_known() -> None:
    data = DailyReportData(
        report_date=datetime.now(UTC),
        status="WATCH",
        total_tables=10,
        healthy_tables=7,
        unhealthy_tables=3,
        tables_needing_compaction=0,
        total_anomalies=0,
        critical_anomalies=[{"table": "x"}],
        total_size_tb_delta=1.25,
    )
    line = _deterministic_bottom_line(data)
    assert "up 1.25 TB" in line

    data_no_delta = DailyReportData(
        report_date=datetime.now(UTC),
        status="WATCH",
        total_tables=10,
        healthy_tables=7,
        unhealthy_tables=3,
        tables_needing_compaction=0,
        total_anomalies=0,
        critical_anomalies=[{"table": "x"}],
        total_size_tb_delta=None,
    )
    line_no_delta = _deterministic_bottom_line(data_no_delta)
    assert "TB" not in line_no_delta


# ---------------------------------------------------------------------------
# _size_snapshot_asof -- real delta, never fabricated
# ---------------------------------------------------------------------------


def test_size_snapshot_asof_returns_none_with_insufficient_history(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="a")
    session.add(
        TableStatsHistory(
            table_id=table.id,
            scan_at=datetime.now(UTC) - timedelta(days=1),
            total_size_gb=100.0,
        )
    )
    session.commit()

    assert _size_snapshot_asof(session, datetime.now(UTC) - timedelta(days=1)) is None


def test_size_snapshot_asof_sums_nearest_prior_row_per_table(session) -> None:
    source = _seed_source(session)
    as_of = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    for i in range(5):
        table = _seed_table(session, source, name=f"t{i}")
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=as_of - timedelta(hours=1),
                total_size_gb=1024.0,  # 1 TB each
            )
        )
        # A later row (after as_of) must not be picked.
        session.add(
            TableStatsHistory(
                table_id=table.id,
                scan_at=as_of + timedelta(hours=1),
                total_size_gb=999_999.0,
            )
        )
    session.commit()

    result = _size_snapshot_asof(session, as_of)

    assert result == 5.0  # 5 tables x 1 TB, using the pre-as_of row only


# ---------------------------------------------------------------------------
# _build_action_items
# ---------------------------------------------------------------------------


def test_build_action_items_uses_table_owner_when_set(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders", owner_email="owner@example.com")
    alerts = [_alert(table_id=table.id, table_name="finance.orders", severity="high")]

    items = _build_action_items(session, alerts, top_issues=[])

    assert len(items) == 1
    assert items[0]["owner"] == "@owner@example.com"
    assert items[0]["severity"] == "high"


def test_build_action_items_flags_missing_owner_honestly(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders", owner_email=None)
    alerts = [_alert(table_id=table.id, table_name="finance.orders", severity="medium")]

    items = _build_action_items(session, alerts, top_issues=[])

    assert items[0]["owner"] == "no owner assigned"


def test_build_action_items_falls_back_to_top_issues_when_no_anomalies(session) -> None:
    items = _build_action_items(
        session,
        [],
        top_issues=[{"table": "finance.orders", "grade": "D", "score": 42, "small_files": 900}],
    )

    assert len(items) == 1
    assert "finance.orders" in items[0]["text"]
    assert items[0]["owner"] == "no owner assigned"


def test_build_action_items_caps_at_three(session) -> None:
    source = _seed_source(session)
    alerts = [
        _alert(
            table_id=_seed_table(session, source, name=f"t{i}").id,
            table_name=f"finance.t{i}",
            severity="high",
        )
        for i in range(5)
    ]

    items = _build_action_items(session, alerts, top_issues=[])

    assert len(items) == 3


# ---------------------------------------------------------------------------
# _collect_report_data -- integration across the pieces above
# ---------------------------------------------------------------------------


def test_collect_report_data_clear_has_no_action_items(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=95,
            total_files=10,
            small_files_count=0,
            total_size_gb=5.0,
        )
    )
    session.commit()

    data = _collect_report_data(session)

    assert data.status == "CLEAR"
    assert data.action_items == []


def test_collect_report_data_watch_when_unhealthy_table_present(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="F",
            overall_score=20,
            total_files=900,
            small_files_count=850,
            total_size_gb=5.0,
        )
    )
    session.commit()

    data = _collect_report_data(session)

    assert data.status == "WATCH"
    assert data.unhealthy_tables == 1
    assert data.tables_needing_compaction == 1


def test_collect_report_data_unexplained_cost_spike_forces_watch(session) -> None:
    """An unexplained fleet-cost spike must surface even when every table itself looks
    healthy -- the cost signal is a fleet-wide check, not a table-health one, and the two
    shouldn't be able to mask each other."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=95,
            total_files=10,
            small_files_count=0,
            total_size_gb=5.0,
        )
    )
    now = datetime.now(UTC)
    session.add(
        TableCostHistory(
            table_id=table.id,
            scan_at=now - timedelta(days=1, hours=1),
            current_monthly_cost_usd=200.0,
        )
    )
    session.add(
        TableCostHistory(
            table_id=table.id,
            scan_at=now - timedelta(days=2, hours=1),
            current_monthly_cost_usd=100.0,
        )
    )
    session.commit()

    data = _collect_report_data(session)

    assert data.status == "WATCH"
    assert data.cost_signal is not None
    assert data.cost_signal.direction == "spike"
    assert data.cost_correlation is not None
    assert data.cost_correlation.explains_change is False
    assert any("Fleet cost spike" in item["text"] for item in data.action_items)


def test_collect_report_data_explained_cost_spike_does_not_force_watch(session) -> None:
    """A spike explained by Cryo's own recent rewrite activity shouldn't force WATCH status
    on an otherwise-healthy fleet -- it's still surfaced, just not as an open question."""
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=95,
            total_files=10,
            small_files_count=0,
            total_size_gb=5.0,
        )
    )
    now = datetime.now(UTC)
    session.add(
        TableCostHistory(
            table_id=table.id,
            scan_at=now - timedelta(days=1, hours=1),
            current_monthly_cost_usd=200.0,
        )
    )
    session.add(
        TableCostHistory(
            table_id=table.id,
            scan_at=now - timedelta(days=2, hours=1),
            current_monthly_cost_usd=100.0,
        )
    )
    session.add(
        AuditLog(
            ts=now - timedelta(hours=6),
            table_id=table.id,
            action="optimize",
            status="OK",
            actual_bytes_freed=1024**3,
        )
    )
    session.commit()

    data = _collect_report_data(session)

    assert data.status == "CLEAR"
    assert data.cost_signal is not None
    assert data.cost_signal.direction == "spike"
    assert data.cost_correlation is not None
    assert data.cost_correlation.explains_change is True


# ---------------------------------------------------------------------------
# generate_daily_report -- end to end without a live LLM provider or Slack
# ---------------------------------------------------------------------------


def test_generate_daily_report_uses_deterministic_fallback_without_ai_provider(session) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source, name="orders")
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="A",
            overall_score=95,
            total_files=10,
            small_files_count=0,
            total_size_gb=5.0,
        )
    )
    session.commit()

    result = asyncio.run(generate_daily_report(session, slack_client=None, use_ai_summary=True))

    assert result["status"] == "CLEAR"
    assert result["bottom_line_used_llm"] is False
    assert "No action required" in result["bottom_line"]
    assert "slack_sent" not in result


# ---------------------------------------------------------------------------
# _build_slack_blocks -- structure per docs/SLACK_REPORT_FORMAT_GUIDE.md
# ---------------------------------------------------------------------------


def test_build_slack_blocks_clear_status_shows_no_action_required() -> None:
    data = DailyReportData(
        report_date=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
        status="CLEAR",
        total_tables=5,
        healthy_tables=5,
        unhealthy_tables=0,
        tables_needing_compaction=0,
        total_anomalies=0,
    )

    blocks = _build_slack_blocks(data, "All clear.")
    rendered = [b.to_dict() for b in blocks]

    assert rendered[0]["type"] == "header"
    assert "July 09, 2026" in rendered[0]["text"]["text"]
    status_block = next(b for b in rendered if b["type"] == "section" and "CLEAR" in str(b))
    assert "🟢" in status_block["text"]["text"]
    action_block = next(b for b in rendered if "Action Items" in str(b))
    assert "No action required" in action_block["text"]["text"]


def test_build_slack_blocks_critical_status_lists_action_items() -> None:
    data = DailyReportData(
        report_date=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
        status="CRITICAL",
        total_tables=5,
        healthy_tables=3,
        unhealthy_tables=2,
        tables_needing_compaction=1,
        total_anomalies=1,
        critical_anomalies=[{"table": "finance.orders"}],
        action_items=[
            {
                "severity": "high",
                "text": "`finance.orders` — null-rate spike",
                "owner": "@owner@example.com",
            }
        ],
    )

    blocks = _build_slack_blocks(data, "Orders table degraded.")
    rendered = [b.to_dict() for b in blocks]

    status_block = next(b for b in rendered if b["type"] == "section" and "CRITICAL" in str(b))
    assert "🔴" in status_block["text"]["text"]
    action_table = next(b for b in rendered if b["type"] == "table" and "null-rate spike" in str(b))
    assert "@owner@example.com" in str(action_table)


def test_build_slack_blocks_always_includes_fallback_footer() -> None:
    data = DailyReportData(
        report_date=datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
        status="CLEAR",
        total_tables=1,
        healthy_tables=1,
        unhealthy_tables=0,
        tables_needing_compaction=0,
        total_anomalies=0,
    )

    blocks = _build_slack_blocks(data, "All clear.")

    assert blocks[-1].type == "context"
