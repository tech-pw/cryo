from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cryo.api.app import create_app
from cryo.config import Settings
from tests.auth_helpers import auth_header


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, environment="test")))


@pytest.mark.parametrize("path", ["/api/v1/ops/metrics", "/api/v1/ops/healthz/deep"])
def test_ops_endpoints_require_a_token(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ["/api/v1/ops/metrics", "/api/v1/ops/healthz/deep"])
def test_ops_endpoints_reject_non_admins(client: TestClient, path: str) -> None:
    assert client.get(path, headers=auth_header("operator")).status_code == 403


def test_admin_reads_metrics(client: TestClient) -> None:
    with patch("cryo.api.metrics_routes.refresh_fleet_metrics_cached"):
        response = client.get("/api/v1/ops/metrics", headers=auth_header("admin"))

    assert response.status_code == 200
    assert "# HELP" in response.text


def test_admin_reads_deep_health_even_when_degraded(client: TestClient) -> None:
    degraded = {"status": "degraded", "checks": {"redis": {"status": "error"}}}
    with patch("cryo.api.metrics_routes.health_check", return_value=degraded):
        response = client.get("/api/v1/ops/healthz/deep", headers=auth_header("admin"))

    assert response.status_code == 503
    assert response.json() == degraded
