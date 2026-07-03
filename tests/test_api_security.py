from __future__ import annotations

import base64
import json
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from cryo.api.deps import get_current_principal, require_admin, require_operator
from cryo.api.security import Principal, Role, principal_from_token


def _client() -> TestClient:
    app = FastAPI()

    @app.get("/operator")
    def operator_route(_: Annotated[Principal, Depends(require_operator)]) -> dict[str, str]:
        return {"ok": "operator"}

    @app.get("/admin")
    def admin_route(_: Annotated[Principal, Depends(require_admin)]) -> dict[str, str]:
        return {"ok": "admin"}

    return TestClient(app)


def test_local_tokens_enforce_role_order() -> None:
    client = _client()

    assert client.get("/operator", headers={"Authorization": "Bearer viewer"}).status_code == 403
    operator_response = client.get("/operator", headers={"Authorization": "Bearer local:operator"})

    assert operator_response.status_code == 200
    assert client.get("/operator", headers={"Authorization": "Bearer admin"}).status_code == 200


def test_jwt_shaped_token_extracts_subject_and_roles() -> None:
    token = f"header.{_b64_json({'sub': 'alice', 'roles': ['viewer', 'operator']})}.signature"

    principal = principal_from_token(token)

    assert principal == Principal(subject="alice", roles=frozenset({Role.VIEWER, Role.OPERATOR}))


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


def _b64_json(value: object) -> str:
    raw = json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
