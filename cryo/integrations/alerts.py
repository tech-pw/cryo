"""Dispatch anomaly alerts to Slack and optional PagerDuty."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from cryo.config import get_settings
from cryo.observability.metrics import anomaly_alerts_total

if TYPE_CHECKING:
    from cryo.ml.anomaly_detector import AnomalyAlert

logger = logging.getLogger(__name__)

PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"


def _severity_to_pagerduty(severity: str) -> str:
    return {"high": "critical", "medium": "error", "low": "warning"}.get(severity, "error")


def _post_json(url: str, payload: dict[str, Any], *, timeout: float = 10.0) -> None:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status >= 400:
            raise urllib.error.HTTPError(
                url, response.status, "alert dispatch failed", response.headers, response
            )


def format_slack_blocks(alerts: list[AnomalyAlert]) -> list[dict[str, Any]]:
    """Build Slack Block Kit payload for anomaly alerts."""
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Cryo anomaly alerts ({len(alerts)})"},
        }
    ]
    severity_emoji = {
        "high": ":red_circle:",
        "medium": ":large_orange_circle:",
        "low": ":large_yellow_circle:",
    }
    for alert in alerts[:10]:
        emoji = severity_emoji.get(alert.severity, ":grey_question:")
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{emoji} *{alert.table_name}* — `{alert.metric_name}`\n"
                        f"{alert.description}\n"
                        f"Severity: *{alert.severity}* | σ={alert.deviation_sigma:.1f}"
                    ),
                },
            }
        )
    if len(alerts) > 10:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_…and {len(alerts) - 10} more alerts_"}],
            }
        )
    return blocks


def send_slack_anomaly_alerts(
    alerts: list[AnomalyAlert],
    *,
    webhook_url: str | None = None,
) -> bool:
    """Post anomaly summary to a Slack incoming webhook."""
    if not alerts:
        return False

    settings = get_settings()
    url = webhook_url or settings.slack_webhook_url
    if not url:
        logger.debug("Slack webhook not configured; skipping anomaly alert dispatch")
        return False

    payload: dict[str, Any] = {
        "text": f"Cryo detected {len(alerts)} table anomalies",
        "blocks": format_slack_blocks(alerts),
    }
    if settings.slack_channel:
        payload["channel"] = settings.slack_channel

    try:
        _post_json(url, payload)
        for alert in alerts:
            anomaly_alerts_total.labels(
                metric_name=alert.metric_name,
                severity=alert.severity,
            ).inc()
        return True
    except Exception:
        logger.exception("Failed to send Slack anomaly alerts")
        return False


def send_pagerduty_anomaly_alerts(
    alerts: list[AnomalyAlert],
    *,
    routing_key: str | None = None,
) -> bool:
    """Trigger PagerDuty Events API v2 incidents for high-severity anomalies."""
    high_alerts = [alert for alert in alerts if alert.severity == "high"]
    if not high_alerts:
        return False

    settings = get_settings()
    key = routing_key or getattr(settings, "pagerduty_routing_key", None)
    if not key:
        logger.debug("PagerDuty routing key not configured; skipping dispatch")
        return False

    sent = False
    for alert in high_alerts:
        payload = {
            "routing_key": key,
            "event_action": "trigger",
            "dedup_key": f"cryo-anomaly-{alert.table_id}-{alert.metric_name}",
            "payload": {
                "summary": f"Cryo anomaly: {alert.table_name} — {alert.metric_name}",
                "source": "cryo-anomaly-detector",
                "severity": _severity_to_pagerduty(alert.severity),
                "component": "scanner",
                "group": "datalake-health",
                "class": alert.metric_name,
                "custom_details": {
                    "table_id": alert.table_id,
                    "table_name": alert.table_name,
                    "current_value": alert.current_value,
                    "expected_value": alert.expected_value,
                    "deviation_sigma": alert.deviation_sigma,
                    "description": alert.description,
                },
            },
        }
        try:
            _post_json(PAGERDUTY_EVENTS_URL, payload)
            anomaly_alerts_total.labels(
                metric_name=alert.metric_name,
                severity=alert.severity,
            ).inc()
            sent = True
        except Exception:
            logger.exception(
                "Failed to send PagerDuty alert for table=%s metric=%s",
                alert.table_name,
                alert.metric_name,
            )
    return sent


def dispatch_anomaly_alerts(
    alerts: list[AnomalyAlert],
    *,
    slack_webhook_url: str | None = None,
    pagerduty_routing_key: str | None = None,
) -> dict[str, bool]:
    """Send anomaly alerts to configured integrations."""
    return {
        "slack": send_slack_anomaly_alerts(alerts, webhook_url=slack_webhook_url),
        "pagerduty": send_pagerduty_anomaly_alerts(alerts, routing_key=pagerduty_routing_key),
    }
