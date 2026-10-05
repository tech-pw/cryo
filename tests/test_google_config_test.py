"""Tests for POST /api/v1/auth/settings/google/test -- verifies a candidate Google Client ID
against a real ID token before anything is saved to AuthSettings."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from cryo.api.app import create_app
from cryo.config import Settings
from tests.auth_helpers import ADMIN_HEADERS, VIEWER_HEADERS


def _client() -> TestClient:
    app = create_app(Settings(_env_file=None, environment="test"))
    return TestClient(app)


def _fake_claims(**overrides: object) -> dict[str, object]:
    base = {
        "aud": "candidate.apps.googleusercontent.com",
        "iss": "https://accounts.google.com",
        "email": "admin@company.com",
        "email_verified": "true",
        "sub": "123",
    }
    base.update(overrides)
    return base


def test_requires_admin() -> None:
    response = _client().post(
        "/api/v1/auth/settings/google/test",
        json={"id_token": "x", "client_id": "candidate.apps.googleusercontent.com"},
        headers=VIEWER_HEADERS,
    )
    assert response.status_code == 403


def test_ok_when_token_verifies_against_candidate_client_id() -> None:
    with patch("cryo.security.google_id_token._fetch_tokeninfo") as mock_fetch:
        mock_fetch.return_value = _fake_claims()
        response = _client().post(
            "/api/v1/auth/settings/google/test",
            json={"id_token": "real-token", "client_id": "candidate.apps.googleusercontent.com"},
            headers=ADMIN_HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["email"] == "admin@company.com"


def test_not_ok_when_audience_does_not_match_candidate_client_id() -> None:
    """The whole point of this endpoint: the token must be checked against the CANDIDATE
    client_id from the request, never a saved one -- this is what makes "test before save"
    meaningful rather than just re-checking what's already persisted."""
    with patch("cryo.security.google_id_token._fetch_tokeninfo") as mock_fetch:
        mock_fetch.return_value = _fake_claims(aud="different.apps.googleusercontent.com")
        response = _client().post(
            "/api/v1/auth/settings/google/test",
            json={"id_token": "real-token", "client_id": "candidate.apps.googleusercontent.com"},
            headers=ADMIN_HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "Client ID" in body["message"]


def test_not_ok_when_hosted_domain_does_not_match() -> None:
    with patch("cryo.security.google_id_token._fetch_tokeninfo") as mock_fetch:
        mock_fetch.return_value = _fake_claims(email="admin@other.com")
        response = _client().post(
            "/api/v1/auth/settings/google/test",
            json={
                "id_token": "real-token",
                "client_id": "candidate.apps.googleusercontent.com",
                "hosted_domain": "company.com",
            },
            headers=ADMIN_HEADERS,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "company.com" in body["message"]


def test_does_not_touch_saved_auth_settings() -> None:
    """A test call must never write to AuthSettings -- only an explicit PATCH /settings save
    does that. Verified indirectly: GET /settings before and after the test call must be
    unchanged (both calls need an admin session, so this also exercises the real route, not
    just the googleidtoken unit)."""
    client = _client()
    before = client.get("/api/v1/auth/settings", headers=ADMIN_HEADERS).json()

    with patch("cryo.security.google_id_token._fetch_tokeninfo") as mock_fetch:
        mock_fetch.return_value = _fake_claims()
        client.post(
            "/api/v1/auth/settings/google/test",
            json={"id_token": "real-token", "client_id": "candidate.apps.googleusercontent.com"},
            headers=ADMIN_HEADERS,
        )

    after = client.get("/api/v1/auth/settings", headers=ADMIN_HEADERS).json()
    assert before == after
