"""End-to-end native expire_snapshots + remove_orphan_files against REST + MinIO."""

from __future__ import annotations

import os

import pytest

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import (
    execute_request,
    is_cryo_engine_available,
    resolve_binary_path,
    verify_cryo_engine_spawnable,
)
from tests.integration.native.rest_env import (
    NativeRestFixture,
    ensure_rest_fixture,
    list_object_sizes,
    rest_uri,
    source_config,
)

requires_rest = pytest.mark.skipif(
    rest_uri() is None,
    reason="Set CRYO_REST_URI (e.g. http://localhost:8181) for native REST e2e tests",
)


def _engine_config() -> dict[str, str]:
    configured = os.getenv("CRYO_ENGINE_BINARY")
    if configured:
        return {"binary_path": configured}
    resolved = resolve_binary_path({})
    if resolved is None:
        return {}
    return {"binary_path": resolved}


requires_engine = pytest.mark.skipif(
    not is_cryo_engine_available(_engine_config()),
    reason="cryo-engine release/debug binary not found; run `cargo build --release` in cryo-engine/",
)


def _spawn_error() -> str | None:
    return verify_cryo_engine_spawnable(_engine_config())


@pytest.fixture(scope="module")
def fixture() -> NativeRestFixture:
    return ensure_rest_fixture()


@requires_rest
@requires_engine
def test_native_expire_and_remove_orphans(fixture: NativeRestFixture) -> None:
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    before_sizes = list_object_sizes(fixture.location)
    assert fixture.orphan_uri in before_sizes
    assert before_sizes[fixture.orphan_uri] == fixture.orphan_bytes

    plan = MaintenancePlan(
        table_ref=fixture.table_ref,
        mode=Mode.DEEP,
        operations=(
            Operation(
                OperationType.EXPIRE_SNAPSHOTS,
                {"retention_days": 0, "retain_last": 1},
            ),
            Operation(
                OperationType.REMOVE_ORPHAN_FILES,
                {"orphan_min_age_hours": 0},
            ),
        ),
    )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute"},
    )
    result = execute_request(request, config=_engine_config(), timeout_seconds=180)
    assert result.ok, result.error or result.stderr
    assert len(result.results) == 2

    expire_result = result.results[0]
    orphan_result = result.results[1]
    assert expire_result.operation == "expire_snapshots"
    assert expire_result.success is True
    assert expire_result.snapshots_expired is not None
    assert expire_result.snapshots_expired >= 1

    assert orphan_result.operation == "remove_orphan_files"
    assert orphan_result.success is True
    assert orphan_result.bytes_removed is not None
    assert orphan_result.bytes_removed >= fixture.orphan_bytes

    after_sizes = list_object_sizes(fixture.location)
    assert fixture.orphan_uri not in after_sizes
    assert after_sizes, "expected referenced table files to remain after orphan cleanup"
