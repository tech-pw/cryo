"""Slack incoming-webhook URLs are the credential itself, and httpx logs every URL at INFO."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from cryo.observability.logging import quiet_third_party_loggers

CRYO = Path(__file__).resolve().parents[1] / "cryo"


def test_httpx_request_lines_are_not_emitted(caplog: pytest.LogCaptureFixture) -> None:
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    quiet_third_party_loggers()

    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info(
            'HTTP Request: POST https://hooks.slack.com/services/T0/B0/secret "HTTP/1.1 200 OK"'
        )

    assert "hooks.slack.com" not in caplog.text


def test_every_basic_config_entrypoint_also_quiets_third_party_loggers() -> None:
    offenders = [
        str(path.relative_to(CRYO))
        for path in CRYO.rglob("*.py")
        if "logging.basicConfig(" in (text := path.read_text())
        and "quiet_third_party_loggers()" not in text
        and path.name != "logging.py"
    ]

    assert offenders == []
