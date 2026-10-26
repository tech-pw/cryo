"""The migration hook must run the API image unless a separate one is configured (issue #140)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1] / "deploy/helm/cryo"
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering")
    return subprocess.run(
        ["helm", "template", "cryo", str(CHART), *args],
        capture_output=True,
        text=True,
    )


def _migrate_container(*args: str) -> dict:
    result = _helm(*args)
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    job = next(
        d
        for d in docs
        if d["kind"] == "Job"
        and d["metadata"]["labels"].get("app.kubernetes.io/component") == "migration"
    )
    return job["spec"]["template"]["spec"]["containers"][0]


def _api_image(*args: str) -> str:
    result = _helm(*args)
    assert result.returncode == 0, result.stderr
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    api = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "cryo-api")
    return api["spec"]["template"]["spec"]["containers"][0]["image"]


def test_migration_follows_api_repository_and_digest() -> None:
    args = ("--set", "image.repository=ghcr.io/acme/cryo", "--set", f"image.digest={DIGEST}")
    container = _migrate_container(*args)
    assert container["image"] == f"ghcr.io/acme/cryo@{DIGEST}"
    assert container["image"] == _api_image(*args)


def test_migration_follows_api_tag_without_digest() -> None:
    container = _migrate_container(
        "--set", "image.repository=ghcr.io/acme/cryo", "--set", "image.tag=1.4.0"
    )
    assert container["image"] == "ghcr.io/acme/cryo:1.4.0"


def test_migration_follows_api_pull_policy() -> None:
    container = _migrate_container("--set", "image.pullPolicy=IfNotPresent")
    assert container["imagePullPolicy"] == "IfNotPresent"


def test_separate_migration_image_is_opt_in() -> None:
    container = _migrate_container(
        "--set",
        f"image.digest={DIGEST}",
        "--set",
        "migration.image.repository=registry.example/cryo-migrate",
        "--set",
        f"migration.image.digest={OTHER_DIGEST}",
        "--set",
        "migration.image.pullPolicy=IfNotPresent",
    )
    assert container["image"] == f"registry.example/cryo-migrate@{OTHER_DIGEST}"
    assert container["imagePullPolicy"] == "IfNotPresent"


def test_separate_migration_repository_without_tag_uses_api_tag() -> None:
    container = _migrate_container(
        "--set", "image.tag=2.0.0", "--set", "migration.image.repository=registry.example/mig"
    )
    assert container["image"] == "registry.example/mig:2.0.0"


def test_migration_digest_alone_pins_the_api_repository() -> None:
    container = _migrate_container(
        "--set",
        "image.repository=ghcr.io/acme/cryo",
        "--set",
        f"migration.image.digest={OTHER_DIGEST}",
    )
    assert container["image"] == f"ghcr.io/acme/cryo@{OTHER_DIGEST}"


def _prod_args(*extra: str) -> tuple[str, ...]:
    return (
        "--set",
        "global.env.CRYO_ENVIRONMENT=prod",
        "--set",
        f"image.digest={DIGEST}",
        "--set",
        f"redis.image.digest={DIGEST}",
        *extra,
    )


def test_production_needs_only_the_api_digest_by_default() -> None:
    container = _migrate_container(*_prod_args())
    assert container["image"].endswith(f"@{DIGEST}")


def test_production_separate_migration_repository_requires_its_digest() -> None:
    result = _helm(*_prod_args("--set", "migration.image.repository=registry.example/mig"))
    assert result.returncode != 0
    assert "requires migration.image.digest" in result.stderr
