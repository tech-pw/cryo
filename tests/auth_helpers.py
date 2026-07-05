"""Test helpers for signed JWT auth headers."""

from __future__ import annotations

import os
import uuid

from cryo.security.jwt_tokens import issue_access_token

_TEST_SECRET = "test-jwt-secret-for-pytest-only"


def ensure_test_jwt_secret() -> None:
    os.environ.setdefault("CRYO_JWT_SECRET", _TEST_SECRET)
    os.environ.setdefault("CRYO_SECRET_KEY", _TEST_SECRET)


def auth_header(role: str, *, tenant_id: uuid.UUID | None = None) -> dict[str, str]:
    ensure_test_jwt_secret()
    token = issue_access_token(subject="test-user", roles=[role], tenant_id=tenant_id)
    return {"Authorization": f"Bearer {token}"}


VIEWER_HEADERS = auth_header("viewer")
OPERATOR_HEADERS = auth_header("operator")
ADMIN_HEADERS = auth_header("admin")
