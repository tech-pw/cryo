"""Tests for cryo/reporting/health_check_slack.py's Block Kit rendering."""

from __future__ import annotations

from datetime import UTC, datetime

from cryo.reporting.health_check import CheckResult, FleetFileTrend, ProductionHealthReport
from cryo.reporting.health_check_slack import (
    build_health_check_slack_blocks,
    health_check_fallback_text,
    health_check_to_payload,
)

NOW = datetime(2026, 8, 19, 9, 45, tzinfo=UTC)

_TREND = FleetFileTrend(
    today_total_files=3_132_768,
    today_small_files=535_898,
    today_total_size_gb=211_036.3,
    today_avg_file_size_mb=69.0,
    yesterday_total_files=3_131_222,
    yesterday_small_files=534_976,
    yesterday_total_size_gb=210_956.0,
    yesterday_avg_file_size_mb=69.0,
    tables_counted=12_392,
)


def _all_pass_report() -> ProductionHealthReport:
    checks = [
        CheckResult(name="stuck_runs", passed=True, severity="info", summary="No stuck runs"),
        CheckResult(name="stale_jobs", passed=True, severity="info", summary="No stale jobs"),
    ]
    return ProductionHealthReport(generated_at=NOW, checks=checks, file_trend=_TREND)


def _with_failures_report() -> ProductionHealthReport:
    checks = [
        CheckResult(
            name="stuck_runs",
            passed=False,
            severity="critical",
            summary="1 run(s) stuck open >12h (oldest 120h)",
            detail="Check cryo.jobs for non-terminal jobs.",
            evidence={"count": 1},
        ),
        CheckResult(
            name="queue_growth",
            passed=False,
            severity="warning",
            summary="Queue depth grew +40% to 1400",
        ),
        CheckResult(name="stale_jobs", passed=True, severity="info", summary="No stale jobs"),
    ]
    return ProductionHealthReport(generated_at=NOW, checks=checks, file_trend=_TREND)


def _block_texts(blocks) -> list[str]:
    texts = []
    for b in blocks:
        if b.text:
            texts.append(b.text.get("text", ""))
    return texts


def test_all_pass_renders_ok_headline() -> None:
    blocks = build_health_check_slack_blocks(_all_pass_report(), generated_at=NOW)
    texts = " ".join(_block_texts(blocks))
    assert "All checks passed" in texts


def test_failures_render_critical_headline_and_detail() -> None:
    report = _with_failures_report()
    blocks = build_health_check_slack_blocks(report, generated_at=NOW)
    texts = " ".join(_block_texts(blocks))
    assert "CRITICAL" in texts
    assert "stuck_runs" in texts
    assert "queue_growth" in texts
    assert "Check cryo.jobs for non-terminal jobs." in texts


def test_passed_checks_render_in_a_table() -> None:
    report = _with_failures_report()
    blocks = build_health_check_slack_blocks(report, generated_at=NOW)
    table_blocks = [b for b in blocks if b.type == "table"]
    assert table_blocks
    # One of the tables is the passed-checks summary -- find it by row count matching passed checks.
    passed_names = {c.name for c in report.checks if c.passed}
    found = False
    for tb in table_blocks:
        row_texts = {
            row[0]["elements"][0]["elements"][0]["text"] for row in tb.rows[1:] if row[0].get("elements")
        }
        if any(name in " ".join(row_texts) for name in passed_names):
            found = True
    assert found


def test_file_trend_table_present_with_real_numbers() -> None:
    blocks = build_health_check_slack_blocks(_all_pass_report(), generated_at=NOW)
    table_blocks = [b for b in blocks if b.type == "table"]
    assert any(
        row[0]["elements"][0]["elements"][0]["text"] == "Total files"
        for tb in table_blocks
        for row in tb.rows
        if row and row[0].get("elements")
    )


def test_fallback_text_ok() -> None:
    text = health_check_fallback_text(_all_pass_report(), NOW)
    assert "all checks passed" in text


def test_fallback_text_with_failures() -> None:
    text = health_check_fallback_text(_with_failures_report(), NOW)
    assert "CRITICAL" in text
    assert "1 critical" in text
    assert "1 warning" in text


def test_payload_includes_file_trend_and_checks() -> None:
    payload = health_check_to_payload(_with_failures_report())
    assert payload["overall_status"] == "CRITICAL"
    assert payload["critical_count"] == 1
    assert payload["warning_count"] == 1
    assert len(payload["checks"]) == 3
    assert payload["file_trend"]["today_total_files"] == 3_132_768
    assert payload["file_trend"]["total_files_delta"] == 1546
