"""End-to-end native rewrite_data_files analyze + COW compaction execute."""

from __future__ import annotations

import os
from datetime import date

import pytest
from pyiceberg.catalog import load_catalog

from cryo.domain.enums import Mode, OperationType, PartitionTransform
from cryo.domain.models import MaintenancePlan, Operation, PartitionPredicate, PartitionPredicateTerm
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import (
    execute_request,
    is_cryo_engine_available,
    resolve_binary_path,
    verify_cryo_engine_spawnable,
)
from tests.integration.native.compaction_fixtures import (
    PARTITIONED_IDENTITY_TABLE,
    PARTITIONED_TABLE,
    UNPARTITIONED_TABLE,
    ensure_compaction_fixtures,
)
from tests.integration.native.compaction_oracles import (
    assert_file_count_reduced,
    assert_files_exist_in_object_store,
    assert_metadata_stable,
    assert_partition_scope_unchanged,
    assert_row_count_unchanged,
    assert_snapshot_lineage,
    capture_table_snapshot,
    data_files_for_partition,
    epoch_day,
    paths_for_path_token,
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


requires_engine = pytest.mark.skipif(
    not is_cryo_engine_available(_engine_config()),
    reason="cryo-engine binary not found; build cryo-engine-go or cryo-engine (Rust)",
)


def _spawn_error() -> str | None:
    return verify_cryo_engine_spawnable(_engine_config())


def _optimize_params() -> dict[str, int]:
    return {
        "target_file_size_bytes": 4 * 1024 * 1024,
        "min_file_size_bytes": 512 * 1024,
        "min_input_files": 3,
    }


def _load_table(table_ref: str):
    namespace, name = table_ref.split(".", 1)
    catalog = load_catalog("rest", **catalog_properties())
    return catalog.load_table((namespace, name))


@pytest.fixture(scope="module")
def compaction_fixtures() -> dict[str, object]:
    return ensure_compaction_fixtures()


@requires_rest
@requires_engine
def test_native_compaction_analyze_reports_candidates(compaction_fixtures: dict[str, object]) -> None:
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    fixture = compaction_fixtures[UNPARTITIONED_TABLE]
    plan = MaintenancePlan(
        table_ref=fixture.table_ref,  # type: ignore[attr-defined]
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, _optimize_params()),),
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
@pytest.mark.skipif(_is_go_engine(), reason="Rust-only staged execute path (upstream commit blocked)")
def test_native_compaction_staged_execute_when_flag_enabled(
    compaction_fixtures: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    fixture = compaction_fixtures[UNPARTITIONED_TABLE]
    monkeypatch.setenv("CRYO_NATIVE_COMPACTION_EXECUTE", "1")

    plan = MaintenancePlan(
        table_ref=fixture.table_ref,  # type: ignore[attr-defined]
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, _optimize_params()),),
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


@requires_rest
@requires_engine
@pytest.mark.skipif(not _is_go_engine(), reason="Go engine COW compaction commit path")
def test_native_compaction_execute_cow_unpartitioned(compaction_fixtures: dict[str, object]) -> None:
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    fixture = compaction_fixtures[UNPARTITIONED_TABLE]
    table = _load_table(fixture.table_ref)  # type: ignore[attr-defined]
    before = capture_table_snapshot(table)
    parent_snapshot_id = before.snapshot_id

    plan = MaintenancePlan(
        table_ref=fixture.table_ref,  # type: ignore[attr-defined]
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, _optimize_params()),),
    )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute"},
    )
    result = execute_request(request, config=_engine_config(), timeout_seconds=300)
    assert result.ok, result.error or result.stderr

    optimize = result.results[0]
    assert optimize.success is True
    assert optimize.input_files is not None and optimize.input_files >= 3
    assert optimize.output_files is not None and optimize.output_files >= 1
    assert optimize.output_files < optimize.input_files
    assert optimize.bytes_written is not None and optimize.bytes_written > 0

    table = _load_table(fixture.table_ref)  # type: ignore[attr-defined]
    after = capture_table_snapshot(table)
    assert_row_count_unchanged(before, after)
    assert_metadata_stable(before, after)
    assert_file_count_reduced(before, after)
    assert_files_exist_in_object_store(table, fixture.location)  # type: ignore[attr-defined]
    assert_snapshot_lineage(table, parent_snapshot_id)


@requires_rest
@requires_engine
def test_native_compaction_analyze_partition_scoped(compaction_fixtures: dict[str, object]) -> None:
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    fixture = compaction_fixtures[PARTITIONED_TABLE]
    scoped_epoch = epoch_day(date(2025, 5, 2))
    predicate = PartitionPredicate(
        terms=(
            PartitionPredicateTerm(
                base_column="event_date",
                transform=PartitionTransform.DAY,
                value=scoped_epoch,
                col_type="date",
                partition_field="event_date_day",
            ),
        )
    )
    plan = MaintenancePlan(
        table_ref=fixture.table_ref,  # type: ignore[attr-defined]
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                _optimize_params(),
                predicates=(predicate,),
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
    optimize = result.results[0]
    assert optimize.success is True
    assert optimize.input_files is not None
    assert 1 <= optimize.input_files <= 4


@requires_rest
@requires_engine
@pytest.mark.skipif(not _is_go_engine(), reason="Go engine COW compaction commit path")
def test_native_compaction_execute_partition_scoped_identity(
    compaction_fixtures: dict[str, object],
) -> None:
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    fixture = compaction_fixtures[PARTITIONED_IDENTITY_TABLE]
    table = _load_table(fixture.table_ref)  # type: ignore[attr-defined]
    before = capture_table_snapshot(table)

    scoped_paths = paths_for_path_token(table, "region=us")
    assert scoped_paths, "expected data files in scoped identity partition"
    other_paths = paths_for_path_token(table, "region=eu")
    assert other_paths, "expected data files outside scoped partition"

    predicate = PartitionPredicate(
        terms=(
            PartitionPredicateTerm(
                base_column="region",
                transform=PartitionTransform.IDENTITY,
                value="us",
                col_type="string",
                partition_field="region",
            ),
        )
    )
    plan = MaintenancePlan(
        table_ref=fixture.table_ref,  # type: ignore[attr-defined]
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                _optimize_params(),
                predicates=(predicate,),
            ),
        ),
    )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute"},
    )
    result = execute_request(request, config=_engine_config(), timeout_seconds=300)
    assert result.ok, result.error or result.stderr
    optimize = result.results[0]
    assert optimize.success is True
    assert optimize.input_files is not None and optimize.input_files >= 1

    table = _load_table(fixture.table_ref)  # type: ignore[attr-defined]
    after = capture_table_snapshot(table)
    assert_row_count_unchanged(before, after)
    assert_metadata_stable(before, after)
    assert_partition_scope_unchanged(before, after, outside_path_token="region=eu")
    assert_files_exist_in_object_store(table, fixture.location)  # type: ignore[attr-defined]


@requires_rest
@requires_engine
@pytest.mark.skipif(not _is_go_engine(), reason="Go engine COW compaction commit path")
def test_native_compaction_execute_day_partition_scoped(compaction_fixtures: dict[str, object]) -> None:
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    fixture = compaction_fixtures[PARTITIONED_TABLE]
    table = _load_table(fixture.table_ref)  # type: ignore[attr-defined]
    before = capture_table_snapshot(table)

    scoped_day = date(2025, 5, 2)
    scoped_epoch = epoch_day(scoped_day)
    scoped_paths = data_files_for_partition(table, epoch_day_value=scoped_epoch)
    assert scoped_paths, "expected data files in scoped partition"

    other_paths = data_files_for_partition(table, epoch_day_value=epoch_day(date(2025, 5, 3)))
    assert other_paths, "expected data files outside scoped partition"

    predicate = PartitionPredicate(
        terms=(
            PartitionPredicateTerm(
                base_column="event_date",
                transform=PartitionTransform.DAY,
                value=scoped_epoch,
                col_type="date",
                partition_field="event_date_day",
            ),
        )
    )
    plan = MaintenancePlan(
        table_ref=fixture.table_ref,  # type: ignore[attr-defined]
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                _optimize_params(),
                predicates=(predicate,),
            ),
        ),
    )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute"},
    )
    result = execute_request(request, config=_engine_config(), timeout_seconds=300)
    assert result.ok, result.error or result.stderr
    optimize = result.results[0]
    assert optimize.success is True
    assert optimize.input_files is not None and optimize.input_files >= 1

    table = _load_table(fixture.table_ref)  # type: ignore[attr-defined]
    after = capture_table_snapshot(table)
    assert_row_count_unchanged(before, after)
    assert_metadata_stable(before, after)
    assert_partition_scope_unchanged(before, after, outside_path_token="event_date_day=2025-05-03")
    assert_files_exist_in_object_store(table, fixture.location)  # type: ignore[attr-defined]
