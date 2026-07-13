"""issue_dev_token()/auth_config() must stay 403/hidden in prod unless allow_dev_token_in_prod
is explicitly set -- see cryo/api/auth_routes.py::_dev_token_enabled.

auth_routes.py calls the process-wide cryo.config.get_settings() singleton directly rather
than a request-scoped dependency, so Settings passed to create_app() doesn't reach these
handlers -- patch cryo.api.auth_routes.get_settings instead (matches the pattern used
elsewhere in this test suite, e.g. test_governor_qpp.py).
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from cryo.api.app import create_app
from cryo.config import Settings


def _client() -> TestClient:
    app = create_app(Settings(_env_file=None, environment="test"))
    return TestClient(app)


def _settings(*, environment: str, allow_dev_token_in_prod: bool = False) -> Settings:
    return Settings(
        _env_file=None,
        environment=environment,
        allow_dev_token_in_prod=allow_dev_token_in_prod,
    )


def test_dev_token_disabled_in_prod_by_default() -> None:
    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="prod")
        response = _client().post("/api/v1/auth/token", json={"role": "admin"})

    assert response.status_code == 503


def test_dev_token_enabled_in_prod_with_explicit_opt_in() -> None:
    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(
            environment="prod", allow_dev_token_in_prod=True
        )
        response = _client().post("/api/v1/auth/token", json={"role": "admin"})

    assert response.status_code == 200
    assert response.json()["access_token"]


def test_dev_token_enabled_outside_prod_regardless_of_flag() -> None:
    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(
            environment="dev", allow_dev_token_in_prod=False
        )
        response = _client().post("/api/v1/auth/token", json={"role": "viewer"})

    assert response.status_code == 200


def test_auth_config_reports_dev_token_enabled_matches_actual_gate() -> None:
    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="prod")
        prod_response = _client().get("/api/v1/auth/config")
    assert prod_response.json()["dev_token_enabled"] is False

    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(
            environment="prod", allow_dev_token_in_prod=True
        )
        opted_in_response = _client().get("/api/v1/auth/config")
    assert opted_in_response.json()["dev_token_enabled"] is True
