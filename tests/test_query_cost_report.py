"""Tests for cryo/reporting/query_cost_report.py's Slack fan-out to SlackConfig rows."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from cryo.db.models import QueryCostAttributionDaily, SlackConfig, Source
from cryo.reporting.query_cost_report import notify_query_cost_report
from cryo.security.crypto import encrypt_secret

NOW = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_row(session, source: Source, *, day, cost: float = 10.0) -> None:
    session.add(
        QueryCostAttributionDaily(
            source_id=source.id,
            day=day,
            client="Metabase",
            schema_name="gold",
            query_count=5,
            failed_query_count=0,
            physical_bytes_scanned=1024**3,
            estimated_scan_cost_usd=cost,
        )
    )


def test_notify_posts_unconditionally_even_when_stable(session, monkeypatch) -> None:
    """Unlike the shared-S3 report (which skips Slack with nothing to report), this always
    posts -- it's a visibility report, not an alert."""
    source = _seed_source(session)
    for delta in (1, 2, 8):
        _seed_row(session, source, day=(NOW - timedelta(days=delta)).date(), cost=10.0)
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

    monkeypatch.setattr("cryo.reporting.query_cost_report.SlackClient", FakeSlack)
    result = asyncio.run(notify_query_cost_report(session, now=NOW))

    assert result["direction"] == "stable"
    assert result["slack_sent"] is True
    assert result["slack_targets"] == 1


def test_notify_skips_slack_when_no_config(session) -> None:
    source = _seed_source(session)
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date())
    session.commit()

    result = asyncio.run(notify_query_cost_report(session, now=NOW))

    assert result["slack_sent"] is False
    assert result["slack_skipped_reason"] == "no_slack_config"
    assert result["slack_targets"] == 0


def test_notify_fans_out_to_every_enabled_config(session, monkeypatch) -> None:
    source = _seed_source(session)
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date(), cost=200.0)
    _seed_row(session, source, day=(NOW - timedelta(days=2)).date(), cost=100.0)
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

    monkeypatch.setattr("cryo.reporting.query_cost_report.SlackClient", FakeSlack)
    result = asyncio.run(notify_query_cost_report(session, now=NOW))

    assert result["direction"] == "spike"
    assert result["slack_sent"] is True
    assert result["slack_targets"] == 2
    assert sorted(sent_to) == ["https://hooks.slack.test/a", "https://hooks.slack.test/b"]


def test_notify_reports_send_failure(session, monkeypatch) -> None:
    source = _seed_source(session)
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date())
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

    monkeypatch.setattr("cryo.reporting.query_cost_report.SlackClient", FailingSlack)
    result = asyncio.run(notify_query_cost_report(session, now=NOW))

    assert result["slack_sent"] is False
    assert result["slack_skipped_reason"] == "send_failed"
    assert result["slack_targets"] == 0


def test_notify_payload_includes_top_clients(session, monkeypatch) -> None:
    source = _seed_source(session)
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date(), cost=10.0)
    session.commit()

    monkeypatch.setattr("cryo.reporting.query_cost_report.SlackClient", MagicMock())
    result = asyncio.run(notify_query_cost_report(session, now=NOW))

    assert result["top_clients"]
    assert result["top_clients"][0]["client"] == "Metabase"
    assert result["current"]["estimated_scan_cost_usd"] == 10.0


def test_notify_payload_includes_top_users(session, monkeypatch) -> None:
    """See docs/QUERY_COST_USER_ATTRIBUTION_PLAN_2026-08.md -- _seed_row doesn't set user_name,
    so this falls back to the model's 'unknown' default, same as every row ingested before the
    user-attribution migration/ingestion change."""
    source = _seed_source(session)
    _seed_row(session, source, day=(NOW - timedelta(days=1)).date(), cost=10.0)
    session.commit()

    monkeypatch.setattr("cryo.reporting.query_cost_report.SlackClient", MagicMock())
    result = asyncio.run(notify_query_cost_report(session, now=NOW))

    assert result["top_users"]
    assert result["top_users"][0]["user_name"] == "unknown"
    assert result["top_users"][0]["estimated_scan_cost_usd"] == 10.0
