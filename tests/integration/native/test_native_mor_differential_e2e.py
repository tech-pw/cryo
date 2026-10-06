"""MOR differential testing: Go native engine vs Trino ground truth (§8.7).

Equality-delete fixtures use Trino OPTIMIZE + Trino reads as the reference.
Position/mixed fixtures use the Go live-row inspect oracle on a single
deterministic clone because Trino cannot read the fixture seeder's
position-delete Parquet yet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from tests.integration.native.compaction_fixtures import (
    MOR_EQ_TABLE,
    MOR_MIXED_TABLE,
    MOR_POS_TABLE,
)
from tests.integration.native.differential_harness import (
    drop_trino_table,
    go_live_id_multiset,
    mor_optimize_params,
    run_go_optimize,
    seed_mor_table_copy,
    trino_count,
    trino_cursor,
    trino_live_id_multiset,
    trino_optimize,
    wait_for_trino_ready,
)
from tests.integration.native.rest_env import wait_for_rest_catalog
from tests.integration.native.test_native_compaction_e2e import requires_engine, requires_rest
from tests.integration.native.test_native_mor_compaction_e2e import (
    _engine_config,
    _is_go_engine,
    _spawn_error,
)

requires_go = pytest.mark.skipif(
    not _is_go_engine(),
    reason="MOR differential test requires Go engine (Trino is the reference where supported)",
)

requires_trino = pytest.mark.skipif(
    os.getenv("CRYO_TRINO_URI") is None,
    reason="Set CRYO_TRINO_URI (e.g. http://localhost:8080) for MOR differential e2e",
)


@requires_trino
@requires_rest
@requires_engine
@requires_go
def test_native_vs_trino_mor_eq_compaction_live_row_multiset() -> None:
    """Trino and Go MOR OPTIMIZE must preserve the same live-row multiset (equality deletes)."""
    spawn_err = _spawn_error()
    if spawn_err is not None:
        pytest.skip(spawn_err)

    wait_for_rest_catalog()
    wait_for_trino_ready()

    fixture_name = MOR_EQ_TABLE
    trino_clone = f"{fixture_name}_mor_diff_trino"
    go_clone = f"{fixture_name}_mor_diff_go"
    seed_mor_table_copy(fixture_name, trino_clone)
    seed_mor_table_copy(fixture_name, go_clone)

    cursor = trino_cursor()
    try:
        baseline = trino_live_id_multiset(cursor, trino_clone)
        assert trino_live_id_multiset(cursor, go_clone) == baseline
        assert 2 not in baseline

        trino_optimize(cursor, trino_clone)
        after_trino = trino_live_id_multiset(cursor, trino_clone)
        assert trino_count(cursor, trino_clone) == len(after_trino)
        assert after_trino == baseline

        result = run_go_optimize(
            f"native_compaction.{go_clone}",
            _engine_config(),
            mor_optimize_params(),
        )
        assert result.ok, result.error or result.stderr
        assert result.results[0].success, result.results[0].error

        after_go = trino_live_id_multiset(cursor, go_clone)
        assert after_go == baseline
        assert after_go == after_trino
        assert 2 not in after_go
    finally:
        drop_trino_table(cursor, trino_clone)
        drop_trino_table(cursor, go_clone)


@dataclass(frozen=True)
class GoOracleMorCase:
    fixture_name: str
    expected_absent_ids: frozenset[int]


GO_ORACLE_MOR_CASES = (
    GoOracleMorCase(MOR_POS_TABLE, frozenset({0})),
    GoOracleMorCase(MOR_MIXED_TABLE, frozenset({4})),
)


@requires_rest
@requires_engine
@requires_go
@pytest.mark.parametrize("case", GO_ORACLE_MOR_CASES, ids=lambda c: c.fixture_name)
def test_native_mor_go_compaction_preserves_go_oracle_multiset(
    case: GoOracleMorCase,
) -> None:
    """Go MOR OPTIMIZE must preserve the Go-oracle live-row multiset (pos/mixed fixtures)."""
    spawn_err = _spawn_error()
    if spawn_err is not None:
        pytest.skip(spawn_err)

    wait_for_rest_catalog()

    clone = f"{case.fixture_name}_mor_diff_go"
    seed_mor_table_copy(case.fixture_name, clone)
    table_ref = f"native_compaction.{clone}"

    baseline = go_live_id_multiset(table_ref)
    for deleted_id in case.expected_absent_ids:
        assert deleted_id not in baseline

    result = run_go_optimize(table_ref, _engine_config(), mor_optimize_params())
    assert result.ok, result.error or result.stderr
    assert result.results[0].success, result.results[0].error

    after = go_live_id_multiset(table_ref)
    assert after == baseline
    for deleted_id in case.expected_absent_ids:
        assert deleted_id not in after

    if os.getenv("CRYO_TRINO_URI"):
        cursor = trino_cursor()
        try:
            drop_trino_table(cursor, clone)
        finally:
            cursor.close()
