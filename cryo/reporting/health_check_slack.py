"""Slack Block Kit payload for the daily production health checklist
(cryo.reporting.health_check). Separate message from both daily_report.py's north-star metric
and query_cost_report.py's cost digest -- same reasoning as those two: a distinct question
("is anything structurally broken") deserves its own message, not another section bolted onto
an already-dense digest a reader has to parse to find the one line that matters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from cryo.reporting.health_check import CheckResult, FleetFileTrend, ProductionHealthReport
from cryo.reporting.slack import (
    SlackBlock,
    context_block,
    divider_block,
    header_block,
    section_block,
    table_block,
)

_STATUS_EMOJI = {"OK": "✅", "WATCH": "🟡", "CRITICAL": "🔴"}
_SEVERITY_EMOJI = {"critical": "🔴", "warning": "🟡", "info": "✅"}


def _fmt_files(n: int) -> str:
    return f"{n:,}"


def _fmt_pct(v: float | None, *, signed: bool = False) -> str:
    if v is None:
        return "n/a"
    fmt = "{:+.1f}%" if signed else "{:.1f}%"
    return fmt.format(v)


def _fmt_mb(v: float | None) -> str:
    return f"{v:.2f} MB" if v is not None else "n/a"


def _file_trend_block(trend: FleetFileTrend) -> SlackBlock:
    """The explicit day-over-day file-count/small-file/avg-size table -- shows absolute
    numbers for both days plus the delta, not just a bare percentage, per the same clarity
    standard the north-star report's DoD/WoW table already applies (see daily_report.py's
    2026-08-19 fix after user feedback that bare percentages weren't legible without a
    baseline number alongside them)."""
    today_ratio = trend.today_small_file_ratio_pct
    yesterday_ratio = trend.yesterday_small_file_ratio_pct
    ratio_delta = trend.small_file_ratio_delta_pct
    size_delta = trend.avg_file_size_delta_mb

    files_delta_str = (
        f"{trend.total_files_delta:+,}" if trend.total_files_delta is not None else "n/a"
    )
    small_delta_str = (
        f"{trend.small_files_delta:+,}" if trend.small_files_delta is not None else "n/a"
    )
    ratio_delta_str = _fmt_pct(ratio_delta, signed=True) if ratio_delta is not None else "n/a"
    size_delta_str = f"{size_delta:+.2f} MB" if size_delta is not None else "n/a"

    return table_block(
        ["Metric", "Yesterday", "Today", "Change"],
        [
            [
                "Total files",
                _fmt_files(trend.yesterday_total_files or 0)
                if trend.yesterday_total_files is not None
                else "n/a",
                _fmt_files(trend.today_total_files),
                files_delta_str,
            ],
            [
                "Small files",
                _fmt_files(trend.yesterday_small_files or 0)
                if trend.yesterday_small_files is not None
                else "n/a",
                _fmt_files(trend.today_small_files),
                small_delta_str,
            ],
            [
                "Small-file ratio",
                _fmt_pct(yesterday_ratio),
                _fmt_pct(today_ratio),
                ratio_delta_str,
            ],
            [
                "Avg file size",
                _fmt_mb(trend.yesterday_avg_file_size_mb),
                _fmt_mb(trend.today_avg_file_size_mb),
                size_delta_str,
            ],
        ],
    )


def _check_row(check: CheckResult) -> list[str]:
    emoji = _SEVERITY_EMOJI.get(check.severity, "•") if not check.passed else "✅"
    return [f"{emoji} {check.name}", check.summary]


def build_health_check_slack_blocks(
    report: ProductionHealthReport,
    *,
    generated_at: datetime,
) -> list[SlackBlock]:
    date_str = generated_at.strftime("%B %d, %Y")
    status_emoji = _STATUS_EMOJI.get(report.overall_status, "❓")
    blocks: list[SlackBlock] = [
        header_block(f"{status_emoji} Production Health Check — {date_str}")
    ]

    if report.overall_status == "OK":
        headline = "✅ *All checks passed* — no structural issues detected"
    else:
        parts = []
        if report.critical_count:
            parts.append(f"{report.critical_count} critical")
        if report.warning_count:
            parts.append(f"{report.warning_count} warning")
        headline = f"{status_emoji} *{report.overall_status}* — {', '.join(parts)}"
    blocks.append(section_block(headline))

    failed = [c for c in report.checks if not c.passed]
    if failed:
        blocks.append(divider_block())
        blocks.append(section_block("*Failed checks*"))
        for check in sorted(failed, key=lambda c: {"critical": 0, "warning": 1}.get(c.severity, 2)):
            emoji = _SEVERITY_EMOJI.get(check.severity, "•")
            text = f"{emoji} *{check.name}*: {check.summary}"
            if check.detail:
                text += f"\n>{check.detail}"
            blocks.append(section_block(text))

    blocks.append(divider_block())
    blocks.append(section_block("*Fleet file-count trend*"))
    blocks.append(_file_trend_block(report.file_trend))

    passed = [c for c in report.checks if c.passed]
    if passed:
        blocks.append(divider_block())
        blocks.append(
            table_block(["Check", "Result"], [_check_row(c) for c in passed])
        )

    blocks.append(divider_block())
    blocks.append(
        context_block([f"Generated by Cryo at {generated_at.strftime('%H:%M UTC')}"])
    )
    return blocks


def health_check_fallback_text(report: ProductionHealthReport, generated_at: datetime) -> str:
    date_str = generated_at.strftime("%B %d, %Y")
    if report.overall_status == "OK":
        return f"Production Health Check — {date_str}: all checks passed."
    return (
        f"Production Health Check — {date_str}: {report.overall_status} "
        f"({report.critical_count} critical, {report.warning_count} warning)"
    )


def health_check_to_payload(report: ProductionHealthReport) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at.isoformat(),
        "overall_status": report.overall_status,
        "critical_count": report.critical_count,
        "warning_count": report.warning_count,
        "checks": [
            {
                "name": c.name,
                "passed": c.passed,
                "severity": c.severity,
                "summary": c.summary,
                "detail": c.detail,
                "evidence": c.evidence,
            }
            for c in report.checks
        ],
        "file_trend": {
            "today_total_files": report.file_trend.today_total_files,
            "today_small_files": report.file_trend.today_small_files,
            "today_small_file_ratio_pct": report.file_trend.today_small_file_ratio_pct,
            "today_avg_file_size_mb": report.file_trend.today_avg_file_size_mb,
            "yesterday_total_files": report.file_trend.yesterday_total_files,
            "yesterday_small_files": report.file_trend.yesterday_small_files,
            "yesterday_small_file_ratio_pct": report.file_trend.yesterday_small_file_ratio_pct,
            "yesterday_avg_file_size_mb": report.file_trend.yesterday_avg_file_size_mb,
            "total_files_delta": report.file_trend.total_files_delta,
            "small_files_delta": report.file_trend.small_files_delta,
            "small_file_ratio_delta_pct": report.file_trend.small_file_ratio_delta_pct,
            "avg_file_size_delta_mb": report.file_trend.avg_file_size_delta_mb,
            "is_worsening": report.file_trend.is_worsening,
            "tables_counted": report.file_trend.tables_counted,
        },
    }
