"""Differential testing: Go native engine vs Trino ground truth (§8.7).

Compares live-row counts from Go-native OPTIMIZE against Trino's
ALTER TABLE ... EXECUTE optimize on identical table clones. Each test
uses fresh CTAS clones so the shared compaction fixtures are not mutated.
"""

from __future__ import annotations

import os

import pytest

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import execute_request
from tests.integration.native.compaction_fixtures import (
    COMPACTION_NAMESPACE,
    PARTITIONED_IDENTITY_TABLE,
    PARTITIONED_TABLE,
    UNPARTITIONED_TABLE,
)
from tests.integration.native.rest_env import (
    source_config,
    trino_connect,
    wait_for_rest_catalog,
    wait_for_trino,
)
from tests.integration.native.test_native_compaction_e2e import (
    _optimize_params,
    requires_engine,
    requires_rest,
)
from tests.integration.native.test_native_mor_compaction_e2e import (
    _engine_config,
    _is_go_engine,
    _spawn_error,
)

requires_go = pytest.mark.skipif(
    not _is_go_engine(),
    reason="Differential test requires Go engine (Trino is the reference; Go is under test)",
)

requires_trino = pytest.mark.skipif(
    os.getenv("CRYO_TRINO_URI") is None,
    reason="Set CRYO_TRINO_URI (e.g. http://localhost:8080) for differential e2e",
)


def _trino_cursor():
    return trino_connect(schema=COMPACTION_NAMESPACE).cursor()


def _wait_for_trino(*, timeout_seconds: float = 90.0) -> None:
    wait_for_trino(schema=COMPACTION_NAMESPACE, timeout_seconds=timeout_seconds)


def _drop_trino_table(cursor, table_name: str) -> None:
    try:
        cursor.execute(f'DROP TABLE IF EXISTS "{COMPACTION_NAMESPACE}"."{table_name}"')
        cursor.fetchall()
    except Exception:  # noqa: BLE001
        pass


def _trino_create_clone(cursor, src_name: str, clone_name: str) -> None:
    _drop_trino_table(cursor, clone_name)
    cursor.execute(
        f'CREATE TABLE "{COMPACTION_NAMESPACE}"."{clone_name}" '
        f'AS SELECT * FROM "{COMPACTION_NAMESPACE}"."{src_name}"'
    )
    cursor.fetchall()


def _trino_count(cursor, table_name: str) -> int:
    cursor.execute(f'SELECT count(*) FROM "{COMPACTION_NAMESPACE}"."{table_name}"')
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _trino_optimize(cursor, table_name: str) -> None:
    cursor.execute(f'ALTER TABLE "{COMPACTION_NAMESPACE}"."{table_name}" EXECUTE optimize')
    cursor.fetchall()


@requires_trino
@requires_rest
@requires_engine
@requires_go
@pytest.mark.parametrize(
    "fixture_name",
    [UNPARTITIONED_TABLE, PARTITIONED_IDENTITY_TABLE],
)
def test_native_vs_trino_compaction_row_count_unchanged(
    fixture_name: str,
) -> None:
    """Trino OPTIMIZE and Go OPTIMIZE must preserve live-row count on identical clones."""
    spawn_err = _spawn_error()
    if spawn_err is not None:
        pytest.skip(spawn_err)

    wait_for_rest_catalog()
    _wait_for_trino()

    cursor = _trino_cursor()
    original_count = _trino_count(cursor, fixture_name)

    trino_clone = f"{fixture_name}_diff_trino"
    go_clone = f"{fixture_name}_diff_go"
    _trino_create_clone(cursor, fixture_name, trino_clone)
    _trino_create_clone(cursor, fixture_name, go_clone)

    _trino_optimize(cursor, trino_clone)
    trino_count = _trino_count(cursor, trino_clone)

    plan = MaintenancePlan(
        table_ref=f"{COMPACTION_NAMESPACE}.{go_clone}",
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
    assert optimize.success, optimize.error

    go_count = _trino_count(cursor, go_clone)

    _drop_trino_table(cursor, trino_clone)
    _drop_trino_table(cursor, go_clone)

    assert trino_count == original_count, (
        f"Trino OPTIMIZE changed row count: {original_count} -> {trino_count}"
    )
    assert go_count == original_count, (
        f"Go OPTIMIZE changed row count: {original_count} -> {go_count}"
    )
    assert trino_count == go_count, (
        f"Trino and Go disagree on row count after OPTIMIZE: "
        f"Trino={trino_count} Go={go_count} (expected {original_count})"
    )


@requires_trino
@requires_rest
@requires_engine
@requires_go
@pytest.mark.xfail(
    reason="iceberg-go v0.6.0 day partition rewrite writes time.Time for int day field",
    strict=False,
)
def test_native_vs_trino_day_partition_compaction() -> None:
    """Day-partitioned COW differential — blocked on iceberg-go partition commit typing."""
    spawn_err = _spawn_error()
    if spawn_err is not None:
        pytest.skip(spawn_err)

    wait_for_rest_catalog()
    _wait_for_trino()

    fixture_name = PARTITIONED_TABLE
    cursor = _trino_cursor()
    original_count = _trino_count(cursor, fixture_name)

    go_clone = f"{fixture_name}_diff_go"
    _trino_create_clone(cursor, fixture_name, go_clone)

    plan = MaintenancePlan(
        table_ref=f"{COMPACTION_NAMESPACE}.{go_clone}",
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
    assert result.results[0].success, result.results[0].error

    go_count = _trino_count(cursor, go_clone)
    _drop_trino_table(cursor, go_clone)
    assert go_count == original_count
