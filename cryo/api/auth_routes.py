"""Authentication helpers for local/dev deployments."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from cryo.api.deps import CurrentPrincipal, require_admin, require_viewer
from cryo.api.middleware import client_ip
from cryo.config import Settings, get_settings
from cryo.db.models import AuditLog, AuthSettings, LocalUser
from cryo.db.session import SessionLocal
from cryo.governor.bandit_store import save_bandit
from cryo.observability.metrics import auth_login_failures_total
from cryo.security.google_id_token import verify_google_id_token
from cryo.security.jwt_tokens import issue_access_token
from cryo.security.local_users import LOCAL_AUTH, invalidate_local_user
from cryo.security.login_throttle import get_login_throttle
from cryo.security.oidc import (
    discover_oidc_endpoints,
    exchange_authorization_code,
    oidc_enabled,
    refresh_oidc_token,
)
from cryo.security.passwords import hash_password, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

AUTH_AUDIT_PREFIX = "auth."
LOGIN_FAILED_ACTION = "auth.login_failed"
_AUDIT_ACCOUNT_MAX = 254  # longest valid email; anything longer is truncated in the audit row


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


class GooglePublicConfig(BaseModel):
    client_id: str
    hosted_domain: str | None = None
    auto_provision: bool = Field(
        default=False,
        description="New accounts on hosted_domain are let in as viewers on first sign-in.",
    )


class AuthConfigResponse(BaseModel):
    mode: Literal["dev", "oidc"]
    dev_token_enabled: bool
    dev_token_enabled_in_prod: bool
    password_login_enabled: bool = False
    oidc: OidcPublicConfig | None = None
    google: GooglePublicConfig | None = None


class OidcExchangeRequest(BaseModel):
    code: str
    redirect_uri: str
    code_verifier: str | None = None


def _dev_token_enabled(settings: Settings) -> bool:
    return settings.environment != "prod" or settings.allow_dev_token_in_prod


def _dev_token_enabled_in_prod(settings: Settings) -> bool:
    return settings.environment == "prod" and settings.allow_dev_token_in_prod


@router.get("/config", response_model=AuthConfigResponse)
def auth_config() -> AuthConfigResponse:
    """Expose auth mode for the UI (dev JWT vs production OIDC)."""
    settings = get_settings()
    dev_token_enabled = _dev_token_enabled(settings)
    dev_token_enabled_in_prod = _dev_token_enabled_in_prod(settings)
    if oidc_enabled():
        endpoints = discover_oidc_endpoints()
        if endpoints is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OIDC issuer discovery failed",
            )
        redirect_uri = settings.oidc_redirect_uri or "http://localhost:5173/login/callback"
        settings_row = _auth_settings()
        google = None
        if settings_row.google_enabled and settings_row.google_client_id:
            google = _google_public_config(settings_row)
        return AuthConfigResponse(
            mode="oidc",
            dev_token_enabled=dev_token_enabled,
            dev_token_enabled_in_prod=dev_token_enabled_in_prod,
            password_login_enabled=settings_row.password_login_enabled,
            google=google,
            oidc=OidcPublicConfig(
                client_id=settings.oidc_client_id or "",
                authorization_endpoint=endpoints["authorization_endpoint"],
                token_endpoint=endpoints["token_endpoint"],
                redirect_uri=redirect_uri,
            ),
        )
    settings_row = _auth_settings()
    google = None
    if settings_row.google_enabled and settings_row.google_client_id:
        google = _google_public_config(settings_row)
    return AuthConfigResponse(
        mode="dev",
        dev_token_enabled=dev_token_enabled,
        dev_token_enabled_in_prod=dev_token_enabled_in_prod,
        password_login_enabled=settings_row.password_login_enabled,
        oidc=None,
        google=google,
    )


@router.post("/oidc/exchange", response_model=TokenResponse)
def exchange_oidc_token(payload: OidcExchangeRequest) -> TokenResponse:
    """Exchange an authorization code for an IdP access token."""
    if not oidc_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OIDC is not configured")
    token_payload = exchange_authorization_code(
        code=payload.code,
        redirect_uri=payload.redirect_uri,
        code_verifier=payload.code_verifier,
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

    Disabled in production unless allow_dev_token_in_prod is explicitly set -- use your
    identity provider instead.
    """
    settings = get_settings()
    if not _dev_token_enabled(settings):
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


_ASSIGNABLE = {"viewer", "operator", "admin"}
_PERMISSIONS = [
    {"role": "viewer", "can": "Read catalogs, runs, and health. Cannot start maintenance."},
    {"role": "operator", "can": "Run maintenance. Cannot change users or sign-in."},
    {"role": "admin", "can": "Operator rights, plus users, Google sign-in, and connections."},
]


def _auth_settings() -> AuthSettings:
    try:
        with SessionLocal() as session:
            row = session.get(AuthSettings, 1)
            if row is None:
                row = AuthSettings(id=1)
                session.add(row)
                session.commit()
                session.refresh(row)
            session.expunge(row)
            return row
    except SQLAlchemyError:
        return AuthSettings(
            id=1,
            password_login_enabled=False,
            google_enabled=False,
            google_auto_provision=False,
        )


def _google_public_config(row: AuthSettings) -> GooglePublicConfig:
    return GooglePublicConfig(
        client_id=(row.google_client_id or "").strip(),
        hosted_domain=_normalize_domain(row.google_hosted_domain),
        auto_provision=_auto_provision_active(row),
    )


def _auto_provision_active(row: AuthSettings) -> bool:
    """Auto-provision only ever applies behind a domain restriction: without one, any Google
    account in the world would get a viewer seat."""
    return bool(row.google_auto_provision and _normalize_domain(row.google_hosted_domain))


def _issue_for_user(session: Session, user: LocalUser, method: str) -> TokenResponse:
    user.last_login_at = datetime.now(UTC)
    user.last_login_method = method
    session.commit()
    token = issue_access_token(
        subject=user.username,
        roles=[user.role],
        extra_claims={"auth": LOCAL_AUTH, "uid": str(user.id), "amr": method},
    )
    return TokenResponse(access_token=token)


def _audit(session: Session, action: str, actor: str, detail: str) -> None:
    session.add(
        AuditLog(action=f"auth.{action}", status="OK", actor=actor, origin="auth", detail=detail)
    )


class LoginRequest(BaseModel):
    username: str
    password: str


class GoogleLoginRequest(BaseModel):
    id_token: str


class GoogleConfigTestRequest(BaseModel):
    """A candidate Client ID/domain, tested against a real Google ID token obtained from a
    one-off sign-in popup -- never against saved AuthSettings, so admins can verify a change
    before persisting it (the same "test before save" shape as a destination connection test)."""

    id_token: str
    client_id: str
    hosted_domain: str | None = None


class GoogleConfigTestResponse(BaseModel):
    ok: bool
    email: str | None = None
    message: str


class UserWrite(BaseModel):
    username: str
    password: str | None = Field(
        default=None,
        min_length=8,
        description="Omit for a Google-only account; the username must then be its email.",
    )
    role: str = "viewer"


class UserPatch(BaseModel):
    role: str | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class UserRead(BaseModel):
    id: uuid.UUID
    username: str
    role: str
    disabled: bool
    has_password: bool
    created_at: datetime | None = None
    created_via: str | None = None
    last_login_at: datetime | None = None
    last_login_method: str | None = None


def _user_read(row: LocalUser) -> UserRead:
    return UserRead(
        id=row.id,
        username=row.username,
        role=row.role,
        disabled=row.disabled,
        has_password=bool(row.password_hash),
        created_at=row.created_at,
        created_via=row.created_via,
        last_login_at=row.last_login_at,
        last_login_method=row.last_login_method,
    )


class MeResponse(BaseModel):
    subject: str
    role: str
    managed: bool = Field(description="Signed in with a Cryo password or Google account.")


class AuthSettingsRead(BaseModel):
    password_login_enabled: bool
    google_enabled: bool
    google_client_id: str | None
    google_hosted_domain: str | None
    google_auto_provision: bool
    google_admin_emails: list[str] = Field(
        default_factory=list,
        description="Enabled admins who can sign in with Google under the current settings.",
    )


class AuthSettingsPatch(BaseModel):
    password_login_enabled: bool | None = None
    google_enabled: bool | None = None
    google_client_id: str | None = None
    google_hosted_domain: str | None = None
    google_auto_provision: bool | None = None


def _check_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized not in _ASSIGNABLE:
        raise HTTPException(status_code=400, detail="role must be viewer, operator, or admin")
    return normalized


def _normalize_domain(value: str | None) -> str | None:
    domain = (value or "").strip().lower().lstrip("@")
    return domain or None


def _bootstrap_admins(settings: Settings) -> set[str]:
    return {
        email.strip().lower() for email in settings.google_admin_emails.split(",") if email.strip()
    }


def _google_can_sign_in(username: str, hosted_domain: str | None) -> bool:
    if "@" not in username:
        return False
    domain = _normalize_domain(hosted_domain)
    return domain is None or username.endswith(f"@{domain}")


def _google_admin_emails(session: Session, row: AuthSettings) -> list[str]:
    admins = (
        session.query(LocalUser.username)
        .filter(LocalUser.role == "admin", LocalUser.disabled.is_(False))
        .order_by(LocalUser.username)
        .all()
    )
    return [
        username
        for (username,) in admins
        if _google_can_sign_in(username, row.google_hosted_domain)
    ]


def _settings_read(session: Session, row: AuthSettings) -> AuthSettingsRead:
    return AuthSettingsRead(
        password_login_enabled=row.password_login_enabled,
        google_enabled=row.google_enabled,
        google_client_id=row.google_client_id,
        google_hosted_domain=row.google_hosted_domain,
        google_auto_provision=_auto_provision_active(row),
        google_admin_emails=_google_admin_emails(session, row),
    )


def _last_admin(session: Session, excluding: uuid.UUID | None = None) -> bool:
    rows = (
        session.query(LocalUser)
        .filter(LocalUser.role == "admin", LocalUser.disabled.is_(False))
        .all()
    )
    remaining = [row for row in rows if row.id != excluding]
    return len(remaining) == 0


def _throttle_or_429(*, account: str | None, ip: str | None, method: str) -> None:
    decision = get_login_throttle().check(account=account, client_ip=ip)
    if decision.allowed:
        return
    auth_login_failures_total.labels(method=method, reason="throttled").inc()
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed sign-in attempts. Try again later.",
        headers={"Retry-After": str(decision.retry_after_seconds)},
    )


def _record_login_failure(*, account: str | None, ip: str | None, method: str, reason: str) -> None:
    """Count, throttle and audit one failed sign-in. Never stores the password or token."""
    get_login_throttle().record_failure(account=account, client_ip=ip)
    auth_login_failures_total.labels(method=method, reason=reason).inc()
    detail = f"method={method} reason={reason}"
    if ip:
        detail += f" client_ip={ip}"
    try:
        with SessionLocal() as session:
            session.add(
                AuditLog(
                    action=LOGIN_FAILED_ACTION,
                    status="failed",
                    actor=(account or "")[:_AUDIT_ACCOUNT_MAX] or None,
                    origin=method,
                    detail=detail,
                )
            )
            session.commit()
    except SQLAlchemyError:
        # The 401 must not turn into a 500 because the audit write failed; the counter
        # above still records the attempt.
        logger.warning("Could not write %s audit row", LOGIN_FAILED_ACTION, exc_info=True)


@router.post("/login", response_model=TokenResponse)
def login_with_password(payload: LoginRequest, request: Request) -> TokenResponse:
    settings_row = _auth_settings()
    if not settings_row.password_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Password sign-in is disabled"
        )
    username = payload.username.strip().lower()
    ip = client_ip(request)
    _throttle_or_429(account=username, ip=ip, method="password")
    with SessionLocal() as session:
        user = session.query(LocalUser).filter(LocalUser.username == username).one_or_none()
        if user is None:
            reason = "unknown_user"
        elif user.disabled:
            reason = "disabled"
        elif not verify_password(payload.password, user.password_hash):
            reason = "bad_password"
        else:
            get_login_throttle().record_success(account=username)
            return _issue_for_user(session, user, "password")
    _record_login_failure(account=username, ip=ip, method="password", reason=reason)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password"
    )


@router.post("/google", response_model=TokenResponse)
def login_with_google(payload: GoogleLoginRequest, request: Request) -> TokenResponse:
    settings_row = _auth_settings()
    if not settings_row.google_enabled or not settings_row.google_client_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Google sign-in is not configured"
        )
    ip = client_ip(request)
    # The account is unknown until the token verifies, so the pre-check is per IP only.
    _throttle_or_429(account=None, ip=ip, method="google")
    claims = verify_google_id_token(
        payload.id_token,
        client_id=settings_row.google_client_id.strip(),
        hosted_domain=_normalize_domain(settings_row.google_hosted_domain),
    )
    if claims is None:
        _record_login_failure(account=None, ip=ip, method="google", reason="token_rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Google sign-in was rejected"
        )
    email = claims["email"].strip().lower()
    _throttle_or_429(account=email, ip=None, method="google")
    with SessionLocal() as session:
        user = session.query(LocalUser).filter(LocalUser.username == email).one_or_none()
        if user is None:
            if email in _bootstrap_admins(get_settings()):
                user = LocalUser(username=email, role="admin", created_via="bootstrap")
            elif _auto_provision_active(settings_row):
                user = LocalUser(username=email, role="viewer", created_via="google")
            if user is not None:
                session.add(user)
                _audit(
                    session,
                    "user_created",
                    email,
                    f"user={email}; role={user.role}; via={user.created_via}",
                )
                session.commit()
                session.refresh(user)
        if user is None:
            _record_login_failure(account=email, ip=ip, method="google", reason="not_allowed")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"{email} does not have a Cryo account. Ask an admin to add it under "
                    "Settings > Access & policy."
                ),
            )
        if user.disabled:
            _record_login_failure(account=email, ip=ip, method="google", reason="disabled")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"The Cryo account for {email} is disabled. Ask an admin to re-enable it.",
            )
        get_login_throttle().record_success(account=email)
        return _issue_for_user(session, user, "google")


class AuthAuditItem(BaseModel):
    id: int
    ts: datetime
    action: str
    status: str
    actor: str | None = None
    origin: str | None = None
    detail: str | None = None


@router.get("/audit", response_model=list[AuthAuditItem], dependencies=[Depends(require_admin)])
def list_auth_audit(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuthAuditItem]:
    """Recent sign-in audit rows (failed sign-ins), newest first. Admin only: the rows hold
    the usernames people tried, so they are kept out of the viewer-readable fleet audit."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(AuditLog)
            .where(AuditLog.action.startswith(AUTH_AUDIT_PREFIX))
            .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
            .limit(limit)
        ).all()
        return [
            AuthAuditItem(
                id=row.id,
                ts=row.ts,
                action=row.action,
                status=row.status,
                actor=row.actor,
                origin=row.origin,
                detail=row.detail,
            )
            for row in rows
        ]


@router.post(
    "/settings/google/test",
    response_model=GoogleConfigTestResponse,
    dependencies=[Depends(require_admin)],
)
def test_google_config(payload: GoogleConfigTestRequest) -> GoogleConfigTestResponse:
    """Verify a candidate Client ID/domain against a real ID token the admin just obtained
    from a one-off Google sign-in popup -- confirms the Client ID is real, the token's
    audience matches it, and (if set) the signed-in account is actually on the allowed
    domain, all before anything is saved to AuthSettings."""
    hosted_domain = _normalize_domain(payload.hosted_domain)
    claims = verify_google_id_token(
        payload.id_token,
        client_id=payload.client_id.strip(),
        hosted_domain=hosted_domain,
    )
    if claims is None:
        domain_hint = f" and that your account is on {hosted_domain}" if hosted_domain else ""
        return GoogleConfigTestResponse(
            ok=False,
            message=(
                "Google rejected this sign-in for the given Client ID. Check that the "
                "Client ID is correct, that this origin is in its Authorized JavaScript "
                f"origins,{domain_hint}."
            ),
        )
    return GoogleConfigTestResponse(
        ok=True,
        email=claims["email"],
        message=f"Verified as {claims['email']}. Enable Google sign-in to save it.",
    )


@router.get("/roles")
def list_roles() -> dict[str, list[dict[str, str]]]:
    return {"roles": _PERMISSIONS}


@router.get("/me", response_model=MeResponse, dependencies=[Depends(require_viewer)])
def read_me(principal: CurrentPrincipal) -> MeResponse:
    """Who the current token resolves to now. For Cryo-managed accounts the role is read
    from the database on every request, so the UI polls this to pick up role changes."""
    order = ["evidence_collector", "viewer", "operator", "admin"]
    role = max((r.value for r in principal.roles), key=order.index)
    with SessionLocal() as session:
        managed = (
            session.query(LocalUser.id).filter(LocalUser.username == principal.subject).first()
            is not None
        )
    return MeResponse(subject=principal.subject, role=role, managed=managed)


@router.get("/users", response_model=list[UserRead], dependencies=[Depends(require_admin)])
def list_users() -> list[UserRead]:
    with SessionLocal() as session:
        rows = session.query(LocalUser).order_by(LocalUser.username).all()
        return [_user_read(row) for row in rows]


@router.post("/users", response_model=UserRead, dependencies=[Depends(require_admin)])
def create_user(payload: UserWrite, principal: CurrentPrincipal) -> UserRead:
    role = _check_role(payload.role)
    username = payload.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if payload.password is None:
        if "@" not in username:
            raise HTTPException(
                status_code=400,
                detail="A user without a password signs in with Google: use their email address",
            )
        domain = _normalize_domain(_auth_settings().google_hosted_domain)
        if domain and not username.endswith(f"@{domain}"):
            raise HTTPException(
                status_code=400,
                detail=f"Google sign-in is restricted to {domain}; {username} could not sign in",
            )
    with SessionLocal() as session:
        if session.query(LocalUser).filter(LocalUser.username == username).one_or_none():
            raise HTTPException(status_code=409, detail="Username already exists")
        user = LocalUser(
            username=username,
            password_hash=hash_password(payload.password) if payload.password else None,
            role=role,
            created_via="admin",
        )
        session.add(user)
        method = "password" if payload.password else "google"
        _audit(
            session,
            "user_created",
            principal.subject,
            f"user={username}; role={role}; sign_in={method}",
        )
        session.commit()
        session.refresh(user)
        return _user_read(user)


@router.patch("/users/{user_id}", response_model=UserRead, dependencies=[Depends(require_admin)])
def update_user(user_id: uuid.UUID, payload: UserPatch, principal: CurrentPrincipal) -> UserRead:
    with SessionLocal() as session:
        user = session.get(LocalUser, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        is_self = user.username == principal.subject
        changes: list[str] = []
        if payload.role is not None:
            role = _check_role(payload.role)
            if role != user.role:
                if is_self and role != "admin":
                    raise HTTPException(
                        status_code=409,
                        detail="You can't remove your own admin role. Ask another admin.",
                    )
                if user.role == "admin" and _last_admin(session, excluding=user.id):
                    raise HTTPException(status_code=409, detail="Cannot remove the last admin")
                changes.append(f"role={user.role}->{role}")
                user.role = role
        if payload.disabled is not None and payload.disabled != user.disabled:
            if payload.disabled and is_self:
                raise HTTPException(status_code=409, detail="You can't disable your own account.")
            if (
                payload.disabled
                and user.role == "admin"
                and _last_admin(session, excluding=user.id)
            ):
                raise HTTPException(status_code=409, detail="Cannot disable the last admin")
            changes.append("disabled" if payload.disabled else "enabled")
            user.disabled = payload.disabled
        if payload.password is not None:
            user.password_hash = hash_password(payload.password)
            changes.append("password_set")
        if changes:
            _audit(
                session,
                "user_updated",
                principal.subject,
                f"user={user.username}; {'; '.join(changes)}",
            )
        session.commit()
        session.refresh(user)
        invalidate_local_user(user.id)
        return _user_read(user)


@router.get("/settings", response_model=AuthSettingsRead, dependencies=[Depends(require_admin)])
def read_auth_settings() -> AuthSettingsRead:
    row = _auth_settings()
    with SessionLocal() as session:
        return _settings_read(session, row)


@router.patch("/settings", response_model=AuthSettingsRead, dependencies=[Depends(require_admin)])
def patch_auth_settings(
    payload: AuthSettingsPatch, principal: CurrentPrincipal
) -> AuthSettingsRead:
    with SessionLocal() as session:
        row = session.get(AuthSettings, 1) or AuthSettings(id=1)
        session.add(row)
        data = payload.model_dump(exclude_unset=True)
        if "google_client_id" in data:
            data["google_client_id"] = (data["google_client_id"] or "").strip() or None
        if "google_hosted_domain" in data:
            data["google_hosted_domain"] = _normalize_domain(data["google_hosted_domain"])
        if data.get("google_enabled") and not (
            data.get("google_client_id") or row.google_client_id
        ):
            raise HTTPException(
                status_code=400, detail="Google Client ID is required to enable sign-in"
            )
        auto_provision = data.get("google_auto_provision", row.google_auto_provision)
        domain = (
            data["google_hosted_domain"]
            if "google_hosted_domain" in data
            else (_normalize_domain(row.google_hosted_domain))
        )
        if auto_provision and not domain:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Restrict Google sign-in to a domain before letting new accounts in as "
                    "viewers, or any Google account could sign in"
                ),
            )
        changed = [key for key, value in data.items() if getattr(row, key) != value]
        for key, value in data.items():
            setattr(row, key, value)
        if changed:
            _audit(
                session,
                "settings_updated",
                principal.subject,
                "; ".join(f"{key}={data[key]}" for key in sorted(changed)),
            )
        session.commit()
        session.refresh(row)
        return _settings_read(session, row)


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
