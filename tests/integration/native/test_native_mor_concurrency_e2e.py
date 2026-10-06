"""MOR compaction concurrency tests (§8.5 C2/C3)."""

from __future__ import annotations

import os
import threading
import time

import pytest

from tests.integration.native.compaction_fixtures import MOR_EQ_TABLE, MOR_POS_TABLE
from tests.integration.native.compaction_oracles import (
    assert_deleted_ids_absent,
    assert_files_exist_in_object_store,
    assert_metadata_stable,
    assert_row_count_unchanged,
    capture_table_snapshot,
)
from tests.integration.native.concurrency_harness import (
    commit_equality_delete,
    is_commit_conflict_error,
    join_optimize,
    reseed_mor_table,
    run_optimize,
    start_optimize_async,
)
from tests.integration.native.mor_inspect import count_live_rows
from tests.integration.native.test_native_mor_compaction_e2e import (
    _engine_config,
    _is_go_engine,
    _load_table,
    _spawn_error,
    _table_ref,
    requires_engine,
    requires_go,
    requires_rest,
)

INJECTED_DELETE_ID = 5
PREEXISTING_DELETE_ID = 2


@requires_rest
@requires_engine
@requires_go
def test_native_mor_c2_concurrent_delete_during_compaction() -> None:
    """C2: concurrent equality delete mid-compaction must not resurrect deleted rows."""
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    reseed_mor_table(MOR_EQ_TABLE)
    table_ref = _table_ref(MOR_EQ_TABLE)
    table = _load_table(table_ref)
    before = capture_table_snapshot(table)
    assert before.row_count == 9
    assert_deleted_ids_absent(table, {PREEXISTING_DELETE_ID})

    injected = threading.Event()
    pause_seconds = os.getenv("CRYO_TEST_PAUSE_BEFORE_COMMIT", "3")

    def _inject_on_progress(event: dict) -> None:
        if injected.is_set():
            return
        if event.get("event") != "progress":
            return
        injected.set()
        commit_equality_delete(table_ref, {INJECTED_DELETE_ID})

    holder = start_optimize_async(
        table_ref,
        engine_config=_engine_config(),
        extra_env={"CRYO_TEST_PAUSE_BEFORE_COMMIT": pause_seconds},
        on_progress=_inject_on_progress,
    )
    # Fallback injection if progress fired before hook attached.
    time.sleep(0.2)
    if not injected.is_set():
        commit_equality_delete(table_ref, {INJECTED_DELETE_ID})
        injected.set()

    result = join_optimize(holder)
    table = _load_table(table_ref)
    after = capture_table_snapshot(table)

    assert_deleted_ids_absent(table, {PREEXISTING_DELETE_ID, INJECTED_DELETE_ID})
    assert after.row_count == before.row_count - 1
    assert_files_exist_in_object_store(table, table.location())

    if result.ok:
        optimize = result.results[0]
        assert optimize.success is True
        assert_metadata_stable(before, after)
        assert after.data_file_count < before.data_file_count
    else:
        assert is_commit_conflict_error(result), result.error or result.stderr


@requires_rest
@requires_engine
@requires_go
def test_native_mor_c3_concurrent_compactions() -> None:
    """C3: overlapping compactions — one wins, the other aborts without corrupting data."""
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    reseed_mor_table(MOR_POS_TABLE)
    table_ref = _table_ref(MOR_POS_TABLE)
    table = _load_table(table_ref)
    before = capture_table_snapshot(table)
    assert before.row_count == 9

    pause_seconds = os.getenv("CRYO_TEST_PAUSE_BEFORE_COMMIT", "3")
    extra_env = {"CRYO_TEST_PAUSE_BEFORE_COMMIT": pause_seconds}

    first = start_optimize_async(table_ref, engine_config=_engine_config(), extra_env=extra_env)
    second = start_optimize_async(table_ref, engine_config=_engine_config(), extra_env=extra_env)

    result_a = join_optimize(first)
    result_b = join_optimize(second)

    outcomes = [result_a.ok, result_b.ok]
    assert outcomes.count(True) == 1, (
        f"expected exactly one successful compaction, got {outcomes} "
        f"errors=({result_a.error}, {result_b.error})"
    )
    failed = result_b if result_a.ok else result_a
    assert is_commit_conflict_error(failed), failed.error or failed.stderr

    table = _load_table(table_ref)
    after = capture_table_snapshot(table)
    assert_row_count_unchanged(before, after)
    assert_metadata_stable(before, after)
    assert after.data_file_count < before.data_file_count
    assert_files_exist_in_object_store(table, table.location())
    assert count_live_rows(table) == 9
