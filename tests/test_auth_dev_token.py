"""issue_dev_token()/auth_config() must stay 403/hidden in prod unless allow_dev_token_in_prod
is explicitly set -- see cryo/api/auth_routes.py::_dev_token_enabled.

auth_routes.py calls the process-wide cryo.config.get_settings() singleton directly rather
than a request-scoped dependency, so Settings passed to create_app() doesn't reach these
handlers -- patch cryo.api.auth_routes.get_settings instead (matches the pattern used
elsewhere in this test suite, e.g. test_governor_qpp.py).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from cryo.api.app import create_app
from cryo.config import DEFAULT_SECRET_KEY, Settings


def _client() -> TestClient:
    app = create_app(Settings(_env_file=None, environment="test"))
    return TestClient(app)


_REAL_KEY = "k" * 48


def _settings(
    *,
    environment: str,
    allow_dev_token_in_prod: bool = False,
    oidc_issuer_url: str | None = None,
) -> Settings:
    return Settings(
        _env_file=None,
        environment=environment,
        allow_dev_token_in_prod=allow_dev_token_in_prod,
        oidc_issuer_url=oidc_issuer_url,
        secret_key=_REAL_KEY,
        jwt_secret=_REAL_KEY,
    )


def test_dev_token_disabled_in_prod_by_default() -> None:
    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="prod")
        response = _client().post("/api/v1/auth/token", json={"role": "admin"})

    assert response.status_code == 503


def test_dev_token_enabled_in_prod_with_explicit_opt_in() -> None:
    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="prod", allow_dev_token_in_prod=True)
        response = _client().post("/api/v1/auth/token", json={"role": "admin"})

    assert response.status_code == 200
    assert response.json()["access_token"]


def test_dev_token_enabled_outside_prod_regardless_of_flag() -> None:
    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="dev", allow_dev_token_in_prod=False)
        response = _client().post("/api/v1/auth/token", json={"role": "viewer"})

    assert response.status_code == 200


def test_auth_config_reports_dev_token_enabled_matches_actual_gate() -> None:
    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="prod")
        prod_response = _client().get("/api/v1/auth/config")
    assert prod_response.json()["dev_token_enabled"] is False

    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="prod", allow_dev_token_in_prod=True)
        opted_in_response = _client().get("/api/v1/auth/config")
    assert opted_in_response.json()["dev_token_enabled"] is True


def test_auth_config_dev_token_enabled_in_prod_only_when_prod_and_opted_in() -> None:
    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="prod")
        prod_response = _client().get("/api/v1/auth/config")
    assert prod_response.status_code == 200
    assert prod_response.json()["dev_token_enabled"] is False
    assert prod_response.json()["dev_token_enabled_in_prod"] is False

    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="prod", allow_dev_token_in_prod=True)
        opted_in_response = _client().get("/api/v1/auth/config")
    assert opted_in_response.status_code == 200
    assert opted_in_response.json()["dev_token_enabled"] is True
    assert opted_in_response.json()["dev_token_enabled_in_prod"] is True

    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="dev", allow_dev_token_in_prod=True)
        dev_response = _client().get("/api/v1/auth/config")
    assert dev_response.status_code == 200
    assert dev_response.json()["dev_token_enabled"] is True
    assert dev_response.json()["dev_token_enabled_in_prod"] is False

    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="staging", allow_dev_token_in_prod=True)
        staging_response = _client().get("/api/v1/auth/config")
    assert staging_response.status_code == 200
    assert staging_response.json()["dev_token_enabled"] is True
    assert staging_response.json()["dev_token_enabled_in_prod"] is False


def test_dev_token_disabled_in_staging() -> None:
    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(environment="staging")
        response = _client().post("/api/v1/auth/token", json={"role": "admin"})

    assert response.status_code == 503


def test_dev_token_disabled_once_oidc_is_configured() -> None:
    with patch("cryo.api.auth_routes.get_settings") as mock_settings:
        mock_settings.return_value = _settings(
            environment="dev", oidc_issuer_url="https://idp.example.com"
        )
        response = _client().post("/api/v1/auth/token", json={"role": "admin"})

    assert response.status_code == 503


@pytest.mark.parametrize("environment", ["staging", "prod"])
@pytest.mark.parametrize("field", ["secret_key", "jwt_secret"])
@pytest.mark.parametrize("value", [DEFAULT_SECRET_KEY, "short-key"])
def test_settings_refuse_default_or_short_keys_outside_dev(
    environment: str, field: str, value: str
) -> None:
    kwargs = {"secret_key": _REAL_KEY, "jwt_secret": _REAL_KEY, field: value}
    with pytest.raises(ValidationError, match=f"CRYO_{field.upper()}"):
        Settings(_env_file=None, environment=environment, **kwargs)


@pytest.mark.parametrize("environment", ["dev", "test"])
def test_settings_keep_the_default_key_in_dev_and_test(environment: str) -> None:
    settings = Settings(
        _env_file=None,
        environment=environment,
        secret_key=DEFAULT_SECRET_KEY,
        jwt_secret=None,
    )

    assert settings.secret_key == DEFAULT_SECRET_KEY


def test_settings_accept_a_real_key_in_prod() -> None:
    settings = Settings(
        _env_file=None, environment="prod", secret_key=_REAL_KEY, jwt_secret=_REAL_KEY
    )

    assert settings.secret_key == _REAL_KEY
