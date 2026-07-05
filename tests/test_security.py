"""Security tests for signed JWT authentication."""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import HTTPException

from cryo.api.security import Role, principal_from_token, require_role
from cryo.security.jwt_tokens import issue_access_token
from tests.auth_helpers import ensure_test_jwt_secret


def test_signed_jwt_creates_principal_with_roles() -> None:
    ensure_test_jwt_secret()
    token = issue_access_token(subject="alice", roles=["operator"])

    principal = principal_from_token(token)

    assert principal is not None
    assert principal.subject == "alice"
    assert principal.can(Role.VIEWER)
    assert principal.can(Role.OPERATOR)
    assert not principal.can(Role.ADMIN)


def test_unsigned_jwt_shaped_token_is_rejected() -> None:
    ensure_test_jwt_secret()
    claims = {"sub": "alice", "roles": ["viewer", "admin"], "exp": 9999999999}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    token = f"ignored.{payload}.ignored"

    assert principal_from_token(token) is None


def test_literal_role_strings_are_rejected() -> None:
    ensure_test_jwt_secret()
    assert principal_from_token("admin") is None
    assert principal_from_token("local:operator") is None


def test_require_role_rejects_insufficient_roles() -> None:
    ensure_test_jwt_secret()
    token = issue_access_token(subject="bob", roles=["viewer"])
    principal = principal_from_token(token)
    assert principal is not None

    with pytest.raises(HTTPException) as exc:
        require_role(principal, Role.OPERATOR)

    assert exc.value.status_code == 403
