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

from cryo.ai.runtime import (
    LLMInvokeContext,
    complete_with_budget_and_observation,
    get_active_provider,
)
from cryo.db.models import SlackConfig
from cryo.reporting.query_cost_signals import (
    UNKNOWN_CLIENT_FLAG_SHARE_PCT,
    QueryCostSignal,
    compute_query_cost_signal,
)
from cryo.reporting.query_cost_slack import build_query_cost_slack_blocks, query_cost_fallback_text
from cryo.reporting.slack import SlackClient
from cryo.security.crypto import decrypt_secret

logger = logging.getLogger(__name__)

BOTTOM_LINE_SYSTEM_PROMPT = """You are Cryo's Trino query-cost report narrator. Write the \
"bottom line" for a daily query-cost digest: exactly 1-2 sentences, no more.

Rules:
- Sentence 1: what happened (the headline fact -- cost/failure-rate direction and magnitude).
- Sentence 2 (optional): who/what drove it, if evident from the top-clients data given.
- Every number you use MUST come from the data provided -- never invent or estimate a figure.
- Plain language, no jargon, no markdown headers.
- If nothing notable happened, say so plainly rather than padding."""

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
        "top_users": [
            {
                "user_name": u.user_name,
                "query_count": u.query_count,
                "physical_bytes_scanned": u.physical_bytes_scanned,
                "estimated_scan_cost_usd": u.estimated_scan_cost_usd,
            }
            for u in signal.top_users_current
        ],
    }


def _build_action_items(signal: QueryCostSignal) -> list[dict[str, Any]]:
    """Up to 3 owned action items -- same shape/severity vocabulary as
    ``daily_report._build_action_items`` (severity/text/owner dicts) so a future unified
    action-item view across both reports doesn't need two different shapes. All items here are
    still "no owner assigned": ``top_users_current`` (see
    docs/QUERY_COST_USER_ATTRIBUTION_PLAN_2026-08.md) gives a real per-user name now, but Cryo
    has no directory mapping a Trino ``user_name`` back to a person's real identity/Slack
    handle the way ``daily_report``'s table-level items have via ``Table.owner_email`` -- that
    mapping, not the attribution data itself, is the missing piece for a real @-mention here.
    """
    items: list[dict[str, Any]] = []

    fsig = signal.failure_signal
    if fsig is not None and fsig.direction == "spike" and fsig.current_rate_pct is not None:
        points = fsig.dod_pct_points if fsig.primary_signal == "dod" else fsig.wow_pct_points
        window = "day-over-day" if fsig.primary_signal == "dod" else "week-over-week"
        points_str = f" (+{points:.1f}pp {window})" if points is not None else ""
        current = signal.current
        count_str = (
            f" -- {current.failed_query_count:,} of {current.query_count:,} queries"
            if current is not None
            else ""
        )
        rate_text = f"Failure rate worsened to {fsig.current_rate_pct:.1f}%{points_str}{count_str}."
        items.append(
            {"severity": "high", "text": rate_text, "owner": "no owner assigned"}
        )

    if (
        signal.unknown_client_share_pct is not None
        and signal.unknown_client_share_pct >= UNKNOWN_CLIENT_FLAG_SHARE_PCT
    ):
        items.append(
            {
                "severity": "medium",
                "text": (
                    f"{signal.unknown_client_share_pct:.0f}% of yesterday's scan cost has no "
                    f"identified client (empty `source` field) -- trace to a real label."
                ),
                "owner": "no owner assigned",
            }
        )

    if signal.direction != "stable":
        pct = signal.dod_pct if signal.primary_signal == "dod" else signal.wow_pct
        window = "day-over-day" if signal.primary_signal == "dod" else "week-over-week"
        pct_str = f" ({pct:+.1f}% {window})" if pct is not None else ""
        top = signal.top_clients_current[0] if signal.top_clients_current else None
        driver_str = f" Top spender: `{top.client}`." if top is not None else ""
        items.append(
            {
                "severity": "low" if signal.direction == "decline" else "medium",
                "text": f"Query cost {signal.direction}{pct_str}.{driver_str}",
                "owner": "no owner assigned",
            }
        )

    return items[:3]


async def _generate_bottom_line(session: Session, signal: QueryCostSignal) -> str | None:
    """1-2 sentence AI-narrated bottom line, same pattern as
    ``daily_report._generate_bottom_line`` -- never lets the LLM invent a number, only the
    pre-computed context below is available to narrate. Returns None (not raised) when no AI
    provider is configured or the call fails; the report still posts in full without this line.
    """
    provider = get_active_provider(session)
    if provider is None:
        logger.info("No AI provider configured, skipping query-cost bottom line")
        return None

    current = signal.current
    if current is None:
        return None

    fsig = signal.failure_signal
    context: dict[str, Any] = {
        "direction": signal.direction,
        "dod_pct": round(signal.dod_pct, 1) if signal.dod_pct is not None else None,
        "wow_pct": round(signal.wow_pct, 1) if signal.wow_pct is not None else None,
        "estimated_scan_cost_usd": (
            round(current.estimated_scan_cost_usd, 2)
            if current.estimated_scan_cost_usd is not None
            else None
        ),
        "query_count": current.query_count,
        "failed_query_count": current.failed_query_count,
        "failure_rate_pct": (
            round(fsig.current_rate_pct, 1)
            if fsig is not None and fsig.current_rate_pct is not None
            else None
        ),
        "failure_rate_direction": fsig.direction if fsig is not None else None,
        "unknown_client_share_pct": (
            round(signal.unknown_client_share_pct, 1)
            if signal.unknown_client_share_pct is not None
            else None
        ),
        "top_clients": [
            {
                "client": c.client,
                "estimated_scan_cost_usd": (
                    round(c.estimated_scan_cost_usd, 2)
                    if c.estimated_scan_cost_usd is not None
                    else None
                ),
            }
            for c in signal.top_clients_current[:5]
        ],
        "top_users": [
            {
                "user_name": u.user_name,
                "estimated_scan_cost_usd": (
                    round(u.estimated_scan_cost_usd, 2)
                    if u.estimated_scan_cost_usd is not None
                    else None
                ),
            }
            for u in signal.top_users_current[:5]
        ],
    }

    prompt = f"Write the bottom line for yesterday's Trino query-cost digest.\n\nData:\n{context}"

    try:
        response, _observation = await complete_with_budget_and_observation(
            session,
            prompt,
            invoke=LLMInvokeContext(
                feature="query_cost_digest_narration",
                prompt_name="query_cost_report_bottom_line",
                extra={"report_date": current.day.isoformat()},
            ),
            system=BOTTOM_LINE_SYSTEM_PROMPT,
            max_tokens=120,
        )
        if response is None:
            return None
        return response.content
    except Exception:
        logger.exception("Failed to generate AI bottom line for query-cost report")
        return None


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

    action_items = _build_action_items(signal)
    bottom_line = await _generate_bottom_line(session, signal)
    payload["action_items"] = action_items
    payload["bottom_line"] = bottom_line
    payload["bottom_line_used_llm"] = bottom_line is not None

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

    blocks = build_query_cost_slack_blocks(
        signal, generated_at=now, bottom_line=bottom_line, action_items=action_items
    )
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
