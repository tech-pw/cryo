"""Daily fleet-health report generator with AI narration.

Follows the structure in ``docs/SLACK_REPORT_FORMAT_GUIDE.md``: header, a CLEAR/WATCH/CRITICAL
status line (answers "do I need to act?" in one glance), a 1-2 sentence bottom line, 4-8 key
metrics, up to 3 owned action items, and a footer. Every number in the message is computed here
in SQL/Python -- the LLM (if configured) only narrates the bottom line, never invents figures.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.ai.runtime import LLMInvokeContext, complete_with_budget_and_observation
from cryo.db.models import AIProvider, AuditLog, Table, TableStats, TableStatsHistory
from cryo.ml.anomaly_detector import AnomalyAlert, detect_all_anomalies
from cryo.reporting.slack import (
    SlackBlock,
    SlackClient,
    context_block,
    divider_block,
    header_block,
    section_block,
    table_block,
)

logger = logging.getLogger(__name__)

ReportStatus = Literal["CLEAR", "WATCH", "CRITICAL"]

REPORT_SYSTEM_PROMPT = """You are Cryo's daily report narrator. Write the "bottom line" for a
datalake health digest: exactly 1-2 sentences, no more.

Rules:
- Sentence 1: what happened (the headline fact).
- Sentence 2 (optional): why it happened, if a cause is evident from the data given.
- Every number you use MUST come from the data provided -- never invent or estimate a figure.
- Plain language, no jargon, no markdown headers.
- If nothing notable happened, say so plainly rather than padding."""


@dataclass
class DailyReportData:
    """Data collected for the daily report."""

    report_date: datetime
    status: ReportStatus
    total_tables: int
    healthy_tables: int
    unhealthy_tables: int
    tables_needing_compaction: int
    total_anomalies: int
    critical_anomalies: list[dict[str, Any]] = field(default_factory=list)
    grade_distribution: dict[str, int] = field(default_factory=dict)
    maintenance_stats: dict[str, int] = field(default_factory=dict)
    top_issues: list[dict[str, Any]] = field(default_factory=list)
    total_size_tb: float = 0.0
    total_size_tb_delta: float | None = None
    total_cost_usd: float = 0.0
    projected_savings_usd: float = 0.0
    action_items: list[dict[str, Any]] = field(default_factory=list)


async def generate_daily_report(
    session: Session,
    slack_client: SlackClient | None = None,
    *,
    use_ai_summary: bool = True,
) -> dict[str, Any]:
    """Generate and optionally send the daily fleet-health report.

    Args:
        session: Database session
        slack_client: Slack client for sending (optional)
        use_ai_summary: Whether to generate the AI-narrated bottom line

    Returns:
        Report data dictionary
    """
    logger.info("Generating daily fleet-health report")

    report_data = _collect_report_data(session)

    bottom_line = None
    if use_ai_summary:
        bottom_line = await _generate_bottom_line(session, report_data)
    narrated_bottom_line = bottom_line or _deterministic_bottom_line(report_data)

    result: dict[str, Any] = {
        "report_date": report_data.report_date.isoformat(),
        "status": report_data.status,
        "summary": {
            "total_tables": report_data.total_tables,
            "healthy_tables": report_data.healthy_tables,
            "unhealthy_tables": report_data.unhealthy_tables,
            "tables_needing_compaction": report_data.tables_needing_compaction,
            "total_anomalies": report_data.total_anomalies,
            "critical_anomalies": len(report_data.critical_anomalies),
        },
        "grade_distribution": report_data.grade_distribution,
        "maintenance_stats": report_data.maintenance_stats,
        "top_issues": report_data.top_issues[:10],
        "critical_anomalies": report_data.critical_anomalies[:5],
        "action_items": report_data.action_items,
        "cost": {
            "total_size_tb": report_data.total_size_tb,
            "total_size_tb_delta": report_data.total_size_tb_delta,
            "total_cost_usd": report_data.total_cost_usd,
            "projected_savings_usd": report_data.projected_savings_usd,
        },
        "bottom_line": narrated_bottom_line,
        "bottom_line_used_llm": bottom_line is not None,
    }

    if slack_client:
        blocks = _build_slack_blocks(report_data, narrated_bottom_line)
        status_emoji = {"CLEAR": "🟢", "WATCH": "🟡", "CRITICAL": "🔴"}[report_data.status]
        slack_sent = await slack_client.send_blocks(
            blocks,
            fallback_text=f"{status_emoji} Cryo Daily Report — {report_data.report_date.date()}",
        )
        result["slack_sent"] = slack_sent

    from cryo.reporting.email import send_report_email

    email_sent = send_report_email(
        f"Cryo Daily Report — {report_data.report_date.date()}",
        f"<pre>{narrated_bottom_line}</pre>",
    )
    if email_sent:
        result["email_sent"] = True

    return result


def _deterministic_bottom_line(data: DailyReportData) -> str:
    if data.status == "CLEAR":
        return f"All {data.total_tables} tables healthy, no anomalies detected. No action required."
    delta_str = ""
    if data.total_size_tb_delta is not None:
        direction = "up" if data.total_size_tb_delta >= 0 else "down"
        delta_str = f" Fleet size {direction} {abs(data.total_size_tb_delta):.2f} TB vs yesterday."
    noun = "anomaly" if len(data.critical_anomalies) == 1 else "anomalies"
    return (
        f"{data.unhealthy_tables} of {data.total_tables} tables need attention "
        f"({len(data.critical_anomalies)} {noun} flagged).{delta_str}"
    )


def _classify_status(unhealthy_tables: int, anomalies: list[AnomalyAlert]) -> ReportStatus:
    if any(a.severity == "high" for a in anomalies):
        return "CRITICAL"
    if unhealthy_tables > 0 or any(a.severity == "medium" for a in anomalies):
        return "WATCH"
    return "CLEAR"


def _size_snapshot_asof(session: Session, as_of: datetime) -> float | None:
    """Fleet total_size_gb as of the nearest prior scan per table to ``as_of`` -- used only to
    compute a real day-over-day size delta. Returns None (no delta shown, never fabricated) if
    there isn't enough history yet for the comparison to mean anything."""
    subq = (
        select(
            TableStatsHistory.table_id,
            func.max(TableStatsHistory.scan_at).label("scan_at"),
        )
        .where(TableStatsHistory.scan_at <= as_of)
        .group_by(TableStatsHistory.table_id)
        .subquery()
    )
    rows = session.execute(
        select(TableStatsHistory.total_size_gb).join(
            subq,
            (TableStatsHistory.table_id == subq.c.table_id)
            & (TableStatsHistory.scan_at == subq.c.scan_at),
        )
    ).fetchall()
    if len(rows) < 5:
        return None
    return sum(float(r[0] or 0) for r in rows) / 1024


def _build_action_items(
    session: Session, anomalies: list[AnomalyAlert], top_issues: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Up to 3 owned action items -- the highest-severity anomalies first, falling back to the
    worst-scoring tables if no anomalies fired. An item with no configured owner still appears
    (dropping it would hide a real problem), but says so plainly rather than inventing one."""
    items: list[dict[str, Any]] = []
    for anomaly in anomalies[:3]:
        table = session.get(Table, uuid.UUID(anomaly.table_id))
        owner = f"@{table.owner_email}" if table and table.owner_email else "no owner assigned"
        items.append(
            {
                "severity": anomaly.severity,
                "text": f"`{anomaly.table_name}` — {anomaly.description}",
                "owner": owner,
            }
        )
    if not items:
        for issue in top_issues[:3]:
            items.append(
                {
                    "severity": "medium",
                    "text": f"`{issue['table']}` — Grade {issue['grade']}, score {issue['score']}",
                    "owner": "no owner assigned",
                }
            )
    return items


def _collect_report_data(session: Session) -> DailyReportData:
    """Collect all data needed for the report."""
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)

    total_tables = (
        session.scalar(select(func.count(Table.id)).where(Table.is_active.is_(True))) or 0
    )

    grade_rows = session.execute(
        select(TableStats.grade, func.count(TableStats.table_id))
        .where(TableStats.grade.isnot(None))
        .group_by(TableStats.grade)
    ).fetchall()
    grade_distribution = {row[0]: row[1] for row in grade_rows}

    healthy = sum(grade_distribution.get(g, 0) for g in ["A", "B"])
    unhealthy = sum(grade_distribution.get(g, 0) for g in ["D", "E", "F"])

    compaction_count = (
        session.scalar(
            select(func.count(TableStats.table_id)).where(TableStats.needs_compaction.is_(True))
        )
        or 0
    )

    # Richer, seasonality-aware anomaly detection -- the same detector already proven out for
    # per-table Slack breach alerts (cryo/scheduler/alert_dispatcher.py), fleet-wide.
    anomaly_alerts = detect_all_anomalies(session, min_severity="medium", lookback_days=7)
    critical_anomalies = [
        {
            "table": a.table_name,
            "metric": a.metric_name,
            "value": a.current_value,
            "baseline": a.expected_value,
            "severity": a.severity,
            "z_score": a.deviation_sigma,
            "description": a.description,
        }
        for a in anomaly_alerts
    ]

    maintenance_rows = session.execute(
        select(AuditLog.action, AuditLog.status, func.count(AuditLog.id))
        .where(AuditLog.ts >= yesterday)
        .group_by(AuditLog.action, AuditLog.status)
    ).fetchall()
    maintenance_stats: dict[str, int] = {
        f"{action}_{status}": count for action, status, count in maintenance_rows
    }

    top_issues_rows = session.execute(
        select(
            Table.schema_name,
            Table.table_name,
            TableStats.grade,
            TableStats.overall_score,
            TableStats.small_files_count,
        )
        .join(TableStats, TableStats.table_id == Table.id)
        .where(TableStats.overall_score < 60)
        .order_by(TableStats.overall_score.asc())
        .limit(10)
    ).fetchall()
    top_issues = [
        {"table": f"{row[0]}.{row[1]}", "grade": row[2], "score": row[3], "small_files": row[4]}
        for row in top_issues_rows
    ]

    cost_stats = session.execute(
        select(
            func.sum(TableStats.total_size_gb),
            func.sum(TableStats.estimated_monthly_s3_cost_usd),
            func.sum(TableStats.projected_savings_usd_month),
        )
    ).first()
    total_size_gb = float(cost_stats[0] or 0.0) if cost_stats is not None else 0.0
    total_cost = float(cost_stats[1] or 0.0) if cost_stats is not None else 0.0
    projected_savings = float(cost_stats[2] or 0.0) if cost_stats is not None else 0.0
    total_size_tb = total_size_gb / 1024

    size_yesterday_tb = _size_snapshot_asof(session, yesterday)
    size_delta = (total_size_tb - size_yesterday_tb) if size_yesterday_tb is not None else None

    status = _classify_status(unhealthy, anomaly_alerts)
    action_items = (
        _build_action_items(session, anomaly_alerts, top_issues) if status != "CLEAR" else []
    )

    return DailyReportData(
        report_date=now,
        status=status,
        total_tables=total_tables,
        healthy_tables=healthy,
        unhealthy_tables=unhealthy,
        tables_needing_compaction=compaction_count,
        total_anomalies=len(anomaly_alerts),
        critical_anomalies=critical_anomalies,
        grade_distribution=grade_distribution,
        maintenance_stats=maintenance_stats,
        top_issues=top_issues,
        total_size_tb=total_size_tb,
        total_size_tb_delta=size_delta,
        total_cost_usd=total_cost,
        projected_savings_usd=projected_savings,
        action_items=action_items,
    )


async def _generate_bottom_line(session: Session, data: DailyReportData) -> str | None:
    """Generate the 1-2 sentence AI-narrated bottom line. Never lets the LLM invent numbers --
    only the pre-computed context below is available to narrate."""
    provider = session.scalar(
        select(AIProvider).where(AIProvider.is_default.is_(True), AIProvider.enabled.is_(True))
    )
    if provider is None:
        provider = session.scalar(select(AIProvider).where(AIProvider.enabled.is_(True)).limit(1))
    if provider is None or not provider.api_key_encrypted:
        logger.info("No AI provider configured, skipping AI bottom line")
        return None

    context = {
        "status": data.status,
        "total_tables": data.total_tables,
        "healthy_tables": data.healthy_tables,
        "unhealthy_tables": data.unhealthy_tables,
        "tables_needing_compaction": data.tables_needing_compaction,
        "total_anomalies": data.total_anomalies,
        "top_anomalies": data.critical_anomalies[:3],
        "total_size_tb": round(data.total_size_tb, 2),
        "total_size_tb_delta": (
            round(data.total_size_tb_delta, 2) if data.total_size_tb_delta is not None else None
        ),
        "monthly_cost_usd": round(data.total_cost_usd, 2),
        "maintenance_last_24h": data.maintenance_stats,
    }

    prompt = f"Write the bottom line for today's datalake health digest.\n\nData:\n{context}"

    try:
        response, _observation = await complete_with_budget_and_observation(
            session,
            prompt,
            invoke=LLMInvokeContext(
                feature="daily_digest_narration",
                prompt_name="daily_report_bottom_line",
                extra={"report_date": data.report_date.date().isoformat()},
            ),
            system=REPORT_SYSTEM_PROMPT,
            max_tokens=120,
        )
        if response is None:
            return None
        return response.content
    except Exception as e:
        logger.exception(f"Failed to generate AI bottom line: {e}")
        return None


def _build_slack_blocks(data: DailyReportData, bottom_line: str) -> list[SlackBlock]:
    """Build Slack Block Kit blocks per docs/SLACK_REPORT_FORMAT_GUIDE.md's 6-section framework:
    header, status, bottom line, key metrics, action items, footer."""
    blocks: list[SlackBlock] = []

    date_str = data.report_date.strftime("%B %d, %Y")
    blocks.append(header_block(f"🧊 Cryo Daily Report — {date_str}"))

    status_emoji = {"CLEAR": "🟢", "WATCH": "🟡", "CRITICAL": "🔴"}[data.status]
    status_label = (
        f"{status_emoji} *{data.status}*"
        if data.status == "CLEAR"
        else f"{status_emoji} *{data.status}* ({len(data.action_items)} item"
        f"{'s' if len(data.action_items) != 1 else ''})"
    )
    blocks.append(section_block(status_label))
    blocks.append(section_block(bottom_line))
    blocks.append(divider_block())

    size_delta_str = ""
    if data.total_size_tb_delta is not None:
        arrow = "↑" if data.total_size_tb_delta >= 0 else "↓"
        size_delta_str = f" {arrow}{abs(data.total_size_tb_delta):.2f}"
    blocks.append(
        table_block(
            ["Metric", "Value"],
            [
                ["📊 Total Tables", str(data.total_tables)],
                ["💚 Health", f"{data.healthy_tables} ✓ / {data.unhealthy_tables} ✗"],
                ["🔧 Needing Compaction", str(data.tables_needing_compaction)],
                [
                    "⚠️ Anomalies",
                    f"{data.total_anomalies} ({len(data.critical_anomalies)} flagged)",
                ],
                ["💾 Total Size", f"{data.total_size_tb:.2f} TB{size_delta_str}"],
                ["💰 Monthly S3 Cost", f"${data.total_cost_usd:,.2f}"],
                ["💵 Projected Savings", f"${data.projected_savings_usd:,.2f}/mo"],
            ],
        )
    )

    if data.grade_distribution:
        blocks.append(divider_block())
        blocks.append(section_block("*Grade Distribution*"))
        blocks.append(
            table_block(
                ["Grade", "Tables"],
                [[grade, str(count)] for grade, count in sorted(data.grade_distribution.items())],
            )
        )

    if data.top_issues:
        blocks.append(divider_block())
        blocks.append(section_block("*Top Unhealthy Tables*"))
        blocks.append(
            table_block(
                ["Table", "Grade", "Score", "Small Files"],
                [
                    [issue["table"], issue["grade"], str(issue["score"]), str(issue["small_files"])]
                    for issue in data.top_issues[:5]
                ],
            )
        )

    if data.maintenance_stats:
        succeeded = sum(v for k, v in data.maintenance_stats.items() if "succeeded" in k)
        failed = sum(v for k, v in data.maintenance_stats.items() if "failed" in k)
        blocks.append(divider_block())
        blocks.append(section_block(f"*Maintenance (24h):* {succeeded} succeeded, {failed} failed"))

    blocks.append(divider_block())
    if data.action_items:
        severity_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        blocks.append(section_block("*Action Items*"))
        blocks.append(
            table_block(
                ["Severity", "Issue", "Owner"],
                [
                    [severity_emoji.get(item["severity"], "🟡"), item["text"], item["owner"]]
                    for item in data.action_items
                ],
            )
        )
    else:
        blocks.append(section_block("*Action Items*\nNo action required."))

    blocks.append(divider_block())
    blocks.append(context_block([f"Generated by Cryo at {data.report_date.strftime('%H:%M UTC')}"]))

    return blocks
