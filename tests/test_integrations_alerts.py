"""Tests for cryo/integrations/alerts.py's Slack table-breach payload construction."""

from __future__ import annotations

from cryo.integrations import alerts


def test_send_slack_table_breach_alert_without_ai_summary(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(alerts, "validated_https_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(alerts, "_post_json", lambda url, payload: captured.update(payload=payload))

    ok = alerts.send_slack_table_breach_alert(
        "finance.orders", ["Freshness breached"], webhook_url="https://hooks.slack.test/x"
    )

    assert ok is True
    blocks = captured["payload"]["blocks"]
    assert len(blocks) == 1
    assert "Freshness breached" in blocks[0]["text"]["text"]


def test_send_slack_table_breach_alert_with_ai_summary_adds_extra_block(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(alerts, "validated_https_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(alerts, "_post_json", lambda url, payload: captured.update(payload=payload))

    ok = alerts.send_slack_table_breach_alert(
        "finance.orders",
        ["Freshness breached"],
        webhook_url="https://hooks.slack.test/x",
        ai_summary="Writer produced many small files (evidence: ...)",
    )

    assert ok is True
    blocks = captured["payload"]["blocks"]
    assert len(blocks) == 2
    assert "Likely cause" in blocks[1]["text"]["text"]
    assert "Writer produced many small files" in blocks[1]["text"]["text"]


def test_send_slack_table_breach_alert_no_reasons_skips_send(monkeypatch):
    called = False

    def _fake_post(url, payload):
        nonlocal called
        called = True

    monkeypatch.setattr(alerts, "_post_json", _fake_post)

    ok = alerts.send_slack_table_breach_alert(
        "finance.orders", [], webhook_url="https://hooks.slack.test/x", ai_summary="unused"
    )

    assert ok is False
    assert called is False


def test_send_slack_table_breach_alert_no_webhook_skips_send(monkeypatch):
    called = False

    def _fake_post(url, payload):
        nonlocal called
        called = True

    monkeypatch.setattr(alerts, "_post_json", _fake_post)
    monkeypatch.setattr(
        alerts, "get_settings", lambda: type("S", (), {"slack_webhook_url": None})()
    )

    ok = alerts.send_slack_table_breach_alert("finance.orders", ["Freshness breached"])

    assert ok is False
    assert called is False
