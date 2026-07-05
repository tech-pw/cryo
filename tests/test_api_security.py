from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from cryo.api.deps import get_current_principal, require_admin, require_operator
from cryo.api.security import Principal, Role
from tests.auth_helpers import (
    ADMIN_HEADERS,
    OPERATOR_HEADERS,
    VIEWER_HEADERS,
    ensure_test_jwt_secret,
)


def _client() -> TestClient:
    ensure_test_jwt_secret()
    app = FastAPI()

    @app.get("/operator")
    def operator_route(_: Annotated[Principal, Depends(require_operator)]) -> dict[str, str]:
        return {"ok": "operator"}

    @app.get("/admin")
    def admin_route(_: Annotated[Principal, Depends(require_admin)]) -> dict[str, str]:
        return {"ok": "admin"}

    return TestClient(app)


def test_signed_jwt_tokens_enforce_role_order() -> None:
    client = _client()

    assert client.get("/operator", headers=VIEWER_HEADERS).status_code == 403
    assert client.get("/operator", headers=OPERATOR_HEADERS).status_code == 200
    assert client.get("/operator", headers=ADMIN_HEADERS).status_code == 200


def test_literal_tokens_are_rejected() -> None:
    client = _client()
    assert client.get("/operator", headers={"Authorization": "Bearer admin"}).status_code == 401


def test_security_dependency_is_easy_to_override() -> None:
    app = FastAPI()

    @app.get("/admin")
    def admin_route(_: Annotated[Principal, Depends(require_admin)]) -> dict[str, str]:
        return {"ok": "admin"}

    app.dependency_overrides[get_current_principal] = lambda: Principal(
        subject="test", roles=frozenset({Role.ADMIN})
    )

    response = TestClient(app).get("/admin")

    assert response.status_code == 200
    assert response.json() == {"ok": "admin"}
