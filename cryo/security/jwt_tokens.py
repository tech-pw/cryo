"""Signed JWT access tokens for Cryo API authentication."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from cryo.config import get_settings

_ALGORITHM = "HS256"
_DEFAULT_TTL_HOURS = 24
_VALID_ROLES = frozenset({"evidence_collector", "viewer", "operator", "admin"})


def jwt_secret() -> str:
    settings = get_settings()
    return settings.jwt_secret or settings.secret_key


def issue_access_token(
    *,
    subject: str,
    roles: list[str],
    tenant_id: uuid.UUID | None = None,
    ttl_hours: int = _DEFAULT_TTL_HOURS,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "roles": roles,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=ttl_hours)).timestamp()),
    }
    if tenant_id is not None:
        payload["tenant_id"] = str(tenant_id)
    if extra_claims:
        payload.update({k: v for k, v in extra_claims.items() if k not in payload})
    return jwt.encode(payload, jwt_secret(), algorithm=_ALGORITHM)


def verify_access_token(token: str) -> dict[str, Any] | None:
    try:
        decoded = jwt.decode(
            token,
            jwt_secret(),
            algorithms=[_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        decoded = None
    if isinstance(decoded, dict):
        return decoded

    from cryo.security.oidc import oidc_enabled, verify_oidc_access_token

    if oidc_enabled():
        return verify_oidc_access_token(token)
    return None


def roles_from_claims(claims: dict[str, Any]) -> frozenset[str]:
    raw: list[str] = []
    for key in ("role", "roles", "scope", "scopes", "groups"):
        value = claims.get(key)
        if isinstance(value, str):
            raw.extend(value.replace(",", " ").split())
        elif isinstance(value, list):
            raw.extend(str(item) for item in value if item is not None)
    return frozenset(v.lower() for v in raw if v.lower() in _VALID_ROLES)


def tenant_id_from_claims(claims: dict[str, Any]) -> uuid.UUID | None:
    raw = claims.get("tenant_id")
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None
