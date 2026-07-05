"""Logging filter that redacts sensitive keys from log records."""

from __future__ import annotations

import logging
import re
from typing import Any

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|secret|token|api[_-]?key|credential|webhook|authorization|"
    r"aws[_-]?secret|session[_-]?token|private[_-]?key|bearer)",
    re.IGNORECASE,
)

REDACTED = "***REDACTED***"


def _redact_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    if SENSITIVE_KEY_PATTERN.search(key):
        return REDACTED
    if isinstance(value, str) and SENSITIVE_KEY_PATTERN.search(value) and len(value) > 8:
        return REDACTED
    return value


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *data* with sensitive values redacted."""
    return {key: _redact_value(key, value) for key, value in data.items()}


class SensitiveDataFilter(logging.Filter):
    """Redact sensitive fields on structured log ``extra`` payloads."""

    def filter(self, record: logging.LogRecord) -> bool:
        for attr in ("msg", "args"):
            value = getattr(record, attr, None)
            if isinstance(value, dict):
                setattr(record, attr, redact_mapping(value))

        extra_keys = [key for key in record.__dict__ if key not in logging.LogRecord.__dict__]
        for key in extra_keys:
            if SENSITIVE_KEY_PATTERN.search(key):
                setattr(record, key, REDACTED)
                continue
            value = getattr(record, key)
            if isinstance(value, dict):
                setattr(record, key, redact_mapping(value))

        if isinstance(record.msg, str):
            record.msg = self._redact_message(record.msg)
        return True

    @staticmethod
    def _redact_message(message: str) -> str:
        return re.sub(
            r"(?i)(password|secret|token|api[_-]?key|webhook[_-]?url)\s*[=:]\s*\S+",
            r"\1=" + REDACTED,
            message,
        )
