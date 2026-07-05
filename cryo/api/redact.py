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


def redact_sensitive_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive fields in a dictionary recursively.
    
    Args:
        data: Dictionary that may contain sensitive keys
        
    Returns:
        Copy of dictionary with sensitive values redacted
    """
    if not isinstance(data, dict):
        return data
    
    result = {}
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
            # Recursively redact nested dicts
            result[key] = redact_sensitive_dict(value)
        elif isinstance(value, list):
            # Recursively redact lists of dicts
            result[key] = [
                redact_sensitive_dict(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value
    
    return result


def is_sensitive_key(key: str) -> bool:
    """Check if a key name indicates sensitive data."""
    key_lower = key.lower()
    return any(pattern in key_lower for pattern in SENSITIVE_KEYS)
