"""Tests for deploy/pin_release_images.py (2026-11-12 cross-repository digest incident)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).parent.parent / "deploy" / "pin_release_images.py"
_spec = importlib.util.spec_from_file_location("pin_release_images", _PATH)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["pin_release_images"] = mod
_spec.loader.exec_module(mod)

APP, SPARK, WEB, REDIS, BOGUS = (f"sha256:{c * 64}" for c in "abcde")

VALUES = f"""\
image:
  repository: ghcr.io/tech-pw/cryo
  # history comment that must survive
  digest: "{BOGUS}"
migration:
  image:
    repository: ghcr.io/tech-pw/cryo
    digest: "{BOGUS}"
spark:
  k8s:
    enabled: true
    image:
      repository: ghcr.io/tech-pw/cryo-spark
      digest: "{APP}"
      pullPolicy: IfNotPresent
web:
  image:
    repository: ghcr.io/tech-pw/cryo-web
    digest: "{SPARK}"
redis:
  image:
    repository: redis
    tag: "7-alpine"
    digest: "{REDIS}"
"""

DIGESTS = {
    "ghcr.io/tech-pw/cryo": APP,
    "ghcr.io/tech-pw/cryo-spark": SPARK,
    "ghcr.io/tech-pw/cryo-web": WEB,
}


def test_find_pins_pairs_each_digest_with_its_own_repository() -> None:
    pins = {(p.path_hint, p.repository, p.digest) for p in mod.find_pins(VALUES)}

    assert pins == {
        ("image", "ghcr.io/tech-pw/cryo", BOGUS),
        ("migration.image", "ghcr.io/tech-pw/cryo", BOGUS),
        ("spark.k8s.image", "ghcr.io/tech-pw/cryo-spark", APP),
        ("web.image", "ghcr.io/tech-pw/cryo-web", SPARK),
        ("redis.image", "redis", REDIS),
    }


def test_pin_writes_each_digest_only_under_its_repository_and_keeps_redis() -> None:
    updated, changed = mod.pin(VALUES, DIGESTS)

    got = {(p.repository, p.digest) for p in mod.find_pins(updated)}
    assert got == {
        ("ghcr.io/tech-pw/cryo", APP),
        ("ghcr.io/tech-pw/cryo-spark", SPARK),
        ("ghcr.io/tech-pw/cryo-web", WEB),
        ("redis", REDIS),
    }
    assert len(changed) == 4
    assert "# history comment that must survive" in updated
    assert "pullPolicy: IfNotPresent" in updated


def test_check_flags_digests_that_do_not_exist_in_their_repository() -> None:
    real = {
        ("ghcr.io/tech-pw/cryo", APP),
        ("ghcr.io/tech-pw/cryo-spark", SPARK),
        ("ghcr.io/tech-pw/cryo-web", WEB),
        ("redis", REDIS),
    }

    problems = mod.check(VALUES, lambda repo, d: (repo, d) in real)

    assert len(problems) == 4
    assert any(p.startswith("spark.k8s.image:") for p in problems)
    assert any(p.startswith("web.image:") for p in problems)
    assert not any(p.startswith("redis.image:") for p in problems)


def test_check_passes_after_pin() -> None:
    real = {(r, d) for r, d in DIGESTS.items()} | {("redis", REDIS)}
    updated, _ = mod.pin(VALUES, DIGESTS)

    assert mod.check(updated, lambda repo, d: (repo, d) in real) == []


def test_check_rejects_a_malformed_digest() -> None:
    bad = VALUES.replace(f'digest: "{REDIS}"', 'digest: "sha256:abc"')

    problems = mod.check(bad, lambda repo, d: True)

    assert problems == ["redis.image: 'sha256:abc' is not sha256:<64 hex>"]


def test_registry_host_and_name_resolution() -> None:
    assert mod._split("redis") == ("registry-1.docker.io", "library/redis")
    assert mod._split("docker.io/bitnami/redis") == ("registry-1.docker.io", "bitnami/redis")
    assert mod._split("ghcr.io/tech-pw/cryo") == ("ghcr.io", "tech-pw/cryo")
