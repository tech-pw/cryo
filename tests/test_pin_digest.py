"""Tests for deploy/pin_digest.py.

Built after a real incident, 2026-08-10: a manual edit updated migration.image.digest and
native.image.digest to a new build but silently missed the top-level image.digest -- three
find-and-replace edits, one skipped, no error. api/worker/governor/scheduler would have run
one commit behind the native engine binary shipped in the same image. These tests exist so
the script that replaced that manual process is itself verified, not just trusted.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).parent.parent / "deploy" / "pin_digest.py"
_spec = importlib.util.spec_from_file_location("pin_digest", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
pin_digest_module = importlib.util.module_from_spec(_spec)
sys.modules["pin_digest"] = pin_digest_module
_spec.loader.exec_module(pin_digest_module)
pin_digest = pin_digest_module.pin_digest

_OLD_DIGEST = "sha256:" + "a" * 64
_NEW_DIGEST = "sha256:" + "b" * 64
_UNRELATED_DIGEST = "sha256:" + "c" * 64

_SAMPLE_YAML = f"""\
image:
  repository: registry.gitlab.com/example/cryo
  tag: latest
  # some prose comment explaining history
  digest: "{_OLD_DIGEST}"

imagePullSecrets:
  - cryo-registry

migration:
  image:
    repository: registry.gitlab.com/example/cryo
    tag: latest
    digest: "{_OLD_DIGEST}"

native:
  enabled: true
  image:
    repository: registry.gitlab.com/example/cryo
    tag: latest
    digest: "{_OLD_DIGEST}"
  keda:
    enabled: true

web:
  enabled: true
  image:
    repository: registry.gitlab.com/example/cryo
    tag: web-latest
    digest: "{_UNRELATED_DIGEST}"

spark:
  k8s:
    image:
      repository: registry.gitlab.com/example/cryo
      tag: spark-latest
      digest: "{_UNRELATED_DIGEST}"

redis:
  image:
    digest: "{_UNRELATED_DIGEST}"
"""


def test_pin_digest_updates_exactly_the_three_target_paths() -> None:
    updated, paths = pin_digest(_SAMPLE_YAML, _NEW_DIGEST)
    assert sorted(paths) == ["image.digest", "migration.image.digest", "native.image.digest"]


def test_pin_digest_actually_replaces_the_value_in_all_three_places() -> None:
    updated, _ = pin_digest(_SAMPLE_YAML, _NEW_DIGEST)
    assert updated.count(f'"{_NEW_DIGEST}"') == 3
    assert _OLD_DIGEST not in updated


def test_pin_digest_never_touches_web_spark_or_redis_digests() -> None:
    """The exact boundary that matters -- these are separate images with their own release
    cadence and must never get silently repointed by a script meant for the main image."""
    updated, _ = pin_digest(_SAMPLE_YAML, _NEW_DIGEST)
    assert updated.count(f'"{_UNRELATED_DIGEST}"') == 3


def test_pin_digest_works_even_when_the_three_digests_have_already_drifted_apart() -> None:
    """Regression, 2026-08-10: the actual incident state -- two of the three digests already
    updated, one stale. A naive find-and-replace anchored on "the old digest" would only catch
    the still-matching ones. This script finds each digest by its YAML position (inside
    image:/migration.image:/native.image:), not by matching a specific old value, so it must
    still find and update all three even when they don't currently agree with each other."""
    drifted = _SAMPLE_YAML.replace(
        f'migration:\n  image:\n    repository: registry.gitlab.com/example/cryo\n    tag: latest\n    digest: "{_OLD_DIGEST}"',
        f'migration:\n  image:\n    repository: registry.gitlab.com/example/cryo\n    tag: latest\n    digest: "{_NEW_DIGEST}"',
    )
    assert drifted != _SAMPLE_YAML  # sanity: the replace actually did something

    updated, paths = pin_digest(drifted, _NEW_DIGEST)
    assert sorted(paths) == ["image.digest", "migration.image.digest", "native.image.digest"]
    assert updated.count(f'"{_NEW_DIGEST}"') == 3
    assert _OLD_DIGEST not in updated


def test_pin_digest_preserves_prose_comments() -> None:
    """The whole reason this is text-based rather than a YAML parse-and-redump -- values-
    prod.yaml's comments document *why*, not just *what*. A full round-trip would silently
    destroy them."""
    updated, _ = pin_digest(_SAMPLE_YAML, _NEW_DIGEST)
    assert "# some prose comment explaining history" in updated
