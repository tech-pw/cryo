"""GET /api/v1/version and build_info() (#263)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cryo import __version__
from cryo.api.app import create_app
from cryo.build_info import build_info
from cryo.config import Settings


def _client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, environment="test")))


def test_build_info_falls_back_to_package_version(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CRYO_VERSION", "CRYO_GIT_SHA", "CRYO_BUILD_DATE"):
        monkeypatch.delenv(name, raising=False)
    info = build_info()
    assert info.version == __version__
    assert info.git_sha is None
    assert info.built_at is None


def test_build_info_reads_image_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYO_VERSION", "v0.1.6")
    monkeypatch.setenv("CRYO_GIT_SHA", "abc123")
    monkeypatch.setenv("CRYO_BUILD_DATE", "2026-10-26T10:00:00Z")
    assert build_info().as_dict() == {
        "version": "v0.1.6",
        "git_sha": "abc123",
        "built_at": "2026-10-26T10:00:00Z",
    }


def test_blank_env_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYO_VERSION", "  ")
    monkeypatch.setenv("CRYO_GIT_SHA", "")
    info = build_info()
    assert info.version == __version__
    assert info.git_sha is None


def test_version_endpoint_is_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYO_VERSION", "v0.1.6")
    monkeypatch.setenv("CRYO_GIT_SHA", "abc123")
    monkeypatch.delenv("CRYO_BUILD_DATE", raising=False)
    with patch("cryo.api.app.current_schema_revision", return_value="20261020_0001"):
        response = _client().get("/api/v1/version")
    assert response.status_code == 200
    assert response.json() == {
        "version": "v0.1.6",
        "git_sha": "abc123",
        "built_at": None,
        "schema_revision": "20261020_0001",
    }


def test_version_endpoint_survives_an_unreachable_database() -> None:
    with patch("cryo.db.session.SessionLocal", side_effect=RuntimeError("down")):
        response = _client().get("/api/v1/version")
    assert response.status_code == 200
    assert response.json()["schema_revision"] is None


def test_healthz_reports_the_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRYO_VERSION", "v0.1.6")
    assert _client().get("/healthz").json()["version"] == "v0.1.6"
