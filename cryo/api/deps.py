from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from cryo.api.security import (
    Principal,
    Role,
    bearer_scheme,
    principal_from_credentials,
    require_role,
)
from cryo.db.session import get_session
from cryo.db.tenant_scope import bind_tenant

CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def get_current_principal(credentials: CredentialsDep) -> Principal:
    return principal_from_credentials(credentials)


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def get_tenant_session(
    principal: CurrentPrincipal,
) -> Iterator[Session]:
    for session in get_session():
        bind_tenant(session, principal.tenant_id)
        yield session


SessionDep = Annotated[Session, Depends(get_tenant_session)]


def require_viewer(principal: CurrentPrincipal) -> Principal:
    return require_role(principal, Role.VIEWER)


def require_operator(principal: CurrentPrincipal) -> Principal:
    return require_role(principal, Role.OPERATOR)


def require_admin(principal: CurrentPrincipal) -> Principal:
    return require_role(principal, Role.ADMIN)


def require_editor(principal: CurrentPrincipal) -> Principal:
    """Alias for operator role (create/trigger operations)."""
    return require_role(principal, Role.OPERATOR)
