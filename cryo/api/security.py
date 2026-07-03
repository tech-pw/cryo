from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


ROLE_RANK: Mapping[Role, int] = {
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
    """Extract a local development principal from a bearer token.

    This accepts unsigned JWT-shaped tokens by decoding their payload only, and simple local
    tokens such as ``viewer``, ``operator``, ``admin``, or ``local:admin``.
    """
    token = token.strip()
    if not token:
        return None

    claims = _claims_from_jwt_payload(token)
    if claims is not None:
        return _principal_from_claims(claims)

    role_text = token.removeprefix("local:")
    role = parse_role(role_text)
    if role is None:
        return None
    return Principal(subject="local", roles=frozenset({role}))


def require_role(principal: Principal, required: Role) -> Principal:
    if not principal.can(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires {required.value} role",
        )
    return principal


def role_allows(actual: Role, required: Role) -> bool:
    return ROLE_RANK[actual] >= ROLE_RANK[required]


def parse_role(value: str) -> Role | None:
    normalized = value.strip().lower()
    for role in Role:
        if role.value == normalized:
            return role
    return None


def _principal_from_claims(claims: Mapping[str, object]) -> Principal | None:
    roles = _roles_from_claims(claims)
    if not roles:
        return None

    subject_claim = claims.get("sub")
    subject = subject_claim if isinstance(subject_claim, str) and subject_claim else "jwt"
    return Principal(subject=subject, roles=roles)


def _roles_from_claims(claims: Mapping[str, object]) -> frozenset[Role]:
    raw_values: list[str] = []
    for key in ("role", "roles", "scope", "scopes"):
        raw_values.extend(_strings_from_claim(claims.get(key)))

    roles = {role for value in raw_values if (role := parse_role(value)) is not None}
    return frozenset(roles)


def _strings_from_claim(value: object) -> Iterable[str]:
    if isinstance(value, str):
        return value.replace(",", " ").split()
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, tuple):
        return [item for item in value if isinstance(item, str)]
    return []


def _claims_from_jwt_payload(token: str) -> Mapping[str, object] | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None

    try:
        raw = _base64url_decode(parts[1])
        decoded = json.loads(raw)
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return None

    if not isinstance(decoded, dict):
        return None
    return {str(key): value for key, value in decoded.items()}


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
