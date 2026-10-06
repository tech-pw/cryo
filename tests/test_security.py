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


def test_evidence_collector_is_not_an_operator_and_admin_cannot_impersonate_it() -> None:
    ensure_test_jwt_secret()
    collector = principal_from_token(
        issue_access_token(subject="spiffe://cryo/evidence-collector", roles=["evidence_collector"])
    )
    admin = principal_from_token(issue_access_token(subject="admin", roles=["admin"]))

    assert collector is not None
    assert admin is not None
    assert collector.can(Role.EVIDENCE_COLLECTOR)
    assert not collector.can(Role.VIEWER)
    assert not collector.can(Role.OPERATOR)
    assert not admin.can(Role.EVIDENCE_COLLECTOR)
