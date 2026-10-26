"""Web Deployment must point nginx at the API Service FQDN and the pod nameserver."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "deploy/helm/cryo"


def render(*args: str) -> list[dict]:
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering")
    result = subprocess.run(
        ["helm", "template", "cryo", str(CHART), "--set", "global.env.CRYO_ENVIRONMENT=dev", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def _web_container(docs: list[dict]) -> dict:
    web = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "cryo-web")
    return web["spec"]["template"]["spec"]["containers"][0]


def test_web_is_off_by_default() -> None:
    docs = render()
    assert not any(d["kind"] == "Deployment" and d["metadata"]["name"] == "cryo-web" for d in docs)


def test_web_injects_api_fqdn_and_local_resolvers() -> None:
    docs = render("--set", "web.enabled=true", "--namespace", "cryo")
    env = {item["name"]: item["value"] for item in _web_container(docs)["env"]}
    assert env["NGINX_ENTRYPOINT_LOCAL_RESOLVERS"] == "1"
    assert env["NGINX_ENVSUBST_FILTER"] == "NGINX_LOCAL_RESOLVERS|CRYO_API_UPSTREAM|CRYO_WEB_HSTS"
    assert env["CRYO_API_UPSTREAM"] == ("http://cryo-api.cryo.svc.cluster.local:8000")


def test_web_api_upstream_follows_release_namespace_and_service_port() -> None:
    docs = render(
        "--set",
        "web.enabled=true",
        "--set",
        "service.port=9000",
        "--namespace",
        "platform",
    )
    env = {item["name"]: item["value"] for item in _web_container(docs)["env"]}
    assert env["CRYO_API_UPSTREAM"] == ("http://cryo-api.platform.svc.cluster.local:9000")
