"""Utilities for redacting sensitive data from API responses."""

from __future__ import annotations

from typing import Any

# Common sensitive key patterns to redact
SENSITIVE_KEYS = {
    # AWS
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    # Generic
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "key",
    "credential",
    "credentials",
    # OAuth
    "client_secret",
    "oauth_token",
    "bearer_token",
    # Database
    "db_password",
    "jdbc_password",
    # Slack/Webhook
    "webhook_url",
    "slack_token",
}


def redact_sensitive_dict(data: dict[str, Any], _depth: int = 0) -> dict[str, Any]:
    """Redact sensitive fields in a dictionary recursively.

    Args:
        data: Dictionary that may contain sensitive keys
        _depth: Internal recursion guard (max 16 levels -- deeper structures are kept as-is).

    Returns:
        Copy of dictionary with sensitive values redacted
    """
    if not isinstance(data, dict):
        return data

    if _depth > 16:
        return data

    result: dict[str, Any] = {}
    seen: set[int] = set()
    for key, value in data.items():
        key_lower = key.lower()

        # Check if key matches any sensitive pattern
        is_sensitive = any(pattern in key_lower for pattern in SENSITIVE_KEYS)

        if is_sensitive and value:
            # Redact but show hint about value type
            if isinstance(value, str):
                result[key] = "***REDACTED***"
            else:
                result[key] = "***REDACTED***"
        elif isinstance(value, dict):
            ident = id(value)
            if ident in seen:
                result[key] = value
                continue
            seen.add(ident)
            result[key] = redact_sensitive_dict(value, _depth + 1)
        elif isinstance(value, list):
            result[key] = [
                redact_sensitive_dict(item, _depth + 1) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value

    return result


def is_sensitive_key(key: str) -> bool:
    """Check if a key name indicates sensitive data."""
    key_lower = key.lower()
    return any(pattern in key_lower for pattern in SENSITIVE_KEYS)


REDACTED_PLACEHOLDER = "***REDACTED***"


def unredact_merge(new_config: dict[str, Any], stored_config: dict[str, Any]) -> dict[str, Any]:
    """Replace any literal REDACTED_PLACEHOLDER leaf in `new_config` with the corresponding real
    value from `stored_config`, recursively, and preserve any key present in `stored_config` but
    absent from `new_config`.

    `redact_sensitive_dict` is what a client ever *sees* for a saved Source/Destination config
    (real secrets are masked before being sent to the browser). Without the placeholder swap,
    submitting an edit form without retyping an untouched password field would silently persist
    the literal string "***REDACTED***" as the new password, permanently destroying the real one.
    Without the base-from-stored-config merge, a caller submitting a partial config (e.g. adding
    one new key) would silently wipe every other key -- the config column is a single JSON blob
    overwritten wholesale on PATCH, not a per-field update, so a partial payload used to mean
    "delete everything else."
    """
    if not isinstance(new_config, dict):
        return new_config

    merged: dict[str, Any] = dict(stored_config) if isinstance(stored_config, dict) else {}
    for key, value in new_config.items():
        stored_value = stored_config.get(key) if isinstance(stored_config, dict) else None
        if value == REDACTED_PLACEHOLDER:
            merged[key] = stored_value
        elif isinstance(value, dict):
            stored_dict = stored_value if isinstance(stored_value, dict) else {}
            merged[key] = unredact_merge(value, stored_dict)
        elif isinstance(value, list) and isinstance(stored_value, list):
            merged[key] = [
                unredact_merge(item, stored_value[i])
                if isinstance(item, dict)
                and i < len(stored_value)
                and isinstance(stored_value[i], dict)
                else item
                for i, item in enumerate(value)
            ]
        else:
            merged[key] = value
    return merged
