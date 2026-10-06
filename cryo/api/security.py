from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from cryo.security.jwt_tokens import (
    roles_from_claims,
    tenant_id_from_claims,
    verify_access_token,
)


class Role(StrEnum):
    EVIDENCE_COLLECTOR = "evidence_collector"
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


ROLE_RANK: dict[Role, int] = {
    Role.EVIDENCE_COLLECTOR: -1,
    Role.VIEWER: 0,
    Role.OPERATOR: 1,
    Role.ADMIN: 2,
}

AUTHENTICATE_HEADER = {"WWW-Authenticate": "Bearer"}
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    roles: frozenset[Role]
    tenant_id: uuid.UUID | None = None

    def can(self, required: Role) -> bool:
        return any(role_allows(role, required) for role in self.roles)


def principal_from_credentials(credentials: HTTPAuthorizationCredentials | None) -> Principal:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers=AUTHENTICATE_HEADER,
        )
    if credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unsupported authentication scheme",
            headers=AUTHENTICATE_HEADER,
        )

    principal = principal_from_token(credentials.credentials)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers=AUTHENTICATE_HEADER,
        )
    return principal


def principal_from_token(token: str) -> Principal | None:
    """Verify a signed JWT and extract roles + tenant_id."""
    token = token.strip()
    if not token:
        return None

    claims = verify_access_token(token)
    if claims is None:
        return None

    role_names = roles_from_claims(claims)
    if not role_names:
        return None

    roles = frozenset(Role(name) for name in role_names)
    subject_claim = claims.get("sub") or claims.get("preferred_username") or claims.get("email")
    subject = subject_claim if isinstance(subject_claim, str) and subject_claim else "jwt"
    tenant_id = tenant_id_from_claims(claims)
    if tenant_id is None:
        from cryo.security.oidc import tenant_id_from_oidc_claims

        tenant_id = tenant_id_from_oidc_claims(claims)
    return Principal(subject=subject, roles=roles, tenant_id=tenant_id)


def require_role(principal: Principal, required: Role) -> Principal:
    if not principal.can(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires {required.value} role",
        )
    return principal


def role_allows(actual: Role, required: Role) -> bool:
    if required is Role.EVIDENCE_COLLECTOR:
        return actual is Role.EVIDENCE_COLLECTOR
    return ROLE_RANK[actual] >= ROLE_RANK[required]


def parse_role(value: str) -> Role | None:
    normalized = value.strip().lower()
    for role in Role:
        if role.value == normalized:
            return role
    return None
