"""End-to-end native rewrite_data_files analyze + staged compaction execute."""

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
def test_native_compaction_analyze_reports_candidates(fixture: NativeRestFixture) -> None:
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    plan = MaintenancePlan(
        table_ref=fixture.table_ref,
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {"target_file_size_bytes": 256 * 1024 * 1024},
            ),
        ),
    )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "analyze"},
    )
    result = execute_request(request, config=_engine_config(), timeout_seconds=180)
    assert result.ok, result.error or result.stderr
    assert len(result.results) == 1

    optimize = result.results[0]
    assert optimize.operation == "rewrite_data_files"
    assert optimize.success is True
    assert optimize.input_files is not None
    assert optimize.input_files >= 1
    assert optimize.output_files is not None
    assert optimize.output_files >= 1


@requires_rest
@requires_engine
def test_native_compaction_staged_execute_when_flag_enabled(
    fixture: NativeRestFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    monkeypatch.setenv("CRYO_NATIVE_COMPACTION_EXECUTE", "1")
    before_sizes = list_object_sizes(fixture.location)

    plan = MaintenancePlan(
        table_ref=fixture.table_ref,
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                {"target_file_size_bytes": 256 * 1024 * 1024},
            ),
        ),
    )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute", "compaction_execute": True},
    )
    result = execute_request(request, config=_engine_config(), timeout_seconds=300)
    assert result.results, result.error or result.stderr

    optimize = result.results[0]
    assert optimize.operation == "rewrite_data_files"
    assert optimize.success is False
    assert optimize.error is not None
    assert "commit blocked upstream" in optimize.error.lower()
    assert optimize.bytes_written is not None
    assert optimize.bytes_written > 0
    assert optimize.output_files is not None
    assert optimize.output_files >= 1

    after_sizes = list_object_sizes(fixture.location)
    staging_objects = [
        uri
        for uri in after_sizes
        if ".cryo-compaction-staging/" in uri and uri.endswith(".parquet")
    ]
    assert staging_objects, "expected staged parquet under .cryo-compaction-staging/"
    assert len(after_sizes) >= len(before_sizes)
