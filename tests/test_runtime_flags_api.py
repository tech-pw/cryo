from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import cryo.config
from cryo.api.app import create_app
from cryo.config import Settings
from tests.auth_helpers import VIEWER_HEADERS

NATIVE_ENGINE_SQL_FALLBACK = "the configured Spark SQL fallback"


def _client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, environment="test")))


def _patch_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    native_engine_enabled: bool,
    maintenance_writes_enabled: bool,
) -> None:
    monkeypatch.setattr(
        cryo.config,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            native_engine_enabled=native_engine_enabled,
            maintenance_writes_enabled=maintenance_writes_enabled,
        ),
    )


def test_runtime_flags_follow_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()

    _patch_settings(
        monkeypatch,
        native_engine_enabled=False,
        maintenance_writes_enabled=False,
    )
    disabled = client.get("/api/v1/runtime", headers=VIEWER_HEADERS)
    assert disabled.status_code == 200
    assert disabled.json() == {
        "native_engine_enabled": False,
        "maintenance_writes_enabled": False,
        "native_engine_fallback": NATIVE_ENGINE_SQL_FALLBACK,
    }

    _patch_settings(
        monkeypatch,
        native_engine_enabled=True,
        maintenance_writes_enabled=True,
    )
    enabled = client.get("/api/v1/runtime", headers=VIEWER_HEADERS)
    assert enabled.status_code == 200
    assert enabled.json() == {
        "native_engine_enabled": True,
        "maintenance_writes_enabled": True,
        "native_engine_fallback": NATIVE_ENGINE_SQL_FALLBACK,
    }

    _patch_settings(
        monkeypatch,
        native_engine_enabled=False,
        maintenance_writes_enabled=True,
    )
    mixed = client.get("/api/v1/runtime", headers=VIEWER_HEADERS)
    assert mixed.status_code == 200
    body = mixed.json()
    assert body["native_engine_enabled"] is False
    assert body["maintenance_writes_enabled"] is True


def test_runtime_requires_viewer() -> None:
    response = _client().get("/api/v1/runtime")
    assert response.status_code == 401
