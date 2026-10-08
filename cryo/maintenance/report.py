"""The one Slack message a batch maintenance run posts."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import SlackConfig
from cryo.maintenance.coordinator import CRON_TRIGGER, RunReport
from cryo.reporting.slack import (
    SlackBlock,
    SlackClient,
    context_block,
    fields_block,
    header_block,
    section_block,
)
from cryo.security.crypto import decrypt_secret

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _clock(value: datetime, zone: timezone) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(zone).strftime("%H:%M")


def _ist(value: datetime) -> str:
    return _clock(value, _IST)


def _duration(start: datetime, end: datetime) -> str:
    minutes = max(0, int((end - start).total_seconds() // 60))
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def _count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 10_000:
        return f"{value / 1_000:.0f}k"
    return f"{value:,}"


def _size(gb: float) -> str:
    return f"{gb / 1024:.2f} TB" if abs(gb) >= 1024 else f"{gb:,.1f} GB"


def _change(before: float, after: float) -> str:
    delta = after - before
    pct = (delta / before * 100) if before else 0.0
    arrow = "↓" if delta < 0 else ("↑" if delta > 0 else "→")
    return f"{arrow}{abs(pct):.0f}%"


def build_blocks(
    report: RunReport, *, web_base_url: str | None = None
) -> tuple[str, list[SlackBlock]]:
    finished = report.finished_at or report.started_at
    window = (
        f"{_clock(report.started_at, UTC)}–{_clock(finished, UTC)} UTC "
        f"({_ist(report.started_at)}–{_ist(finished)} Asia/Kolkata), "
        f"{_duration(report.started_at, finished)}"
    )
    attempted = report.succeeded + report.partial + report.failed
    if report.failed == 0 and report.partial == 0 and report.skipped == 0:
        status = ":large_green_circle: CLEAR"
    elif report.maintained > report.failed:
        status = ":large_yellow_circle: WATCH"
    else:
        status = ":red_circle: CRITICAL"
    headline = (
        f"{status} · *{report.maintained}* of {attempted} tables maintained ({window})"
        if attempted
        else f"{status} · nothing needed maintenance ({window})"
    )
    title = (
        "Cryo nightly maintenance"
        if report.trigger == CRON_TRIGGER
        else f"Cryo maintenance ({report.trigger})"
    )
    fallback = f"{title}: {report.maintained}/{attempted} tables maintained"

    files_after = report.files_after
    size_after = report.size_after_gb
    fields = [
        (
            "Tables",
            f"✅ {report.succeeded} · ⚠️ {report.partial} · ❌ {report.failed} · ⏭️ {report.skipped}",
        ),
        (
            "Data files (maintained tables)",
            f"{_count(report.files_before)} → "
            + (
                f"{_count(files_after)} ({_change(report.files_before, files_after)})"
                if files_after is not None
                else "rescan unavailable"
            ),
        ),
        (
            "Size (maintained tables)",
            f"{_size(report.size_before_gb)} → "
            + (
                f"{_size(size_after)} ({_change(report.size_before_gb, size_after)})"
                if size_after is not None
                else "rescan unavailable"
            ),
        ),
        (
            "Compaction",
            f"{_count(report.files_rewritten)} files → {_count(report.files_added)} "
            f"({_size(report.bytes_rewritten / 1024**3)} rewritten)",
        ),
        (
            "Cleanup",
            f"{_count(report.orphan_files_removed)} orphan files · "
            f"{_count(report.expired_files_deleted)} expired files",
        ),
        (
            "Spark pods",
            ", ".join(
                f"{lane['cpu_milli'] // 1000} vCPU/{lane['mem_mib'] // 1024} GiB×{lane['tables']}t"
                + (" (replacement)" if "replaces" in lane else "")
                for lane in report.lanes
            )
            or "none",
        ),
    ]
    blocks = [header_block(title), section_block(headline), fields_block(fields)]
    notes: list[str] = []
    if report.failures:
        top = report.failures[:3]
        notes.append(
            "*Top failures*\n" + "\n".join(f"• `{f['table']}`: {f['error'][:140]}" for f in top)
        )
    if report.orphan_gate_closed:
        notes.append(
            ":warning: Orphan-file removal was skipped for every table: the shared-S3-path "
            "snapshot could not be refreshed and the last one is stale."
        )
    if report.rescan_error:
        notes.append(f":warning: Post-run rescan failed: {report.rescan_error[:200]}")
    if notes:
        blocks.append(section_block("\n\n".join(notes)))
    footer = [
        f"Selected {report.selected} · nothing to do {report.nothing_to_do} · "
        f"planning failed {report.planning_failed} · locked {report.locked}"
    ]
    if web_base_url and report.run_id:
        footer.append(f"<{web_base_url.rstrip('/')}/runs/{report.run_id}|Open run>")
    blocks.append(context_block(footer))
    return fallback, blocks


def _slack_client(session: Session) -> SlackClient | None:
    config = session.scalar(
        select(SlackConfig).where(SlackConfig.is_default.is_(True), SlackConfig.enabled.is_(True))
    ) or session.scalar(select(SlackConfig).where(SlackConfig.enabled.is_(True)).limit(1))
    if config is None or config.webhook_url_encrypted is None:
        return None
    url = decrypt_secret(config.webhook_url_encrypted)
    if not url:
        return None
    return SlackClient(url, config.channel)


def post_report(session: Session, report: RunReport, *, web_base_url: str | None = None) -> bool:
    client = _slack_client(session)
    if client is None:
        logger.warning("No enabled Slack connection; batch maintenance report not sent")
        return False
    fallback, blocks = build_blocks(report, web_base_url=web_base_url)
    return asyncio.run(client.send_message(fallback, blocks))
