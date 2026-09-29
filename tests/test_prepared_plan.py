from __future__ import annotations

import ast
import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from cryo.maintenance.prepared_plan import (
    CODE_IDEMPOTENCY_CONFLICT,
    CODE_STALE_PLAN,
    DEFAULT_PLAN_TTL,
    ConflictPayload,
    EvidenceVersion,
    IdempotencyRecord,
    Membership,
    PlanDisposition,
    PreparedPlanConflict,
    Recipe,
    TableDisposition,
    canonical_sha256,
    expires_at,
    lookup_idempotency,
    plan_digest,
    plan_material,
    summarize_conflict,
)

_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _ROOT / "cryo" / "maintenance" / "prepared_plan.py"


def _recipe(**updates: object) -> Recipe:
    base: dict[str, object] = {
        "destination_id": "spark-prod",
        "preset_id": "nightly",
        "revision": "3",
        "mode": "standard",
        "operations": ("optimize", "expire_snapshots"),
        "params": {"target_file_size_bytes": 134217728},
        "resource_plan_version": "lanes-v1",
    }
    base.update(updates)
    return Recipe(**base)  # type: ignore[arg-type]


def _evidence() -> tuple[EvidenceVersion, ...]:
    return (
        EvidenceVersion("table-b", scan_version="scan-2", snapshot_version="20"),
        EvidenceVersion("table-a", scan_version="scan-1", snapshot_version="10"),
    )


def test_canonical_sha256_matches_the_gateway_encoding() -> None:
    payload = {"b": 1, "a": {"z": 2, "y": 3}}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    assert canonical_sha256(payload) == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    assert canonical_sha256({"b": 1, "a": 2}) == canonical_sha256({"a": 2, "b": 1})


def test_digest_is_stable_and_ignores_secrets() -> None:
    membership = Membership(("table-b", "table-a", "table-a"))
    recipe = _recipe(
        params={
            "target_file_size_bytes": 134217728,
            "s3_secret_key": "super-secret-value",
            "nested": {"password": "hidden", "keep": 1},
        }
    )
    reordered = _recipe(
        params={
            "nested": {"keep": 1, "password": "other-secret"},
            "s3_secret_key": "different-secret",
            "target_file_size_bytes": 134217728,
        }
    )
    first = plan_digest(membership, recipe, _evidence())
    second = plan_digest(["table-a", "table-b"], reordered, tuple(reversed(_evidence())))
    assert first == second
    material = json.dumps(plan_material(membership, recipe, _evidence()))
    assert "super-secret-value" not in material
    assert "hidden" not in material
    assert material.index("table-a") < material.index("table-b")


def test_digest_changes_with_membership_recipe_or_evidence() -> None:
    recipe = _recipe()
    evidence = _evidence()
    base = plan_digest(("table-a",), recipe, evidence)
    assert plan_digest(("table-a", "table-c"), recipe, evidence) != base
    assert plan_digest(("table-a",), _recipe(revision="4"), evidence) != base
    changed = (
        EvidenceVersion("table-b", scan_version="scan-2", snapshot_version="21"),
        EvidenceVersion("table-a", scan_version="scan-1", snapshot_version="10"),
    )
    assert plan_digest(("table-a",), recipe, changed) != base
    assert plan_digest(("table-a",), _recipe(resource_plan_version="lanes-v2"), evidence) != base


def test_expires_at_defaults_to_ten_minutes() -> None:
    now = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)
    assert DEFAULT_PLAN_TTL == timedelta(minutes=10)
    assert expires_at(now) == now + timedelta(minutes=10)
    assert expires_at(datetime(2026, 9, 29, 6, 0)) == now + timedelta(minutes=10)
    later = datetime(2026, 9, 29, 8, 0, tzinfo=timezone(timedelta(hours=2)))
    assert expires_at(later, ttl=timedelta(seconds=60)) == datetime(2026, 9, 29, 6, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        expires_at(now, ttl=timedelta(0))


def test_idempotency_is_scoped_and_conflicts_on_digest_mismatch() -> None:
    stored = IdempotencyRecord(
        tenant_id="tenant-a",
        principal="operator",
        key="submit-1",
        digest="digest-a",
        plan_id="plan-1",
    )
    other_tenant = IdempotencyRecord(
        tenant_id="tenant-b",
        principal="operator",
        key="submit-1",
        digest="digest-b",
        plan_id="plan-2",
    )
    records = (stored, other_tenant)
    assert (
        lookup_idempotency(
            records,
            tenant_id="tenant-a",
            principal="operator",
            key="submit-1",
            digest="digest-a",
        )
        == stored
    )
    assert (
        lookup_idempotency(
            records,
            tenant_id="tenant-b",
            principal="someone-else",
            key="submit-1",
            digest="digest-b",
        )
        is None
    )
    with pytest.raises(PreparedPlanConflict) as caught:
        lookup_idempotency(
            records,
            tenant_id="tenant-a",
            principal="operator",
            key="submit-1",
            digest="digest-other",
        )
    assert caught.value.payload == ConflictPayload(
        code=CODE_IDEMPOTENCY_CONFLICT,
        blocked_delta=0,
        recipe_changed=False,
        deactivated=0,
    )
    with pytest.raises(ValueError):
        lookup_idempotency(records, tenant_id="tenant-a", principal=" ", key="submit-1", digest="x")


def test_conflict_payload_summarizes_the_diff() -> None:
    reviewed = PlanDisposition(
        recipe=_recipe(),
        tables=(
            TableDisposition("a", blocked=False, active=True),
            TableDisposition("b", blocked=False, active=True),
            TableDisposition("c", blocked=False, active=True),
        ),
    )
    live = PlanDisposition(
        recipe=_recipe(params={"target_file_size_bytes": 134217728, "password": "nope"}),
        tables=(
            TableDisposition("a", blocked=True, active=True),
            TableDisposition("b", blocked=True, active=True),
            TableDisposition("c", blocked=False, active=False),
        ),
    )
    payload = summarize_conflict(reviewed, live)
    assert payload.code == CODE_STALE_PLAN
    assert payload.blocked_delta == 2
    assert payload.recipe_changed is False
    assert payload.deactivated == 1

    changed = summarize_conflict(reviewed, PlanDisposition(recipe=_recipe(mode="full"), tables=()))
    assert changed.recipe_changed is True
    assert changed.deactivated == 3
    assert changed.blocked_delta == 0

    stored = IdempotencyRecord("tenant-a", "operator", "submit-1", "digest-a", "plan-1")
    with pytest.raises(PreparedPlanConflict) as caught:
        lookup_idempotency(
            (stored,),
            tenant_id="tenant-a",
            principal="operator",
            key="submit-1",
            digest="digest-other",
            reviewed=reviewed,
            live=live,
        )
    assert caught.value.payload.code == CODE_IDEMPOTENCY_CONFLICT
    assert caught.value.payload.blocked_delta == 2
    assert caught.value.payload.recipe_changed is False
    assert caught.value.payload.deactivated == 1


def test_prepared_plan_does_not_import_assistant_or_engines() -> None:
    tree = ast.parse(_MODULE.read_text())
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    assert modules
    assert not any(name == "cryo.ai" or name.startswith("cryo.ai.") for name in modules)
    assert not any(name.startswith("cryo.engines") for name in modules)


def test_gateway_hash_delegates_without_changing_encoding() -> None:
    from cryo.ai.actions import gateway as gateway_mod

    payload = {"table_ids": ["b", "a"], "filter": {"schema": "analytics"}}
    assert gateway_mod._sha256(payload) == canonical_sha256(payload)
