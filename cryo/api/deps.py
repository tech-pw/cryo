from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials

from cryo.api.security import (
    Principal,
    Role,
    bearer_scheme,
    principal_from_credentials,
    require_role,
)

CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def get_current_principal(credentials: CredentialsDep) -> Principal:
    return principal_from_credentials(credentials)


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def require_viewer(principal: CurrentPrincipal) -> Principal:
    return require_role(principal, Role.VIEWER)


def require_operator(principal: CurrentPrincipal) -> Principal:
    return require_role(principal, Role.OPERATOR)


def require_admin(principal: CurrentPrincipal) -> Principal:
    return require_role(principal, Role.ADMIN)


def require_editor(principal: CurrentPrincipal) -> Principal:
    """Alias for operator role (create/trigger operations)."""
    return require_role(principal, Role.OPERATOR)
