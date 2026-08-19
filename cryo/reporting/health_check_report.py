"""Compute and Slack-notify the daily production health checklist
(cryo.reporting.health_check). Third independent daily report alongside daily_report.py's
fleet-health digest and query_cost_report.py's Trino cost digest -- same fan-out pattern as
both: every enabled SlackConfig row, sent unconditionally (a clean-bill-of-health day is still
worth a "yes, actually checked" message, same reasoning query_cost_report.py already uses).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import SlackConfig
from cryo.reporting.health_check import run_production_health_checks
from cryo.reporting.health_check_slack import (
    build_health_check_slack_blocks,
    health_check_fallback_text,
    health_check_to_payload,
)
from cryo.reporting.slack import SlackClient
from cryo.security.crypto import decrypt_secret

logger = logging.getLogger(__name__)

# 09:45 IST -- see health_check.py's PRODUCTION_HEALTH_CHECK_CRON docstring for why this slot.
PRODUCTION_HEALTH_CHECK_CRON = "45 9 * * *"
PRODUCTION_HEALTH_CHECK_TZ = ZoneInfo("Asia/Kolkata")


async def notify_production_health_check(
    session: Session, *, now: datetime | None = None
) -> dict[str, Any]:
    """Compute the checklist and send it to every enabled Slack config, unconditionally.

    Unlike an alert that only fires on failure, this posts every day regardless of status --
    the whole point is a daily, positive confirmation that maintenance is genuinely running
    clean, not just silence that could mean either "fine" or "nobody's watching."
    """
    now = now or datetime.now(UTC)
    report = run_production_health_checks(session, now=now)
    payload = health_check_to_payload(report)

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
        logger.warning("Production health check ready but no enabled Slack webhook is configured")
        return payload

    blocks = build_health_check_slack_blocks(report, generated_at=now)
    fallback = health_check_fallback_text(report, now)
    sent = 0
    for config in configs:
        webhook = decrypt_secret(config.webhook_url_encrypted)
        if not webhook:
            continue
        client = SlackClient(webhook, config.channel)
        if await client.send_blocks(blocks, fallback_text=fallback):
            sent += 1
            logger.info("Production health check sent to Slack config '%s'", config.name)
        else:
            logger.warning("Production health check Slack send failed for config '%s'", config.name)

    payload["slack_sent"] = sent > 0
    payload["slack_skipped_reason"] = None if sent > 0 else "send_failed"
    payload["slack_targets"] = sent
    return payload


def run_production_health_check_now(*, send_slack: bool = False) -> dict[str, Any]:
    """CLI/Makefile entry: compute, optionally notify."""
    import asyncio

    from cryo.db.session import SessionLocal
    from cryo.reporting.health_check_slack import health_check_to_payload

    session = SessionLocal()
    try:
        if send_slack:
            return asyncio.run(notify_production_health_check(session))
        report = run_production_health_checks(session)
        return health_check_to_payload(report)
    finally:
        session.close()
