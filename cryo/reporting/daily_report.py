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

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from cryo.ai.runtime import LLMInvokeContext, complete_with_budget_and_observation
from cryo.db.models import AIProvider, AuditLog, Source, Table, TableStats, TableStatsHistory
from cryo.ml.anomaly_detector import AnomalyAlert, detect_all_anomalies
from cryo.reporting.cost_signals import (
    CostSignal,
    MaintenanceCorrelation,
    compute_cost_signal,
    correlate_with_maintenance,
)
from cryo.reporting.impact_signals import (
    ImpactSignal,
    compute_impact_signal,
    day_bounds,
    measured_reclaim_gb,
    reclaim_display,
)
from cryo.reporting.scan_health import ScanHealth, collect_scan_health
from cryo.reporting.slack import (
    SlackBlock,
    SlackClient,
    context_block,
    divider_block,
    header_block,
    section_block,
    table_block,
)
from cryo.reporting.work_picture import WorkPicture, compute_work_picture, work_picture_deductions

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
    tables_scanned_yesterday: int = 0
    total_files: int = 0
    small_files: int = 0
    critical_anomalies: list[dict[str, Any]] = field(default_factory=list)
    grade_distribution: dict[str, int] = field(default_factory=dict)
    maintenance_stats: dict[str, int] = field(default_factory=dict)
    top_issues: list[dict[str, Any]] = field(default_factory=list)
    total_size_tb: float = 0.0
    total_size_tb_delta: float | None = None
    total_cost_usd: float = 0.0
    projected_savings_usd: float = 0.0
    cost_signal: CostSignal | None = None
    cost_correlation: MaintenanceCorrelation | None = None
    impact_signal: ImpactSignal | None = None
    work_picture: object | None = None
    scan_health: ScanHealth | None = None
    action_items: list[dict[str, Any]] = field(default_factory=list)


async def generate_daily_report(
    session: Session,
    slack_client: SlackClient | None = None,
    *,
    use_ai_summary: bool = True,
    include_diagnostics: bool = True,
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

    report_data = _collect_report_data(session, include_diagnostics=include_diagnostics)

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
            "signal": (
                {
                    "direction": report_data.cost_signal.direction,
                    "primary_signal": report_data.cost_signal.primary_signal,
                    "dod_pct": report_data.cost_signal.dod_pct,
                    "wow_pct": report_data.cost_signal.wow_pct,
                    "explained": (
                        report_data.cost_correlation.explains_change
                        if report_data.cost_correlation
                        else None
                    ),
                    "explanation": (
                        report_data.cost_correlation.summary
                        if report_data.cost_correlation
                        else None
                    ),
                }
                if report_data.cost_signal is not None
                else None
            ),
        },
        "impact": (
            {
                # Review finding F10: distinguishes "gb_freed_yesterday is the real complete
                # total" from "partial subtotal, some evidence missing" from "no evidence at
                # all" -- a bare gb_freed number used to imply the first case unconditionally.
                "measurement_state": report_data.impact_signal.measurement_state,
                "gb_freed_yesterday": (
                    round(measured, 2)
                    if (measured := measured_reclaim_gb(report_data.impact_signal)) is not None
                    else None
                ),
                "actions_succeeded": report_data.impact_signal.today.actions_succeeded,
                "tables_touched": report_data.impact_signal.today.tables_touched,
                "optimize_files_in": report_data.impact_signal.today.optimize_files_in,
                "optimize_files_out": report_data.impact_signal.today.optimize_files_out,
                "optimize_file_reduction_pct": (
                    round(report_data.impact_signal.today.optimize_file_reduction_pct, 1)
                    if report_data.impact_signal.today.optimize_file_reduction_pct is not None
                    else None
                ),
                "direction": report_data.impact_signal.direction,
                # Absolute baselines alongside the pct change -- a bare "-94.9%" doesn't say
                # what it's relative to; gb_freed_yesterday vs dod/wow_baseline_gb does.
                "dod_baseline_gb": (
                    round(report_data.impact_signal.dod_baseline_gb, 2)
                    if report_data.impact_signal.dod_baseline_gb is not None
                    else None
                ),
                "wow_baseline_gb": (
                    round(report_data.impact_signal.wow_baseline_gb, 2)
                    if report_data.impact_signal.wow_baseline_gb is not None
                    else None
                ),
                "dod_pct": _comparable_pct(report_data.impact_signal, "dod"),
                "wow_pct": _comparable_pct(report_data.impact_signal, "wow"),
            }
            if report_data.impact_signal is not None
            else None
        ),
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
    from cryo.reporting.html_report import render_daily_report_html

    email_sent = send_report_email(
        f"Cryo Daily Report — {report_data.report_date.date()}",
        render_daily_report_html(report_data, narrated_bottom_line),
    )
    if email_sent:
        result["email_sent"] = True

    return result


def _append_north_star(blocks: list[SlackBlock], picture: object | None) -> None:
    if not isinstance(picture, WorkPicture):
        return
    hits = work_picture_deductions(picture)
    if any(hit.severe for hit in hits):
        status = "CRITICAL"
    elif hits:
        status = "DEGRADED"
    else:
        status = "HEALTHY"
    coverage = "n/a" if picture.coverage_ratio is None else f"{picture.coverage_ratio:.0%}"
    if picture.measurement_ratio is None:
        measured = "n/a"
    else:
        measured = (
            f"{picture.measurement_ratio:.0%} "
            f"({picture.byte_actions_measured}/{picture.byte_actions_succeeded})"
        )
    engines = ", ".join(f"{engine} {count}" for engine, count in picture.by_engine) or "none"
    blocks.append(divider_block())
    blocks.append(section_block(f"*North star and vitals — {status}*"))
    blocks.append(
        table_block(
            ["North star", "Yesterday"],
            [
                [
                    "Tables maintained",
                    f"{picture.tables_maintained:,} / {picture.tables_baseline_median} ({coverage})",
                ],
                ["Measured GiB", f"{picture.measured_gib:.1f}"],
                ["Byte measurement", measured],
                ["Engines", engines],
            ],
        )
    )
    action_rows = [
        [
            row.action,
            f"{row.succeeded:,}",
            f"{row.failed:,}",
            f"{row.measured:,}",
            f"{row.bytes_freed / (1024**3):.1f}",
        ]
        for row in picture.by_action
    ]
    if action_rows:
        blocks.append(
            table_block(
                ["Action", "OK", "Failed", "Measured", "GiB"],
                action_rows,
            )
        )
    blocks.append(
        context_block(
            [
                "Vitals here are coverage and measurement, not job success. "
                "expire_snapshots has no size in Iceberg's result, so a blank expire GiB is not a failure."
            ]
        )
    )


def _comparable_pct(signal: ImpactSignal, window: Literal["dod", "wow"]) -> float | None:
    if not signal.today.is_comparable_total:
        return None
    pct = signal.dod_pct if window == "dod" else signal.wow_pct
    return round(pct, 1) if pct is not None else None


def _impact_signal_sentence(data: DailyReportData) -> str:
    signal = data.impact_signal
    if signal is None or signal.direction == "flat":
        return ""
    if signal.direction == "unknown":
        # Recovery plan §6: no reclaim action yesterday reported actual_bytes_freed (e.g. the
        # fleet ran entirely on Trino/Spark, which don't emit it yet) -- say so plainly rather
        # than print "0.0 GB freed", which would misreport an absent measurement as a real one.
        return " Reclaim impact unknown: no engine reported bytes-freed evidence yesterday."
    pct = signal.dod_pct if signal.primary_signal == "dod" else signal.wow_pct
    baseline = signal.dod_baseline_gb if signal.primary_signal == "dod" else signal.wow_baseline_gb
    window = "DoD" if signal.primary_signal == "dod" else "WoW"
    # Always pair the pct with what it's relative to -- "-94.9%" alone doesn't say from what.
    comparison = (
        f" ({pct:+.1f}% {window}, vs {baseline:.1f} GB baseline)"
        if pct is not None and baseline is not None
        else f" ({pct:+.1f}% {window})"
        if pct is not None
        else ""
    )
    # Review finding F10: a "partial" day's gb_freed is real but incomplete -- flag it inline
    # rather than let a reader assume it's the full day total the way "complete" coverage is.
    if signal.measurement_state == "partial":
        comparison += " -- partial coverage, some actions had no bytes-freed evidence"
    return f" Reclaim impact {signal.direction}{comparison}: {signal.today.gb_freed:.1f} GB freed yesterday."


def _cost_signal_sentence(data: DailyReportData) -> str:
    signal = data.cost_signal
    if signal is None or signal.direction == "stable":
        return ""
    pct = signal.dod_pct if signal.primary_signal == "dod" else signal.wow_pct
    window = "DoD" if signal.primary_signal == "dod" else "WoW"
    pct_str = f" ({pct:+.1f}% {window})" if pct is not None else ""
    explained = data.cost_correlation is not None and data.cost_correlation.explains_change
    tail = " Explained by Cryo's own maintenance." if explained else " Not yet explained."
    return f" Fleet cost {signal.direction}{pct_str}.{tail}"


def _deterministic_bottom_line(data: DailyReportData) -> str:
    cost_sentence = _cost_signal_sentence(data)
    impact_sentence = _impact_signal_sentence(data)
    if data.status == "CLEAR":
        base = f"All {data.total_tables} tables healthy, no anomalies detected."
        return f"{base}{impact_sentence}{cost_sentence} No action required."
    delta_str = ""
    if data.total_size_tb_delta is not None:
        direction = "up" if data.total_size_tb_delta >= 0 else "down"
        delta_str = f" Fleet size {direction} {abs(data.total_size_tb_delta):.2f} TB vs yesterday."
    noun = "anomaly" if len(data.critical_anomalies) == 1 else "anomalies"
    return (
        f"{data.unhealthy_tables} of {data.total_tables} tables need attention "
        f"({len(data.critical_anomalies)} {noun} flagged).{delta_str}{impact_sentence}{cost_sentence}"
    )


def _classify_status(
    unhealthy_tables: int,
    anomalies: list[AnomalyAlert],
    cost_signal: CostSignal | None = None,
    cost_correlation: MaintenanceCorrelation | None = None,
) -> ReportStatus:
    if any(a.severity == "high" for a in anomalies):
        return "CRITICAL"
    # An unexplained cost spike (no matching maintenance activity found) is worth surfacing
    # even when nothing else fired -- explained spikes (Cryo's own rewrites, pending their
    # own cleanup window) still show up in the report, just without forcing WATCH status.
    if cost_signal is not None and cost_signal.direction == "spike":
        if cost_correlation is None or not cost_correlation.explains_change:
            return "WATCH"
    if unhealthy_tables > 0 or any(a.severity == "medium" for a in anomalies):
        return "WATCH"
    return "CLEAR"


def _fleet_table_ids() -> Select[tuple[uuid.UUID]]:
    """Active tables on enabled sources: the fleet every Fleet number in the digest describes.

    A disabled source's tables keep is_active=True and their last stats rows (cdp_dev: 1,164
    tables), so filtering on is_active alone overstated the fleet by ~10%.
    """
    return (
        select(Table.id)
        .join(Source, Source.id == Table.source_id)
        .where(Table.is_active.is_(True), Source.enabled.is_(True))
    )


def _size_snapshot_asof(session: Session, as_of: datetime) -> float | None:
    """Fleet total_size_gb as of the nearest prior scan per table to ``as_of`` -- used only to
    compute a real day-over-day size delta. Returns None (no delta shown, never fabricated) if
    there isn't enough history yet for the comparison to mean anything."""
    subq = (
        select(
            TableStatsHistory.table_id,
            func.max(TableStatsHistory.scan_at).label("scan_at"),
        )
        .where(
            TableStatsHistory.scan_at <= as_of,
            TableStatsHistory.table_id.in_(_fleet_table_ids()),
        )
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


def _add_cost_action_item(
    items: list[dict[str, Any]],
    signal: CostSignal,
    correlation: MaintenanceCorrelation,
) -> list[dict[str, Any]]:
    """Prepend a cost-signal action item when the fleet cost spiked or declined past
    threshold -- ahead of table-level items, since a fleet-wide cost move is more
    consequential than any single table's grade. An explained spike/decline still gets an
    item (so the report never silently drops a real cost move), just marked low severity
    with the explanation attached instead of reading as an open question.
    """
    pct = signal.dod_pct if signal.primary_signal == "dod" else signal.wow_pct
    window = "day-over-day" if signal.primary_signal == "dod" else "week-over-week"
    severity = "high" if not correlation.explains_change else "low"
    sign = "+" if (pct or 0) >= 0 else ""
    text = (
        f"Fleet cost {signal.direction} — {sign}{pct:.1f}% {window}. {correlation.summary}"
        if pct is not None
        else f"Fleet cost {signal.direction}. {correlation.summary}"
    )
    item = {"severity": severity, "text": text, "owner": "no owner assigned"}
    return [item, *items[:2]]


def _add_impact_action_item(
    items: list[dict[str, Any]],
    signal: ImpactSignal,
) -> list[dict[str, Any]]:
    """Prepend an item when reclaim impact is worsening -- ahead of table-level items, same
    reasoning as _add_cost_action_item: a fleet-wide trend outranks any single table's grade.
    Only ever called for "worsening"; an "improving" day is good news, not an action item."""
    pct = signal.dod_pct if signal.primary_signal == "dod" else signal.wow_pct
    baseline = signal.dod_baseline_gb if signal.primary_signal == "dod" else signal.wow_baseline_gb
    window = "day-over-day" if signal.primary_signal == "dod" else "week-over-week"
    # Always pair the pct with the absolute baseline it's relative to -- a bare "-94.9%" forces
    # the reader to do the arithmetic themselves to find out this dropped from ~1268 GB to 65 GB.
    comparison = (
        f" ({pct:+.1f}% {window}, down from {baseline:.1f} GB)"
        if pct is not None and baseline is not None
        else f" ({pct:+.1f}% {window})"
        if pct is not None
        else ""
    )
    text = (
        f"Reclaim impact worsening{comparison} — {signal.today.gb_freed:.1f} GB freed "
        f"yesterday from {signal.today.actions_succeeded} successful action"
        f"{'s' if signal.today.actions_succeeded != 1 else ''} across "
        f"{signal.today.tables_touched} table{'s' if signal.today.tables_touched != 1 else ''}."
    )
    item = {"severity": "high", "text": text, "owner": "no owner assigned"}
    return [item, *items[:2]]


def _collect_report_data(session: Session, *, include_diagnostics: bool = True) -> DailyReportData:
    """Collect the data for the report.

    ``include_diagnostics`` covers the parts only the API response renders -- anomalies,
    the worst-table list, the cost signal and the derived status/action items. The
    scheduled Slack digest stopped showing all of those, and computing them anyway cost
    ~9 minutes of blocking work per send, so it opts out.
    """
    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)

    in_fleet = TableStats.table_id.in_(_fleet_table_ids())
    total_tables = (
        session.scalar(select(func.count()).select_from(_fleet_table_ids().subquery())) or 0
    )

    grade_rows = session.execute(
        select(TableStats.grade, func.count(TableStats.table_id))
        .where(TableStats.grade.isnot(None), in_fleet)
        .group_by(TableStats.grade)
    ).fetchall()
    grade_distribution = {row[0]: row[1] for row in grade_rows}

    healthy = sum(grade_distribution.get(g, 0) for g in ["A", "B"])
    unhealthy = sum(grade_distribution.get(g, 0) for g in ["D", "E", "F"])

    compaction_count = (
        session.scalar(
            select(func.count(TableStats.table_id)).where(
                TableStats.needs_compaction.is_(True), in_fleet
            )
        )
        or 0
    )

    # Richer, seasonality-aware anomaly detection -- the same detector already proven out for
    # per-table Slack breach alerts (cryo/scheduler/alert_dispatcher.py), fleet-wide.
    # Measured at 540s against the 12.6k-table fleet on 2026-09-23, so it is skipped unless
    # a caller actually renders it; the scheduled Slack digest does not.
    anomaly_alerts: list[AnomalyAlert] = (
        detect_all_anomalies(session, min_severity="medium", lookback_days=7)
        if include_diagnostics
        else []
    )
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

    # Calendar yesterday, matching compute_impact_signal's window rather than a rolling 24h.
    # The digest shows both side by side, and two "yesterday" numbers spanning different
    # windows disagreed by ~900 actions.
    yesterday_start, yesterday_end = day_bounds(now, 1)
    maintenance_rows = session.execute(
        select(AuditLog.action, AuditLog.status, func.count(AuditLog.id))
        .where(AuditLog.ts >= yesterday_start, AuditLog.ts < yesterday_end)
        .group_by(AuditLog.action, AuditLog.status)
    ).fetchall()
    maintenance_stats: dict[str, int] = {
        f"{action}_{status}": count for action, status, count in maintenance_rows
    }

    top_issues: list[dict[str, Any]] = []
    if include_diagnostics:
        top_issues_rows = session.execute(
            select(
                Table.schema_name,
                Table.table_name,
                TableStats.grade,
                TableStats.overall_score,
                TableStats.small_files_count,
            )
            .join(TableStats, TableStats.table_id == Table.id)
            .where(TableStats.overall_score < 60, in_fleet)
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
            func.sum(TableStats.total_files),
            func.sum(TableStats.small_files_count),
        ).where(in_fleet)
    ).first()
    total_size_gb = float(cost_stats[0] or 0.0) if cost_stats is not None else 0.0
    total_cost = float(cost_stats[1] or 0.0) if cost_stats is not None else 0.0
    projected_savings = float(cost_stats[2] or 0.0) if cost_stats is not None else 0.0
    total_files = int(cost_stats[3] or 0) if cost_stats is not None else 0
    small_files = int(cost_stats[4] or 0) if cost_stats is not None else 0
    total_size_tb = total_size_gb / 1024

    tables_scanned_yesterday = (
        session.scalar(
            select(func.count(TableStats.table_id)).where(
                TableStats.scan_at >= yesterday_start,
                TableStats.scan_at < yesterday_end,
                in_fleet,
            )
        )
        or 0
    )

    size_yesterday_tb = _size_snapshot_asof(session, yesterday)
    size_delta = (total_size_tb - size_yesterday_tb) if size_yesterday_tb is not None else None

    # Sub-second each, and the digest renders the impact numbers, so these always run.
    cost_signal = compute_cost_signal(session, now=now)
    cost_correlation = correlate_with_maintenance(session, cost_signal, now=now)
    impact_signal = compute_impact_signal(session, now=now)
    work_picture = compute_work_picture(session, now=now)

    status: ReportStatus = "CLEAR"
    action_items: list[dict[str, Any]] = []
    if include_diagnostics:
        status = _classify_status(unhealthy, anomaly_alerts, cost_signal, cost_correlation)
        if status != "CRITICAL" and impact_signal.direction == "worsening":
            status = "WATCH"
        action_items = (
            _build_action_items(session, anomaly_alerts, top_issues) if status != "CLEAR" else []
        )
        if cost_signal.direction != "stable" and cost_correlation is not None:
            action_items = _add_cost_action_item(action_items, cost_signal, cost_correlation)
        if impact_signal.direction == "worsening":
            action_items = _add_impact_action_item(action_items, impact_signal)
        if status != "CRITICAL" and work_picture_deductions(work_picture):
            status = "WATCH"

    return DailyReportData(
        report_date=now,
        status=status,
        total_tables=total_tables,
        healthy_tables=healthy,
        unhealthy_tables=unhealthy,
        tables_needing_compaction=compaction_count,
        total_anomalies=len(anomaly_alerts),
        tables_scanned_yesterday=tables_scanned_yesterday,
        total_files=total_files,
        small_files=small_files,
        critical_anomalies=critical_anomalies,
        grade_distribution=grade_distribution,
        maintenance_stats=maintenance_stats,
        top_issues=top_issues,
        total_size_tb=total_size_tb,
        total_size_tb_delta=size_delta,
        total_cost_usd=total_cost,
        projected_savings_usd=projected_savings,
        cost_signal=cost_signal,
        cost_correlation=cost_correlation,
        impact_signal=impact_signal,
        work_picture=work_picture,
        scan_health=collect_scan_health(session),
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
        "impact_signal": (
            {
                "direction": data.impact_signal.direction,
                "measurement_state": data.impact_signal.measurement_state,
                "gb_freed_yesterday": (
                    round(measured_reclaim_gb(data.impact_signal), 1)
                    if measured_reclaim_gb(data.impact_signal) is not None
                    else None
                ),
                "dod_baseline_gb": (
                    round(data.impact_signal.dod_baseline_gb, 1)
                    if data.impact_signal.dod_baseline_gb is not None
                    else None
                ),
                "wow_baseline_gb": (
                    round(data.impact_signal.wow_baseline_gb, 1)
                    if data.impact_signal.wow_baseline_gb is not None
                    else None
                ),
                "dod_pct": _comparable_pct(data.impact_signal, "dod"),
                "wow_pct": _comparable_pct(data.impact_signal, "wow"),
            }
            if data.impact_signal is not None
            else None
        ),
        "cost_signal": (
            {
                "direction": data.cost_signal.direction,
                "primary_signal": data.cost_signal.primary_signal,
                "dod_pct": (
                    round(data.cost_signal.dod_pct, 1)
                    if data.cost_signal.dod_pct is not None
                    else None
                ),
                "wow_pct": (
                    round(data.cost_signal.wow_pct, 1)
                    if data.cost_signal.wow_pct is not None
                    else None
                ),
                "explanation": data.cost_correlation.summary if data.cost_correlation else None,
            }
            if data.cost_signal is not None
            else None
        ),
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


def _format_bytes(num_bytes: float) -> str:
    """Human-readable size for a single file's average, which spans KB to GB across the fleet."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024.0 or unit == "GB":
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} GB"


_SUCCESS_STATUSES = {"ok", "success", "succeeded", "completed"}
_FAILURE_STATUSES = {"failed", "failure", "error"}


def _maintenance_split(stats: dict[str, int]) -> list[list[str]]:
    """Turn ``{"optimize_OK": 12, ...}`` into per-operation succeeded/failed rows.

    Keys are ``f"{action}_{status}"`` built in _collect_report_data, and status is the last
    underscore-separated token. AuditLog writes 'OK' and 'FAILED', not the 'succeeded' the
    previous summary line matched on, which is why every report read "0 succeeded, 0 failed"
    on days with thousands of actions.
    """
    per_action: dict[str, dict[str, int]] = {}
    for key, count in stats.items():
        action, _, status = key.rpartition("_")
        if not action:
            continue
        bucket = per_action.setdefault(action, {"succeeded": 0, "failed": 0})
        normalised = status.lower()
        if normalised in _SUCCESS_STATUSES:
            bucket["succeeded"] += count
        elif normalised in _FAILURE_STATUSES:
            bucket["failed"] += count

    return [
        [action, f"{counts['succeeded']:,}", f"{counts['failed']:,}"]
        for action, counts in sorted(per_action.items())
        if counts["succeeded"] or counts["failed"]
    ]


def _build_slack_blocks(data: DailyReportData, bottom_line: str) -> list[SlackBlock]:
    """Build the daily digest.

    Deliberately limited to the fleet counters, yesterday's maintenance and impact, the
    two trend comparisons, the grade spread and the file-size picture. The narrated
    bottom line, anomaly list, cost/savings estimates, top-unhealthy table list and
    action items were removed on request: they made the message long enough that the
    numbers being tracked day to day were hard to find in it.
    """
    blocks: list[SlackBlock] = []

    date_str = data.report_date.strftime("%B %d, %Y")
    blocks.append(header_block(f"🧊 Cryo Daily Report — {date_str}"))

    size_delta_str = ""
    if data.total_size_tb_delta is not None:
        arrow = "↑" if data.total_size_tb_delta >= 0 else "↓"
        size_delta_str = f" {arrow}{abs(data.total_size_tb_delta):.2f}"

    blocks.append(
        table_block(
            ["Fleet", "Value"],
            [
                ["Active Tables", f"{data.total_tables:,}"],
                ["Scanned (yesterday)", f"{data.tables_scanned_yesterday:,}"],
                ["Needing Compaction", f"{data.tables_needing_compaction:,}"],
                ["Total Size", f"{data.total_size_tb:.2f} TB{size_delta_str}"],
            ],
        )
    )

    if data.scan_health is not None:
        health = data.scan_health
        retry_str = " · ".join(
            f"{count:,} {label}" for label, count in health.retry_tiers.items() if count
        )
        rows = [
            ["Tables in catalog", f"{health.total:,}"],
            ["Passed", f"{health.passed:,}"],
            ["Corrupt (metadata missing in S3)", f"{health.corrupt:,}"],
            ["Failed (permissions / other)", f"{health.failed:,}"],
        ]
        if health.never_scanned:
            rows.append(["Not scanned yet", f"{health.never_scanned:,}"])
        if retry_str:
            rows.append(["Failing tables retried", retry_str])
        blocks.append(divider_block())
        blocks.append(table_block(["Scan Health", "Tables"], rows))

    blocks.append(divider_block())
    blocks.append(section_block("*Maintenance Operations (yesterday)*"))
    operation_rows = _maintenance_split(data.maintenance_stats)
    if operation_rows:
        blocks.append(table_block(["Operation", "Succeeded", "Failed"], operation_rows))
    else:
        blocks.append(section_block("No maintenance operations ran."))

    if data.impact_signal is not None:
        impact = data.impact_signal
        opt_pct = impact.today.optimize_file_reduction_pct
        opt_str = (
            f"{impact.today.optimize_files_in:,} → {impact.today.optimize_files_out:,} "
            f"({opt_pct:+.0f}%)"
            if opt_pct is not None
            else "no optimize runs"
        )
        blocks.append(divider_block())
        blocks.append(
            table_block(
                ["Impact", "Yesterday"],
                [
                    ["GB Reclaimed", reclaim_display(impact)],
                    ["Successful Actions", f"{impact.today.actions_succeeded:,}"],
                    ["Tables Touched", f"{impact.today.tables_touched:,}"],
                    ["Optimize File Reduction", opt_str],
                ],
            )
        )

        # Both baselines are shown next to the percentage because a bare "-94.9%" says
        # which way it moved but not what it moved against.
        dod_baseline_str = (
            f"{impact.dod_baseline_gb:.1f} GB" if impact.dod_baseline_gb is not None else "n/a"
        )
        wow_baseline_str = (
            f"{impact.wow_baseline_gb:.1f} GB" if impact.wow_baseline_gb is not None else "n/a"
        )
        dod_pct = _comparable_pct(impact, "dod")
        wow_pct = _comparable_pct(impact, "wow")
        dod_pct_str = f"{dod_pct:+.1f}%" if dod_pct is not None else "n/a"
        wow_pct_str = f"{wow_pct:+.1f}%" if wow_pct is not None else "n/a"
        yesterday_str = reclaim_display(impact)
        blocks.append(
            table_block(
                ["Trend", "Baseline", "Yesterday", "Change"],
                [
                    [
                        "Day-over-day (vs 2 days ago)",
                        dod_baseline_str,
                        yesterday_str,
                        dod_pct_str,
                    ],
                    [
                        "Week-over-week (vs same day last week)",
                        wow_baseline_str,
                        yesterday_str,
                        wow_pct_str,
                    ],
                ],
            )
        )

    if data.grade_distribution:
        blocks.append(divider_block())
        blocks.append(section_block("*Grade Distribution*"))
        blocks.append(
            table_block(
                ["Grade", "Tables"],
                [[grade, f"{count:,}"] for grade, count in sorted(data.grade_distribution.items())],
            )
        )

    ratio_str = f"{100 * data.small_files / data.total_files:.1f}%" if data.total_files else "n/a"
    avg_size_str = (
        _format_bytes(data.total_size_tb * (1024**4) / data.total_files)
        if data.total_files
        else "n/a"
    )
    blocks.append(divider_block())
    blocks.append(
        table_block(
            ["Files", "Value"],
            [
                ["Total Files", f"{data.total_files:,}"],
                ["Small Files", f"{data.small_files:,}"],
                ["Small-File Ratio", ratio_str],
                ["Avg File Size", avg_size_str],
            ],
        )
    )

    _append_north_star(blocks, data.work_picture)
    blocks.append(divider_block())
    blocks.append(context_block([f"Generated by Cryo at {data.report_date.strftime('%H:%M UTC')}"]))

    return blocks
