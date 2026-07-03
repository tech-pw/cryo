"""Slack notification client for Cryo reports."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SlackBlock:
    """Represents a Slack Block Kit block."""

    type: str
    text: dict[str, Any] | None = None
    elements: list[dict[str, Any]] | None = None
    fields: list[dict[str, Any]] | None = None
    accessory: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.type}
        if self.text:
            result["text"] = self.text
        if self.elements:
            result["elements"] = self.elements
        if self.fields:
            result["fields"] = self.fields
        if self.accessory:
            result["accessory"] = self.accessory
        return result


def header_block(text: str) -> SlackBlock:
    return SlackBlock(type="header", text={"type": "plain_text", "text": text, "emoji": True})


def section_block(text: str, markdown: bool = True) -> SlackBlock:
    return SlackBlock(
        type="section", text={"type": "mrkdwn" if markdown else "plain_text", "text": text}
    )


def fields_block(fields: list[tuple[str, str]]) -> SlackBlock:
    return SlackBlock(
        type="section",
        fields=[{"type": "mrkdwn", "text": f"*{label}*\n{value}"} for label, value in fields],
    )


def divider_block() -> SlackBlock:
    return SlackBlock(type="divider")


def context_block(texts: list[str]) -> SlackBlock:
    return SlackBlock(
        type="context",
        elements=[{"type": "mrkdwn", "text": t} for t in texts],
    )


class SlackClient:
    """Client for sending messages to Slack via incoming webhooks."""

    def __init__(self, webhook_url: str, channel: str | None = None):
        self.webhook_url = webhook_url
        self.channel = channel

    async def send_message(
        self,
        text: str,
        blocks: list[SlackBlock] | None = None,
        *,
        thread_ts: str | None = None,
    ) -> bool:
        """Send a message to Slack.

        Args:
            text: Fallback text for notifications
            blocks: Block Kit blocks for rich formatting
            thread_ts: Thread timestamp to reply in thread

        Returns:
            True if sent successfully
        """
        payload: dict[str, Any] = {"text": text}

        if blocks:
            payload["blocks"] = [b.to_dict() for b in blocks]
        if self.channel:
            payload["channel"] = self.channel
        if thread_ts:
            payload["thread_ts"] = thread_ts

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.webhook_url, json=payload)
                if response.status_code == 200:
                    logger.info("Slack message sent successfully")
                    return True
                else:
                    logger.error(f"Slack API error: {response.status_code} - {response.text}")
                    return False
        except Exception as e:
            logger.exception(f"Failed to send Slack message: {e}")
            return False

    async def send_blocks(
        self, blocks: list[SlackBlock], fallback_text: str = "Cryo Report"
    ) -> bool:
        """Convenience method to send a block-based message."""
        return await self.send_message(fallback_text, blocks)
