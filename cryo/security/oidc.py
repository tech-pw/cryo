"""OIDC bearer-token verification for production deployments."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient

from cryo.config import get_settings

_VALID_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")


@lru_cache
def _jwks_client(jwks_uri: str) -> PyJWKClient:
    return PyJWKClient(jwks_uri, cache_keys=True)


def oidc_enabled() -> bool:
    settings = get_settings()
    return bool(settings.oidc_issuer_url and settings.oidc_client_id)


def discover_oidc_endpoints() -> dict[str, str] | None:
    settings = get_settings()
    if not settings.oidc_issuer_url:
        return None
    import httpx

    issuer = settings.oidc_issuer_url.rstrip("/")
    try:
        response = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=10.0)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    authorization = payload.get("authorization_endpoint")
    token = payload.get("token_endpoint")
    jwks = payload.get("jwks_uri")
    if not isinstance(authorization, str) or not isinstance(token, str):
        return None
    endpoints: dict[str, str] = {
        "authorization_endpoint": authorization,
        "token_endpoint": token,
    }
    if isinstance(jwks, str):
        endpoints["jwks_uri"] = jwks
    return endpoints


def verify_oidc_access_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.oidc_issuer_url:
        return None

    endpoints = discover_oidc_endpoints()
    jwks_uri = (
        settings.oidc_jwks_uri
        or (endpoints or {}).get("jwks_uri")
        or f"{settings.oidc_issuer_url.rstrip('/')}/.well-known/jwks.json"
    )

    try:
        signing_key = _jwks_client(jwks_uri).get_signing_key_from_jwt(token)
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(_VALID_ALGORITHMS),
            audience=settings.oidc_audience or settings.oidc_client_id,
            issuer=settings.oidc_issuer_url.rstrip("/"),
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


def exchange_authorization_code(*, code: str, redirect_uri: str) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.oidc_issuer_url or not settings.oidc_client_id or not settings.oidc_client_secret:
        return None

    endpoints = discover_oidc_endpoints()
    token_endpoint = (endpoints or {}).get("token_endpoint")
    if not token_endpoint:
        return None

    import httpx

    try:
        response = httpx.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
            },
            headers={"Accept": "application/json"},
            timeout=15.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def tenant_id_from_oidc_claims(claims: dict[str, Any]) -> uuid.UUID | None:
    for key in ("tenant_id", "tid", "org_id"):
        raw = claims.get(key)
        if raw is None:
            continue
        try:
            return uuid.UUID(str(raw))
        except ValueError:
            continue
    return None
