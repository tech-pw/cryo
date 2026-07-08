"""COW compaction fault-injection tests (§8.5 C4–C8)."""

from __future__ import annotations

import pytest

from tests.integration.native.compaction_fixtures import UNPARTITIONED_TABLE, ensure_compaction_fixtures
from tests.integration.native.compaction_oracles import (
    assert_failure_no_op,
    assert_file_count_reduced,
    assert_files_exist_in_object_store,
    assert_metadata_stable,
    assert_row_count_unchanged,
    capture_table_snapshot,
)
from tests.integration.native.fault_injection_harness import (
    configure_commit_retries,
    fault_env,
    is_commit_failure,
    orphan_data_paths,
    run_optimize_with_faults,
    run_remove_orphans,
)
from tests.integration.native.test_native_compaction_e2e import (
    _engine_config,
    _is_go_engine,
    _load_table,
    _spawn_error,
    requires_engine,
    requires_rest,
)


@requires_rest
@requires_engine
@pytest.mark.skipif(not _is_go_engine(), reason="Go engine fault-injection testhooks")
def test_native_c4_commit_retry_exhaustion_no_op(compaction_fixtures: dict[str, object]) -> None:
    """C4: forced commit conflicts exhaust retries and leave the table unchanged."""
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    fixture = compaction_fixtures[UNPARTITIONED_TABLE]
    table_ref = fixture.table_ref  # type: ignore[attr-defined]
    table = _load_table(table_ref)
    before = capture_table_snapshot(table)

    result = run_optimize_with_faults(
        table_ref,
        engine_config=_engine_config(),
        fault_env_vars=fault_env(
            CRYO_TEST_FAIL_COMMIT="exhaust",
            CRYO_TEST_COMMIT_NUM_RETRIES="1",
        ),
    )

    assert not result.ok, result.error or result.stderr
    assert is_commit_failure(result), result.error or result.stderr

    table = _load_table(table_ref)
    after = capture_table_snapshot(table)
    assert_failure_no_op(before, after)
    assert_files_exist_in_object_store(table, fixture.location)  # type: ignore[attr-defined]


@requires_rest
@requires_engine
@pytest.mark.skipif(not _is_go_engine(), reason="Go engine fault-injection testhooks")
def test_native_c5_crash_before_commit_leaves_orphans(compaction_fixtures: dict[str, object]) -> None:
    """C5: crash after write/before commit is a no-op; orphans are removable."""
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    fixture = compaction_fixtures[UNPARTITIONED_TABLE]
    table_ref = fixture.table_ref  # type: ignore[attr-defined]
    location = fixture.location  # type: ignore[attr-defined]
    table = _load_table(table_ref)
    before = capture_table_snapshot(table)

    result = run_optimize_with_faults(
        table_ref,
        engine_config=_engine_config(),
        fault_env_vars=fault_env(CRYO_TEST_EXIT_BEFORE_COMMIT="1"),
    )

    assert not result.ok
    table = _load_table(table_ref)
    after = capture_table_snapshot(table)
    assert_failure_no_op(before, after)

    orphans = orphan_data_paths(location, after.data_file_paths)
    assert orphans, "expected orphan data files after crash-before-commit injection"

    cleanup = run_remove_orphans(table_ref, engine_config=_engine_config())
    assert cleanup.ok, cleanup.error or cleanup.stderr
    remaining = orphan_data_paths(location, after.data_file_paths)
    assert not remaining, f"orphan cleanup left data files behind: {sorted(remaining)[:3]}"


@requires_rest
@requires_engine
@pytest.mark.skipif(not _is_go_engine(), reason="Go engine fault-injection testhooks")
def test_native_c6_catalog_error_is_atomic_no_op(compaction_fixtures: dict[str, object]) -> None:
    """C6: catalog 500 during commit must not partially apply metadata."""
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    fixture = compaction_fixtures[UNPARTITIONED_TABLE]
    table_ref = fixture.table_ref  # type: ignore[attr-defined]
    table = _load_table(table_ref)
    before = capture_table_snapshot(table)

    result = run_optimize_with_faults(
        table_ref,
        engine_config=_engine_config(),
        fault_env_vars=fault_env(CRYO_TEST_FAIL_COMMIT="catalog500"),
    )

    assert not result.ok
    assert is_commit_failure(result), result.error or result.stderr

    table = _load_table(table_ref)
    after = capture_table_snapshot(table)
    assert_failure_no_op(before, after)
    assert_files_exist_in_object_store(table, fixture.location)  # type: ignore[attr-defined]


@requires_rest
@requires_engine
@pytest.mark.skipif(not _is_go_engine(), reason="Go engine fault-injection testhooks")
def test_native_c7_object_store_put_failure_aborts_chunk(compaction_fixtures: dict[str, object]) -> None:
    """C7: failed output PUT aborts compaction without dangling manifest refs."""
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    fixture = compaction_fixtures[UNPARTITIONED_TABLE]
    table_ref = fixture.table_ref  # type: ignore[attr-defined]
    table = _load_table(table_ref)
    before = capture_table_snapshot(table)

    result = run_optimize_with_faults(
        table_ref,
        engine_config=_engine_config(),
        fault_env_vars=fault_env(CRYO_TEST_FAIL_DATA_PUT_ON="1"),
    )

    assert not result.ok
    assert "put failure" in (result.error or result.stderr or "").lower()

    table = _load_table(table_ref)
    after = capture_table_snapshot(table)
    assert_failure_no_op(before, after)
    assert_files_exist_in_object_store(table, fixture.location)  # type: ignore[attr-defined]


@requires_rest
@requires_engine
@pytest.mark.skipif(not _is_go_engine(), reason="Go engine fault-injection testhooks")
def test_native_c8_late_commit_retry_is_idempotent(compaction_fixtures: dict[str, object]) -> None:
    """C8: one injected conflict then success must not double-apply the rewrite."""
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    fixture = compaction_fixtures[UNPARTITIONED_TABLE]
    table_ref = fixture.table_ref  # type: ignore[attr-defined]
    table = _load_table(table_ref)
    configure_commit_retries(table, num_retries=2)
    before = capture_table_snapshot(table)

    result = run_optimize_with_faults(
        table_ref,
        engine_config=_engine_config(),
        fault_env_vars=fault_env(CRYO_TEST_FAIL_COMMIT_REMAINING="1"),
    )
    assert result.ok, result.error or result.stderr

    table = _load_table(table_ref)
    after = capture_table_snapshot(table)
    assert_row_count_unchanged(before, after)
    assert_metadata_stable(before, after)
    assert_file_count_reduced(before, after)
    assert after.data_file_count >= 1
    assert_files_exist_in_object_store(table, fixture.location)  # type: ignore[attr-defined]


@pytest.fixture(scope="module")
def compaction_fixtures() -> dict[str, object]:
    return ensure_compaction_fixtures()
