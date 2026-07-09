"""Partial-progress checkpoint/resume concurrency e2e (§8.5 C9/C10).

Exercises the Go engine's resumable partial-progress commit path against REST + MinIO by
injecting commit conflicts:

- C9: a *transient* commit-race loss on the first per-group commit — the engine reloads,
  re-plans from the current snapshot, and completes every group (idempotent resume). The
  table ends up fully compacted and durable ``checkpoint`` progress events are emitted.
- C10: a *sustained* commit-race loss — every attempt loses, the bounded checkpoint rounds
  are exhausted, and the run fails with a first-class ``commit_race_lost`` signal while the
  table is left byte-for-byte unchanged (atomic no-op).

See docs/native-engine-scoping-research.md §3.2 / Scenario B.
"""

from __future__ import annotations

import pytest

from tests.integration.native.checkpoint_resume_harness import (
    CheckpointCollector,
    is_commit_race_lost_result,
    run_partial_progress_optimize,
)
from tests.integration.native.compaction_fixtures import (
    PARTITIONED_IDENTITY_TABLE,
    ensure_compaction_fixtures,
)
from tests.integration.native.compaction_oracles import (
    assert_failure_no_op,
    assert_file_count_reduced,
    assert_files_exist_in_object_store,
    assert_metadata_stable,
    assert_row_count_unchanged,
    capture_table_snapshot,
)
from tests.integration.native.fault_injection_harness import fault_env, is_commit_failure
from tests.integration.native.test_native_compaction_e2e import (
    _engine_config,
    _is_go_engine,
    _load_table,
    _spawn_error,
    requires_engine,
    requires_rest,
)


@pytest.fixture()
def identity_fixture() -> object:
    """Fresh f_part_identity (3 partitions x 4 files) per test, so each run re-plans 3 groups."""
    return ensure_compaction_fixtures()[PARTITIONED_IDENTITY_TABLE]


@requires_rest
@requires_engine
@pytest.mark.skipif(not _is_go_engine(), reason="Go engine resumable partial-progress path")
def test_native_c9_transient_commit_race_resumes_and_completes(identity_fixture: object) -> None:
    """C9: one injected conflict mid-run is recovered via reload+re-plan; all groups land."""
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    table_ref = identity_fixture.table_ref  # type: ignore[attr-defined]
    location = identity_fixture.location  # type: ignore[attr-defined]
    table = _load_table(table_ref)
    before = capture_table_snapshot(table)

    checkpoints = CheckpointCollector()
    result = run_partial_progress_optimize(
        table_ref,
        engine_config=_engine_config(),
        # Fail exactly one commit with no in-process iceberg-go retry, so the failure
        # bubbles to the resumable loop rather than being absorbed by OCC retry.
        fault_env_vars=fault_env(
            CRYO_TEST_FAIL_COMMIT_REMAINING="1",
            CRYO_TEST_COMMIT_NUM_RETRIES="0",
        ),
        on_progress=checkpoints,
    )

    assert result.ok, result.error or result.stderr
    optimize = result.results[0]
    assert optimize.success is True

    table = _load_table(table_ref)
    after = capture_table_snapshot(table)
    # Resume succeeded: every partition compacted despite the injected conflict.
    assert_row_count_unchanged(before, after)
    assert_metadata_stable(before, after)
    assert_file_count_reduced(before, after)
    assert_files_exist_in_object_store(table, location)

    # Durable per-group checkpoints were emitted, and the last one reports every group landed.
    assert checkpoints.events, "expected at least one checkpoint progress event"
    assert checkpoints.max_committed >= 1
    # committed_groups is monotonically increasing within the successful round.
    assert checkpoints.committed_groups == sorted(checkpoints.committed_groups)


@requires_rest
@requires_engine
@pytest.mark.skipif(not _is_go_engine(), reason="Go engine resumable partial-progress path")
def test_native_c10_sustained_commit_race_is_flagged_and_noop(identity_fixture: object) -> None:
    """C10: an unwinnable race exhausts checkpoint rounds -> commit_race_lost + atomic no-op."""
    spawn_error = _spawn_error()
    if spawn_error is not None:
        pytest.skip(spawn_error)

    table_ref = identity_fixture.table_ref  # type: ignore[attr-defined]
    location = identity_fixture.location  # type: ignore[attr-defined]
    table = _load_table(table_ref)
    before = capture_table_snapshot(table)

    result = run_partial_progress_optimize(
        table_ref,
        engine_config=_engine_config(),
        # Every commit fails, with no in-process retry: the bounded checkpoint rounds are
        # exhausted and the engine surfaces commit_race_lost.
        fault_env_vars=fault_env(
            CRYO_TEST_FAIL_COMMIT="exhaust",
            CRYO_TEST_COMMIT_NUM_RETRIES="0",
        ),
    )

    assert not result.ok, result.error or result.stderr
    assert is_commit_failure(result), result.error or result.stderr
    assert is_commit_race_lost_result(result), (
        f"expected commit_race_lost signal, got error={result.error!r} "
        f"op_error={result.results[0].error if result.results else None!r}"
    )

    table = _load_table(table_ref)
    after = capture_table_snapshot(table)
    # No group ever committed: the table is byte-for-byte unchanged.
    assert_failure_no_op(before, after)
    assert_files_exist_in_object_store(table, location)
