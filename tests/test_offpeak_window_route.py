"""The table Config tab reads the governor's off-peak window read-only;
this endpoint must mirror is_offpeak()'s own equal-bounds-disables-the-gate rule."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi.testclient import TestClient

from cryo.api.app import create_app
from cryo.api.deps import get_tenant_session
from cryo.config import Settings
from tests.auth_helpers import VIEWER_HEADERS, ensure_test_jwt_secret


class FakeSession:
    def expire_all(self) -> None:
        return None

    def expunge_all(self) -> None:
        return None


def _client() -> TestClient:
    ensure_test_jwt_secret()
    app = create_app(Settings(_env_file=None, environment="test"))

    def fake_tenant_session() -> Iterator[FakeSession]:
        yield FakeSession()

    app.dependency_overrides[get_tenant_session] = fake_tenant_session
    return TestClient(app)


def test_offpeak_window_reports_enabled_when_bounds_differ(monkeypatch) -> None:
    settings = Settings(_env_file=None, environment="test", offpeak_start_ist=22, offpeak_end_ist=6)
    monkeypatch.setattr("cryo.api.routes.get_settings", lambda: settings)
    response = _client().get("/api/v1/governor/offpeak-window", headers=VIEWER_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body == {"start_hour_ist": 22, "end_hour_ist": 6, "enabled": True}


def test_offpeak_window_reports_disabled_when_bounds_are_equal(monkeypatch) -> None:
    settings = Settings(_env_file=None, environment="test", offpeak_start_ist=0, offpeak_end_ist=0)
    monkeypatch.setattr("cryo.api.routes.get_settings", lambda: settings)
    response = _client().get("/api/v1/governor/offpeak-window", headers=VIEWER_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"start_hour_ist": 0, "end_hour_ist": 0, "enabled": False}
