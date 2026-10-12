"""End-to-end native MOR rewrite_data_files compaction (Phase D)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from pyiceberg.catalog import load_catalog

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import (
    execute_request,
    is_cryo_engine_available,
    resolve_binary_path,
    verify_cryo_engine_spawnable,
)
from tests.integration.native.compaction_fixtures import (
    MOR_EQ_TABLE,
    MOR_MIXED_TABLE,
    MOR_PARTIAL_TABLE,
    MOR_POS_TABLE,
)
from tests.integration.native.compaction_oracles import (
    assert_delete_files_reduced,
    assert_deleted_ids_absent,
    assert_file_count_reduced,
    assert_files_exist_in_object_store,
    assert_metadata_stable,
    assert_row_count_unchanged,
    assert_snapshot_lineage,
    capture_table_snapshot,
    count_delete_files,
)
from tests.integration.native.rest_env import catalog_properties, rest_uri, source_config

requires_rest = pytest.mark.skipif(
    rest_uri() is None,
    reason="Set CRYO_REST_URI (e.g. http://localhost:8181) for native REST e2e tests",
)


def _engine_config() -> dict[str, str]:
    config: dict[str, str] = {}
    configured = os.getenv("CRYO_ENGINE_BINARY")
    if configured:
        config["binary_path"] = configured
    impl = os.getenv("CRYO_ENGINE_IMPL")
    if impl:
        config["engine_impl"] = impl
    if config:
        return config
    resolved = resolve_binary_path({})
    if resolved is None:
        return {}
    return {"binary_path": resolved}


def _is_go_engine() -> bool:
    impl = os.getenv("CRYO_ENGINE_IMPL", "").strip().lower()
    if impl == "go":
        return True
    binary = _engine_config().get("binary_path", "")
    return "cryo-engine-go" in binary


requires_go = pytest.mark.skipif(not _is_go_engine(), reason="Go engine MOR compaction path")
requires_engine = pytest.mark.skipif(
    not is_cryo_engine_available(_engine_config()),
    reason="cryo-engine binary not found; build cryo-engine-go",
)


def _spawn_error() -> str | None:
    return verify_cryo_engine_spawnable(_engine_config())


def _seed_binary() -> Path:
    configured = os.getenv("CRYO_SEED_NATIVE_FIXTURES_BINARY")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "engine-go" / "bin" / "seed-native-fixtures"


def _reseed_mor_table(table_name: str) -> None:
    binary = _seed_binary()
    if not binary.is_file():
        pytest.skip(
            f"MOR fixture seeder not found at {binary}; run `make build-seed-native-fixtures`"
        )
    subprocess.run(
        [str(binary), "seed-table", table_name],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


@pytest.fixture(autouse=True)
def _fresh_mor_table(request: pytest.FixtureRequest) -> None:
    callspec = getattr(request.node, "callspec", None)
    if callspec is not None and "fixture_name" in callspec.params:
        _reseed_mor_table(callspec.params["fixture_name"])
        return
    if request.node.name == "test_native_mor_partial_fixture_has_retained_delete_files":
        _reseed_mor_table(MOR_PARTIAL_TABLE)


def _optimize_params() -> dict[str, int]:
    return {
        "target_file_size_bytes": 4 * 1024 * 1024,
        "min_file_size_bytes": 512 * 1024,
        "min_input_files": 1,
    }


def _table_ref(name: str) -> str:
    return f"native_compaction.{name}"


def _load_table(table_ref: str):
    namespace, name = table_ref.split(".", 1)
    catalog = load_catalog("rest", **catalog_properties())
    return catalog.load_table((namespace, name))


def _run_optimize(table_ref: str) -> object:
    plan = MaintenancePlan(
        table_ref=table_ref,
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, _optimize_params()),),
    )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute", "engine_impl": "go"},
    )
    return execute_request(request, config=_engine_config(), timeout_seconds=300)


@requires_rest
@requires_engine
@requires_go
@pytest.mark.parametrize(
    ("fixture_name", "deleted_ids"),
    [
        (MOR_POS_TABLE, set()),
        (MOR_EQ_TABLE, {2}),
        (MOR_MIXED_TABLE, {4}),  # eq delete; position delete on id=1 covered by f_mor_pos
    ],
)
def test_native_mor_compaction_execute(fixture_name: str, deleted_ids: set[int]) -> None:
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    table_ref = _table_ref(fixture_name)
    table = _load_table(table_ref)
    before = capture_table_snapshot(table)
    assert before.delete_file_count > 0, "expected MOR fixture to ship delete files"
    parent_snapshot_id = before.snapshot_id

    result = _run_optimize(table_ref)
    assert result.ok, result.error or result.stderr
    optimize = result.results[0]
    assert optimize.success is True
    assert optimize.input_files is not None and optimize.input_files >= 1
    assert optimize.output_files is not None and optimize.output_files >= 1

    table = _load_table(table_ref)
    after = capture_table_snapshot(table)
    assert_row_count_unchanged(before, after)
    assert_metadata_stable(before, after)
    assert_file_count_reduced(before, after)
    assert_delete_files_reduced(before, after)
    if deleted_ids:
        assert_deleted_ids_absent(table, deleted_ids)
    assert_files_exist_in_object_store(table, table.location())
    assert_snapshot_lineage(table, parent_snapshot_id)


@requires_rest
@requires_engine
@requires_go
def test_native_mor_partial_fixture_has_retained_delete_files() -> None:
    """Sanity-check partial-consumption layout before partitioned scoped execute lands."""
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    table = _load_table(_table_ref(MOR_PARTIAL_TABLE))
    assert count_delete_files(table) > 0
    snapshot = capture_table_snapshot(table)
    assert snapshot.row_count == 3
