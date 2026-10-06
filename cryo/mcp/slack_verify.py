"""Slack request signature check. No network calls."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

MAX_AGE_SECONDS = 300


def verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
    secret: str,
    *,
    now: datetime | None = None,
) -> bool:
    if not secret or not timestamp or not signature or not signature.startswith("v0="):
        return False
    try:
        issued = int(timestamp)
    except ValueError:
        return False
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if current.timestamp() - issued > MAX_AGE_SECONDS:
        return False
    basestring = b"v0:" + timestamp.encode("utf-8") + b":" + body
    expected = "v0=" + hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
