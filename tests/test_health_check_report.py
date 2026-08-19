"""Tests for cryo/reporting/health_check_report.py's Slack fan-out to SlackConfig rows."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from cryo.db.models import SlackConfig
from cryo.reporting.health_check_report import notify_production_health_check
from cryo.security.crypto import encrypt_secret

NOW = datetime(2026, 8, 19, 9, 45, tzinfo=UTC)


def test_notify_posts_unconditionally_even_when_all_checks_pass(session, monkeypatch) -> None:
    session.add(
        SlackConfig(
            id=uuid4(),
            name="cryo-channel",
            webhook_url_encrypted=encrypt_secret("https://hooks.slack.test/x"),
            channel="#cryo",
            enabled=True,
        )
    )
    session.commit()

    class FakeSlack:
        def __init__(self, webhook_url: str, channel: str | None = None) -> None:
            self.webhook_url = webhook_url
            self.channel = channel

        async def send_blocks(self, blocks: object, fallback_text: str = "") -> bool:
            assert blocks
            return True

    monkeypatch.setattr("cryo.reporting.health_check_report.SlackClient", FakeSlack)
    result = asyncio.run(notify_production_health_check(session, now=NOW))

    assert result["overall_status"] == "OK"
    assert result["slack_sent"] is True
    assert result["slack_targets"] == 1


def test_notify_skips_slack_when_no_config(session) -> None:
    result = asyncio.run(notify_production_health_check(session, now=NOW))

    assert result["slack_sent"] is False
    assert result["slack_skipped_reason"] == "no_slack_config"
    assert result["slack_targets"] == 0


def test_notify_fans_out_to_every_enabled_config(session, monkeypatch) -> None:
    session.add_all(
        [
            SlackConfig(
                id=uuid4(),
                name="channel-a",
                webhook_url_encrypted=encrypt_secret("https://hooks.slack.test/a"),
                enabled=True,
            ),
            SlackConfig(
                id=uuid4(),
                name="channel-b",
                webhook_url_encrypted=encrypt_secret("https://hooks.slack.test/b"),
                enabled=True,
            ),
            SlackConfig(
                id=uuid4(),
                name="disabled-channel",
                webhook_url_encrypted=encrypt_secret("https://hooks.slack.test/c"),
                enabled=False,
            ),
        ]
    )
    session.commit()

    sent_to: list[str] = []

    class FakeSlack:
        def __init__(self, webhook_url: str, channel: str | None = None) -> None:
            sent_to.append(webhook_url)

        async def send_blocks(self, blocks: object, fallback_text: str = "") -> bool:
            return True

    monkeypatch.setattr("cryo.reporting.health_check_report.SlackClient", FakeSlack)
    result = asyncio.run(notify_production_health_check(session, now=NOW))

    assert result["slack_sent"] is True
    assert result["slack_targets"] == 2
    assert sorted(sent_to) == ["https://hooks.slack.test/a", "https://hooks.slack.test/b"]


def test_notify_reports_send_failure(session, monkeypatch) -> None:
    session.add(
        SlackConfig(
            id=uuid4(),
            name="cryo-channel",
            webhook_url_encrypted=encrypt_secret("https://hooks.slack.test/x"),
            enabled=True,
        )
    )
    session.commit()

    class FailingSlack:
        def __init__(self, webhook_url: str, channel: str | None = None) -> None:
            pass

        async def send_blocks(self, blocks: object, fallback_text: str = "") -> bool:
            return False

    monkeypatch.setattr("cryo.reporting.health_check_report.SlackClient", FailingSlack)
    result = asyncio.run(notify_production_health_check(session, now=NOW))

    assert result["slack_sent"] is False
    assert result["slack_skipped_reason"] == "send_failed"
    assert result["slack_targets"] == 0
