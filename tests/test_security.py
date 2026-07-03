from __future__ import annotations

import base64
import json

import pytest
from fastapi import HTTPException

from cryo.api.security import Role, principal_from_token, require_role


def test_local_bearer_tokens_create_principals() -> None:
    principal = principal_from_token("local:operator")

    assert principal is not None
    assert principal.subject == "local"
    assert principal.can(Role.VIEWER)
    assert principal.can(Role.OPERATOR)
    assert not principal.can(Role.ADMIN)


def test_unsigned_jwt_shaped_token_roles_are_decoded_for_local_dev() -> None:
    claims = {"sub": "alice", "roles": ["viewer", "admin"]}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")

    principal = principal_from_token(f"ignored.{payload}.ignored")

    assert principal is not None
    assert principal.subject == "alice"
    assert principal.can(Role.ADMIN)


def test_require_role_rejects_insufficient_roles() -> None:
    principal = principal_from_token("viewer")
    assert principal is not None

    with pytest.raises(HTTPException) as exc:
        require_role(principal, Role.OPERATOR)

    assert exc.value.status_code == 403
