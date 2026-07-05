"""Authentication helpers for local/dev deployments."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from cryo.api.deps import require_admin
from cryo.config import get_settings
from cryo.db.session import SessionLocal
from cryo.governor.bandit_store import save_bandit
from cryo.security.jwt_tokens import issue_access_token
from cryo.security.oidc import (
    discover_oidc_endpoints,
    exchange_authorization_code,
    oidc_enabled,
    refresh_oidc_token,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class TokenRequest(BaseModel):
    role: str = Field(description="viewer, operator, or admin")
    subject: str = "dev-user"
    tenant_id: uuid.UUID | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    expires_in: int | None = None


class OidcRefreshRequest(BaseModel):
    refresh_token: str


class OidcPublicConfig(BaseModel):
    client_id: str
    authorization_endpoint: str
    token_endpoint: str
    redirect_uri: str


class AuthConfigResponse(BaseModel):
    mode: Literal["dev", "oidc"]
    dev_token_enabled: bool
    oidc: OidcPublicConfig | None = None


class OidcExchangeRequest(BaseModel):
    code: str
    redirect_uri: str


@router.get("/config", response_model=AuthConfigResponse)
def auth_config() -> AuthConfigResponse:
    """Expose auth mode for the UI (dev JWT vs production OIDC)."""
    settings = get_settings()
    dev_token_enabled = settings.environment != "prod"
    if oidc_enabled():
        endpoints = discover_oidc_endpoints()
        if endpoints is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OIDC issuer discovery failed",
            )
        redirect_uri = settings.oidc_redirect_uri or "http://localhost:5173/login/callback"
        return AuthConfigResponse(
            mode="oidc",
            dev_token_enabled=dev_token_enabled,
            oidc=OidcPublicConfig(
                client_id=settings.oidc_client_id or "",
                authorization_endpoint=endpoints["authorization_endpoint"],
                token_endpoint=endpoints["token_endpoint"],
                redirect_uri=redirect_uri,
            ),
        )
    return AuthConfigResponse(mode="dev", dev_token_enabled=dev_token_enabled, oidc=None)


@router.post("/oidc/exchange", response_model=TokenResponse)
def exchange_oidc_token(payload: OidcExchangeRequest) -> TokenResponse:
    """Exchange an authorization code for an IdP access token."""
    if not oidc_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC is not configured")
    token_payload = exchange_authorization_code(
        code=payload.code,
        redirect_uri=payload.redirect_uri,
    )
    if token_payload is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Code exchange failed")
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="IdP response missing access_token",
        )
    return TokenResponse(
        access_token=access_token,
        refresh_token=token_payload.get("refresh_token")
        if isinstance(token_payload.get("refresh_token"), str)
        else None,
        expires_in=int(token_payload["expires_in"])
        if isinstance(token_payload.get("expires_in"), (int, float))
        else None,
    )


@router.post("/oidc/refresh", response_model=TokenResponse)
def refresh_oidc_access_token(payload: OidcRefreshRequest) -> TokenResponse:
    """Refresh an OIDC access token using a refresh token."""
    if not oidc_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC is not configured")
    token_payload = refresh_oidc_token(payload.refresh_token)
    if token_payload is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Refresh failed")
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="IdP response missing access_token",
        )
    return TokenResponse(
        access_token=access_token,
        refresh_token=token_payload.get("refresh_token")
        if isinstance(token_payload.get("refresh_token"), str)
        else payload.refresh_token,
        expires_in=int(token_payload["expires_in"])
        if isinstance(token_payload.get("expires_in"), (int, float))
        else None,
    )


@router.post("/token", response_model=TokenResponse)
def issue_dev_token(payload: TokenRequest) -> TokenResponse:
    """Issue a signed JWT for dev/test UI and integration tests.

    Disabled in production — use your identity provider instead.
    """
    settings = get_settings()
    if settings.environment == "prod":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Token issuance is disabled in production",
        )
    role = payload.role.strip().lower()
    if role not in {"viewer", "operator", "admin"}:
        raise HTTPException(status_code=400, detail="role must be viewer, operator, or admin")
    token = issue_access_token(
        subject=payload.subject,
        roles=[role],
        tenant_id=payload.tenant_id,
    )
    return TokenResponse(access_token=token)


@router.post(
    "/governor/bandit/reset",
    dependencies=[Depends(require_admin)],
    tags=["governor"],
)
def reset_admission_bandit() -> dict[str, str]:
    """Clear learned admission-bandit state and revert to static heuristics."""
    from cryo.governor.bandit import AdmissionBandit

    with SessionLocal() as session:
        save_bandit(session, AdmissionBandit())
    return {"status": "reset"}
