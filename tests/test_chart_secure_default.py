from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "deploy" / "helm" / "cryo"
_DIGEST = "sha256:" + "0" * 64


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering")
    return subprocess.run(
        ["helm", "template", "cryo", str(CHART), *args], capture_output=True, text=True
    )


def test_chart_defaults_to_prod() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text())

    assert values["global"]["env"]["CRYO_ENVIRONMENT"] == "prod"


def test_bare_render_fails_until_images_are_pinned() -> None:
    result = _helm()

    assert result.returncode != 0
    assert "production requires image.digest" in result.stderr


def test_prod_render_with_pinned_images_sets_prod_on_every_workload() -> None:
    result = _helm(
        "--set",
        f"image.digest={_DIGEST}",
        "--set",
        f"migration.image.digest={_DIGEST}",
        "--set",
        f"redis.image.digest={_DIGEST}",
    )

    assert result.returncode == 0, result.stderr
    envs = [
        env
        for doc in yaml.safe_load_all(result.stdout)
        if doc and doc.get("kind") in {"Deployment", "CronJob", "Job"}
        for env in _container_env(doc)
    ]
    assert envs
    assert all(env.get("CRYO_ENVIRONMENT") == "prod" for env in envs if env)


def _container_env(doc: dict) -> list[dict[str, str]]:
    spec = doc["spec"]
    pod = (
        spec["jobTemplate"]["spec"]["template"]["spec"]
        if "jobTemplate" in spec
        else spec["template"]["spec"]
    )
    return [
        {e["name"]: e.get("value") for e in c.get("env", []) if "name" in e}
        for c in pod.get("containers", [])
    ]
