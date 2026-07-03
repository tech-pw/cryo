"""Daily anomaly report generator with AI summary."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.ai.anomaly import Anomaly, detect_anomalies
from cryo.ai.llm import create_llm_client
from cryo.db.models import AIProvider, AuditLog, Table, TableStats, TableStatsHistory
from cryo.reporting.slack import (
    SlackBlock,
    SlackClient,
    context_block,
    divider_block,
    fields_block,
    header_block,
    section_block,
)

logger = logging.getLogger(__name__)

REPORT_SYSTEM_PROMPT = """You are Cryo's daily report summarizer. Generate a concise
executive summary of the datalake health report.

Guidelines:
- Be concise (2-3 paragraphs max)
- Highlight critical issues first
- Mention positive trends if any
- Use plain language, avoid jargon
- Include specific numbers when relevant
- End with recommended actions if needed

Format the response as plain text suitable for Slack (no markdown headers, just paragraphs)."""


@dataclass
class DailyReportData:
    """Data collected for the daily report."""

    report_date: datetime
    total_tables: int
    healthy_tables: int
    unhealthy_tables: int
    tables_needing_compaction: int
    total_anomalies: int
    critical_anomalies: list[dict[str, Any]]
    grade_distribution: dict[str, int]
    maintenance_stats: dict[str, int]
    top_issues: list[dict[str, Any]]
    total_size_tb: float
    total_cost_usd: float
    projected_savings_usd: float


async def generate_daily_report(
    session: Session,
    slack_client: SlackClient | None = None,
    *,
    use_ai_summary: bool = True,
) -> dict[str, Any]:
    """Generate and optionally send the daily anomaly report.

    Args:
        session: Database session
        slack_client: Slack client for sending (optional)
        use_ai_summary: Whether to generate AI summary

    Returns:
        Report data dictionary
    """
    logger.info("Generating daily anomaly report")

    # Collect report data
    report_data = _collect_report_data(session)

    # Generate AI summary if enabled and provider available
    ai_summary = None
    if use_ai_summary:
        ai_summary = await _generate_ai_summary(session, report_data)

    # Build report result
    result: dict[str, Any] = {
        "report_date": report_data.report_date.isoformat(),
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
        "cost": {
            "total_size_tb": report_data.total_size_tb,
            "total_cost_usd": report_data.total_cost_usd,
            "projected_savings_usd": report_data.projected_savings_usd,
        },
        "ai_summary": ai_summary,
    }

    # Send to Slack if client provided
    if slack_client:
        blocks = _build_slack_blocks(report_data, ai_summary)
        await slack_client.send_blocks(
            blocks,
            fallback_text=f"Cryo Daily Report - {report_data.report_date.date()}",
        )
        result["slack_sent"] = True

    return result


def _collect_report_data(session: Session) -> DailyReportData:
    """Collect all data needed for the report."""
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)

    # Table counts
    total_tables = (
        session.scalar(select(func.count(Table.id)).where(Table.is_active.is_(True))) or 0
    )

    # Health distribution
    grade_rows = session.execute(
        select(TableStats.grade, func.count(TableStats.table_id))
        .where(TableStats.grade.isnot(None))
        .group_by(TableStats.grade)
    ).fetchall()
    grade_distribution = {row[0]: row[1] for row in grade_rows}

    healthy = sum(grade_distribution.get(g, 0) for g in ["A", "B"])
    unhealthy = sum(grade_distribution.get(g, 0) for g in ["D", "E", "F"])

    # Compaction needs
    compaction_count = (
        session.scalar(
            select(func.count(TableStats.table_id)).where(TableStats.needs_compaction.is_(True))
        )
        or 0
    )

    # Collect anomalies from recent history
    all_anomalies: list[Anomaly] = []
    critical_anomalies: list[dict[str, Any]] = []

    # Get tables with recent stats
    tables_with_history = session.execute(
        select(Table.id, Table.schema_name, Table.table_name)
        .where(Table.is_active.is_(True))
        .limit(500)
    ).fetchall()

    for table_id, schema_name, table_name in tables_with_history:
        history = session.scalars(
            select(TableStatsHistory)
            .where(TableStatsHistory.table_id == table_id)
            .order_by(TableStatsHistory.scan_at)
            .limit(30)
        ).all()

        if len(history) < 3:
            continue

        rows = []
        for row in history:
            raw = dict(row.raw or {})
            rows.append(
                {
                    "scan_at": row.scan_at.isoformat(),
                    "small_files_count": row.small_files_count,
                    "total_files": row.total_files,
                    "total_size_gb": row.total_size_gb,
                    "overall_score": row.overall_score,
                    "total_snapshots": raw.get("total_snapshots"),
                    "total_delete_files": raw.get("total_delete_files"),
                    "manifest_count": raw.get("manifest_count"),
                }
            )

        anomalies = detect_anomalies(
            rows,
            metrics=(
                "small_files_count",
                "total_files",
                "total_size_gb",
                "total_snapshots",
                "total_delete_files",
            ),
        )

        for a in anomalies:
            all_anomalies.append(a)
            if a.severity in ("warning", "critical"):
                critical_anomalies.append(
                    {
                        "table": f"{schema_name}.{table_name}",
                        "metric": a.metric,
                        "value": a.value,
                        "baseline": a.baseline,
                        "severity": a.severity,
                        "z_score": a.z_score,
                    }
                )

    # Maintenance stats (last 24h)
    maintenance_rows = session.execute(
        select(AuditLog.action, AuditLog.status, func.count(AuditLog.id))
        .where(AuditLog.ts >= yesterday)
        .group_by(AuditLog.action, AuditLog.status)
    ).fetchall()

    maintenance_stats: dict[str, int] = {}
    for action, status, count in maintenance_rows:
        key = f"{action}_{status}"
        maintenance_stats[key] = count

    # Top issues (tables with worst scores)
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
        {
            "table": f"{row[0]}.{row[1]}",
            "grade": row[2],
            "score": row[3],
            "small_files": row[4],
        }
        for row in top_issues_rows
    ]

    # Cost stats
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

    return DailyReportData(
        report_date=now,
        total_tables=total_tables,
        healthy_tables=healthy,
        unhealthy_tables=unhealthy,
        tables_needing_compaction=compaction_count,
        total_anomalies=len(all_anomalies),
        critical_anomalies=critical_anomalies,
        grade_distribution=grade_distribution,
        maintenance_stats=maintenance_stats,
        top_issues=top_issues,
        total_size_tb=total_size_gb / 1024,
        total_cost_usd=total_cost,
        projected_savings_usd=projected_savings,
    )


async def _generate_ai_summary(session: Session, data: DailyReportData) -> str | None:
    """Generate AI summary using configured provider."""
    # Get default AI provider
    provider = session.scalar(
        select(AIProvider).where(
            AIProvider.is_default.is_(True),
            AIProvider.enabled.is_(True),
        )
    )
    if provider is None:
        provider = session.scalar(select(AIProvider).where(AIProvider.enabled.is_(True)).limit(1))
    if provider is None or not provider.api_key_encrypted:
        logger.info("No AI provider configured, skipping AI summary")
        return None

    # Build context for AI
    context = {
        "report_date": data.report_date.date().isoformat(),
        "total_tables": data.total_tables,
        "healthy_tables": data.healthy_tables,
        "unhealthy_tables": data.unhealthy_tables,
        "tables_needing_compaction": data.tables_needing_compaction,
        "total_anomalies": data.total_anomalies,
        "critical_anomalies": len(data.critical_anomalies),
        "critical_anomaly_details": data.critical_anomalies[:5],
        "grade_distribution": data.grade_distribution,
        "maintenance_last_24h": data.maintenance_stats,
        "top_unhealthy_tables": data.top_issues[:5],
        "total_size_tb": round(data.total_size_tb, 2),
        "monthly_cost_usd": round(data.total_cost_usd, 2),
        "projected_monthly_savings_usd": round(data.projected_savings_usd, 2),
    }

    prompt = f"""Generate an executive summary for this daily datalake health report:

{context}

Focus on:
1. Overall health status
2. Critical issues requiring attention
3. Maintenance activity summary
4. Cost optimization opportunities"""

    try:
        client = create_llm_client(
            provider.provider_type,
            provider.api_key_encrypted,
            provider.model,
            config=provider.config,
        )
        response = await client.complete(prompt, system=REPORT_SYSTEM_PROMPT, max_tokens=500)
        return response.content
    except Exception as e:
        logger.exception(f"Failed to generate AI summary: {e}")
        return None


def _build_slack_blocks(data: DailyReportData, ai_summary: str | None) -> list[SlackBlock]:
    """Build Slack Block Kit blocks for the report."""
    blocks: list[SlackBlock] = []

    # Header
    date_str = data.report_date.strftime("%B %d, %Y")
    blocks.append(header_block(f"🧊 Cryo Daily Report — {date_str}"))

    # AI Summary (if available)
    if ai_summary:
        blocks.append(divider_block())
        blocks.append(section_block(f"*AI Summary*\n{ai_summary}"))

    # Health Overview
    blocks.append(divider_block())
    health_emoji = (
        "🟢" if data.unhealthy_tables == 0 else "🟡" if data.unhealthy_tables < 5 else "🔴"
    )
    blocks.append(
        fields_block(
            [
                ("Total Tables", str(data.total_tables)),
                (
                    "Health Status",
                    f"{health_emoji} {data.healthy_tables} healthy / "
                    f"{data.unhealthy_tables} unhealthy",
                ),
                ("Needing Compaction", str(data.tables_needing_compaction)),
                (
                    "Anomalies Detected",
                    f"{data.total_anomalies} ({len(data.critical_anomalies)} critical)",
                ),
            ]
        )
    )

    # Grade Distribution
    if data.grade_distribution:
        grade_str = " | ".join(
            f"{grade}: {count}" for grade, count in sorted(data.grade_distribution.items())
        )
        blocks.append(section_block(f"*Grade Distribution:* {grade_str}"))

    # Critical Anomalies
    if data.critical_anomalies:
        blocks.append(divider_block())
        blocks.append(section_block("*⚠️ Critical Anomalies*"))
        for anomaly in data.critical_anomalies[:3]:
            blocks.append(
                section_block(
                    f"• `{anomaly['table']}` — {anomaly['metric']}: {anomaly['value']:.1f} "
                    f"(baseline: {anomaly['baseline']:.1f}, z-score: {anomaly['z_score']:.1f})"
                )
            )

    # Top Issues
    if data.top_issues:
        blocks.append(divider_block())
        blocks.append(section_block("*📉 Top Unhealthy Tables*"))
        for issue in data.top_issues[:5]:
            blocks.append(
                section_block(
                    f"• `{issue['table']}` — Grade {issue['grade']}, "
                    f"Score {issue['score']}, {issue['small_files']} small files"
                )
            )

    # Cost Summary
    blocks.append(divider_block())
    blocks.append(
        fields_block(
            [
                ("Total Size", f"{data.total_size_tb:.2f} TB"),
                ("Monthly S3 Cost", f"${data.total_cost_usd:,.2f}"),
                ("Projected Savings", f"${data.projected_savings_usd:,.2f}/mo"),
            ]
        )
    )

    # Maintenance Activity
    if data.maintenance_stats:
        succeeded = sum(v for k, v in data.maintenance_stats.items() if "succeeded" in k)
        failed = sum(v for k, v in data.maintenance_stats.items() if "failed" in k)
        blocks.append(section_block(f"*Maintenance (24h):* {succeeded} succeeded, {failed} failed"))

    # Footer
    blocks.append(divider_block())
    blocks.append(context_block([f"Generated by Cryo at {data.report_date.strftime('%H:%M UTC')}"]))

    return blocks
