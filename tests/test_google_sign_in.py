from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from cryo.api.app import create_app
from cryo.config import Settings
from cryo.db.models import AuditLog, AuthSettings, LocalUser
from cryo.security.local_users import invalidate_local_user
from tests.auth_helpers import ADMIN_HEADERS
from tests.conftest import (
    _assign_missing_autoincrement_primary_keys,
    _assign_missing_uuid_primary_keys,
)

CLIENT_ID = "candidate.apps.googleusercontent.com"


@pytest.fixture()
def session_factory(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    event.listen(factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    return factory


@pytest.fixture()
def client(session_factory, monkeypatch) -> TestClient:
    monkeypatch.setattr("cryo.api.auth_routes.SessionLocal", session_factory)
    monkeypatch.setattr("cryo.security.local_users.SessionLocal", session_factory)
    invalidate_local_user()
    with session_factory() as session:
        session.query(AuditLog).delete()
        session.query(LocalUser).delete()
        session.merge(
            AuthSettings(
                id=1,
                password_login_enabled=True,
                google_enabled=True,
                google_client_id=CLIENT_ID,
                google_hosted_domain=None,
                google_auto_provision=False,
            )
        )
        session.commit()
    return TestClient(create_app(Settings(_env_file=None, environment="test")))


def _claims(email: str = "admin@company.com") -> dict[str, object]:
    return {
        "aud": CLIENT_ID,
        "iss": "https://accounts.google.com",
        "email": email,
        "email_verified": "true",
        "sub": "123",
    }


def _google_login(client: TestClient, email: str):
    with patch("cryo.security.google_id_token._fetch_tokeninfo", return_value=_claims(email)):
        return client.post("/api/v1/auth/google", json={"id_token": "token"})


def test_admin_can_add_a_google_only_user_by_email(client) -> None:
    response = client.post(
        "/api/v1/auth/users",
        json={"username": " Admin@Company.com ", "role": "admin"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["username"] == "admin@company.com"
    assert response.json()["has_password"] is False
    settings = client.get("/api/v1/auth/settings", headers=ADMIN_HEADERS).json()
    assert settings["google_admin_emails"] == ["admin@company.com"]


def test_user_without_password_must_be_an_email(client) -> None:
    response = client.post(
        "/api/v1/auth/users", json={"username": "alice", "role": "viewer"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 400
    assert "email" in response.json()["detail"]


def test_google_only_user_outside_the_allowed_domain_is_rejected(client) -> None:
    client.patch(
        "/api/v1/auth/settings",
        json={"google_hosted_domain": " @Company.com "},
        headers=ADMIN_HEADERS,
    )

    response = client.post(
        "/api/v1/auth/users", json={"username": "bob@other.com"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Google sign-in is restricted to company.com; bob@other.com could not sign in"
    )


def test_domain_is_normalized_and_filters_the_admin_list(client) -> None:
    client.post(
        "/api/v1/auth/users",
        json={"username": "root", "password": "long-enough-pw", "role": "admin"},
        headers=ADMIN_HEADERS,
    )
    client.post(
        "/api/v1/auth/users",
        json={"username": "admin@company.com", "role": "admin"},
        headers=ADMIN_HEADERS,
    )

    settings = client.patch(
        "/api/v1/auth/settings",
        json={"google_hosted_domain": " @Company.com ", "google_client_id": f" {CLIENT_ID} "},
        headers=ADMIN_HEADERS,
    ).json()

    assert settings["google_hosted_domain"] == "company.com"
    assert settings["google_client_id"] == CLIENT_ID
    assert settings["google_admin_emails"] == ["admin@company.com"]


def test_google_sign_in_matches_the_added_user_case_insensitively(client) -> None:
    client.post(
        "/api/v1/auth/users",
        json={"username": "admin@company.com", "role": "admin"},
        headers=ADMIN_HEADERS,
    )

    response = _google_login(client, "Admin@Company.com")

    assert response.status_code == 200
    assert response.json()["access_token"]


def test_unknown_google_account_gets_an_actionable_error(client) -> None:
    response = _google_login(client, "stranger@company.com")

    assert response.status_code == 403
    assert "stranger@company.com does not have a Cryo account" in response.json()["detail"]


def test_disabled_account_says_so(client, session_factory) -> None:
    with session_factory() as session:
        session.add(LocalUser(username="gone@company.com", role="viewer", disabled=True))
        session.commit()

    response = _google_login(client, "gone@company.com")

    assert response.status_code == 403
    assert "disabled" in response.json()["detail"]


def test_bootstrap_admin_email_is_created_as_admin_on_first_sign_in(
    client, session_factory, monkeypatch
) -> None:
    settings = Settings(
        _env_file=None, environment="test", google_admin_emails="Admin@Company.com, x@y.com"
    )
    monkeypatch.setattr("cryo.api.auth_routes.get_settings", lambda: settings)

    response = _google_login(client, "admin@company.com")

    assert response.status_code == 200
    with session_factory() as session:
        user = session.query(LocalUser).filter_by(username="admin@company.com").one()
        assert user.role == "admin"


def test_bootstrap_list_does_not_promote_an_existing_user(
    client, session_factory, monkeypatch
) -> None:
    with session_factory() as session:
        session.add(LocalUser(username="admin@company.com", role="viewer"))
        session.commit()
    settings = Settings(_env_file=None, environment="test", google_admin_emails="admin@company.com")
    monkeypatch.setattr("cryo.api.auth_routes.get_settings", lambda: settings)

    assert _google_login(client, "admin@company.com").status_code == 200
    with session_factory() as session:
        assert session.query(LocalUser).filter_by(username="admin@company.com").one().role == (
            "viewer"
        )


def _enable_viewer_auto_provision(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/auth/settings",
        json={"google_hosted_domain": "company.com", "google_auto_provision": True},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["google_auto_provision"] is True


def _bearer(response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_new_domain_account_gets_viewer_access_on_first_sign_in(client, session_factory) -> None:
    _enable_viewer_auto_provision(client)

    response = _google_login(client, "new.person@company.com")

    assert response.status_code == 200
    me = client.get("/api/v1/auth/me", headers=_bearer(response)).json()
    assert me == {"subject": "new.person@company.com", "role": "viewer", "managed": True}
    with session_factory() as session:
        user = session.query(LocalUser).filter_by(username="new.person@company.com").one()
        assert user.created_via == "google"
        assert user.last_login_method == "google"
        assert user.last_login_at is not None


def test_auto_provision_needs_a_domain(client) -> None:
    response = client.patch(
        "/api/v1/auth/settings", json={"google_auto_provision": True}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 400
    assert "domain" in response.json()["detail"]


def test_clearing_the_domain_turns_auto_provision_off_in_effect(client, session_factory) -> None:
    with session_factory() as session:
        row = session.get(AuthSettings, 1)
        row.google_auto_provision = True
        row.google_hosted_domain = None
        session.commit()

    assert _google_login(client, "anyone@gmail.com").status_code == 403
    assert client.get("/api/v1/auth/config").json()["google"]["auto_provision"] is False


def test_role_change_applies_to_an_existing_session(client) -> None:
    _enable_viewer_auto_provision(client)
    session_headers = _bearer(_google_login(client, "new.person@company.com"))
    assert client.get("/api/v1/auth/users", headers=session_headers).status_code == 403
    user = next(
        u
        for u in client.get("/api/v1/auth/users", headers=ADMIN_HEADERS).json()
        if u["username"] == "new.person@company.com"
    )

    client.patch(f"/api/v1/auth/users/{user['id']}", json={"role": "admin"}, headers=ADMIN_HEADERS)

    assert client.get("/api/v1/auth/me", headers=session_headers).json()["role"] == "admin"
    assert client.get("/api/v1/auth/users", headers=session_headers).status_code == 200


def test_disabling_a_user_ends_their_session(client) -> None:
    _enable_viewer_auto_provision(client)
    session_headers = _bearer(_google_login(client, "new.person@company.com"))
    user = client.get("/api/v1/auth/users", headers=ADMIN_HEADERS).json()[0]

    client.patch(f"/api/v1/auth/users/{user['id']}", json={"disabled": True}, headers=ADMIN_HEADERS)

    assert client.get("/api/v1/auth/me", headers=session_headers).status_code == 401


def test_admins_cannot_demote_or_disable_themselves(client, session_factory) -> None:
    with session_factory() as session:
        session.add_all(
            [
                LocalUser(username="test-user", role="admin"),
                LocalUser(username="other@company.com", role="admin"),
            ]
        )
        session.commit()
    me = next(
        u
        for u in client.get("/api/v1/auth/users", headers=ADMIN_HEADERS).json()
        if u["username"] == "test-user"
    )

    demote = client.patch(
        f"/api/v1/auth/users/{me['id']}", json={"role": "viewer"}, headers=ADMIN_HEADERS
    )
    disable = client.patch(
        f"/api/v1/auth/users/{me['id']}", json={"disabled": True}, headers=ADMIN_HEADERS
    )

    assert demote.status_code == 409
    assert disable.status_code == 409


def test_access_changes_are_audited_for_admins_only(client) -> None:
    _enable_viewer_auto_provision(client)
    _google_login(client, "new.person@company.com")
    user = client.get("/api/v1/auth/users", headers=ADMIN_HEADERS).json()[0]
    client.patch(
        f"/api/v1/auth/users/{user['id']}", json={"role": "operator"}, headers=ADMIN_HEADERS
    )

    entries = client.get("/api/v1/auth/audit", headers=ADMIN_HEADERS).json()

    assert [entry["action"] for entry in entries] == [
        "auth.user_updated",
        "auth.user_created",
        "auth.settings_updated",
    ]
    assert entries[0]["actor"] == "test-user"
    assert "role=viewer->operator" in entries[0]["detail"]
    fleet = client.get("/api/v1/audit", headers=ADMIN_HEADERS)
    if fleet.status_code == 200:
        assert not [
            row for row in fleet.json().get("entries", []) if row["action"].startswith("auth.")
        ]
