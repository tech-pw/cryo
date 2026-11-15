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


_QUERY_CREDENTIAL_RE = re.compile(
    r"(?i)([?&](?:token|ticket|access_token|id_token|api_key|apikey|code)=)[^&\s\"']+"
)
ACCESS_LOG_LOGGERS = ("uvicorn.access", "uvicorn.error", "uvicorn")


def scrub_query_credentials(text: str) -> str:
    """Replace credential-bearing query parameter values in a URL or log line (#551)."""
    return _QUERY_CREDENTIAL_RE.sub(r"\1" + REDACTED, text)


class QueryStringCredentialFilter(logging.Filter):
    """Scrub ``?token=``/``?ticket=`` values from server access and WebSocket log lines.

    Uvicorn formats the request path with its query string into ``record.args``; its loggers
    do not propagate to the root logger, so the root ``SensitiveDataFilter`` never sees them.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub_query_credentials(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                scrub_query_credentials(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


def install_access_log_scrubbing() -> None:
    for name in ACCESS_LOG_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, QueryStringCredentialFilter) for f in logger.filters):
            logger.addFilter(QueryStringCredentialFilter())
