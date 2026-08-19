"""Compute and Slack-notify the daily Trino query-cost report.

Second, independent daily report alongside ``cryo.reporting.daily_report``'s fleet-health
digest -- same relationship the shared-S3 anomaly report (``cryo.anomalies.report``) has to it.
Fanned out to every enabled ``SlackConfig`` row exactly like the shared-S3 report is (see
``notify_shared_s3_report``), so it reaches whatever channel(s) are already configured (e.g.
#cryo) without a separate webhook to set up.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import SlackConfig
from cryo.reporting.query_cost_signals import QueryCostSignal, compute_query_cost_signal
from cryo.reporting.query_cost_slack import build_query_cost_slack_blocks, query_cost_fallback_text
from cryo.reporting.slack import SlackClient
from cryo.security.crypto import decrypt_secret

logger = logging.getLogger(__name__)

# 10:15 IST -- 15 minutes after the shared-S3 report's 10:00 IST slot (cryo.anomalies.report),
# so the two independent daily Slack sends don't fire in the same scheduler tick, and so
# QueryCostAttributionDaily's own separately-watermarked ingestion has a bit more runway past
# midnight before this report reads yesterday's row.
QUERY_COST_REPORT_CRON = "15 10 * * *"
QUERY_COST_REPORT_TZ = ZoneInfo("Asia/Kolkata")


def signal_to_payload(signal: QueryCostSignal, *, generated_at: datetime) -> dict[str, Any]:
    current = signal.current
    return {
        "generated_at": generated_at.isoformat(),
        "direction": signal.direction,
        "primary_signal": signal.primary_signal,
        "dod_pct": round(signal.dod_pct, 1) if signal.dod_pct is not None else None,
        "wow_pct": round(signal.wow_pct, 1) if signal.wow_pct is not None else None,
        "current": (
            {
                "day": current.day.isoformat(),
                "query_count": current.query_count,
                "failed_query_count": current.failed_query_count,
                "physical_bytes_scanned": current.physical_bytes_scanned,
                "estimated_scan_cost_usd": current.estimated_scan_cost_usd,
            }
            if current is not None
            else None
        ),
        "top_clients": [
            {
                "client": c.client,
                "query_count": c.query_count,
                "physical_bytes_scanned": c.physical_bytes_scanned,
                "estimated_scan_cost_usd": c.estimated_scan_cost_usd,
            }
            for c in signal.top_clients_current
        ],
    }


async def notify_query_cost_report(
    session: Session, *, now: datetime | None = None
) -> dict[str, Any]:
    """Compute the signal and send it to every enabled Slack config, unconditionally.

    Unlike the shared-S3 report (which skips Slack when there's nothing to report), this posts
    every day regardless of direction -- it's a visibility report ("how much did Trino cost
    yesterday, and who drove it"), not an alert, so a CLEAR/stable day is still worth a line
    saying so rather than going silent.
    """
    now = now or datetime.now(UTC)
    signal = compute_query_cost_signal(session, now=now)
    payload = signal_to_payload(signal, generated_at=now)

    configs = session.scalars(
        select(SlackConfig).where(
            SlackConfig.enabled.is_(True),
            SlackConfig.webhook_url_encrypted.is_not(None),
        )
    ).all()
    if not configs:
        payload["slack_sent"] = False
        payload["slack_skipped_reason"] = "no_slack_config"
        payload["slack_targets"] = 0
        logger.warning("Trino query-cost report ready but no enabled Slack webhook is configured")
        return payload

    blocks = build_query_cost_slack_blocks(signal, generated_at=now)
    fallback = query_cost_fallback_text(signal, now)
    sent = 0
    for config in configs:
        webhook = decrypt_secret(config.webhook_url_encrypted)
        if not webhook:
            continue
        client = SlackClient(webhook, config.channel)
        if await client.send_blocks(blocks, fallback_text=fallback):
            sent += 1
            logger.info("Trino query-cost report sent to Slack config '%s'", config.name)
        else:
            logger.warning("Trino query-cost Slack send failed for config '%s'", config.name)

    payload["slack_sent"] = sent > 0
    payload["slack_skipped_reason"] = None if sent > 0 else "send_failed"
    payload["slack_targets"] = sent
    return payload


def run_query_cost_report_now(*, send_slack: bool = False) -> dict[str, Any]:
    """CLI/Makefile entry: compute, optionally notify."""
    import asyncio

    from cryo.db.session import SessionLocal

    session = SessionLocal()
    try:
        if send_slack:
            return asyncio.run(notify_query_cost_report(session))
        signal = compute_query_cost_signal(session)
        return signal_to_payload(signal, generated_at=datetime.now(UTC))
    finally:
        session.close()
