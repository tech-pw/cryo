"""Differential testing: Go native engine vs Trino ground truth (§8.7).

Compares live-row multisets from Go-native OPTIMIZE against Trino's
ALTER TABLE ... EXECUTE optimize on identical seeded tables.  This is the
strongest correctness guarantee that iceberg-go commit behaviour matches the
established Trino reference engine — especially for MOR tables where the
whole correctness risk is mishandling delete files.
"""

from __future__ import annotations

import os
import time

import pytest

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import execute_request
from tests.integration.native.compaction_fixtures import (
    PARTITIONED_IDENTITY_TABLE,
    PARTITIONED_TABLE,
    UNPARTITIONED_TABLE,
)
from tests.integration.native.compaction_oracles import count_rows
from tests.integration.native.rest_env import (
    catalog_properties,
    source_config,
    wait_for_rest_catalog,
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


def _trino_cursor():
    import trino

    return trino.connect(
        host="localhost",
        port=8080,
        user="cryo-test",
        catalog="iceberg_rest",
        schema="native_compaction",
    ).cursor()


def _wait_for_trino(*, timeout_seconds: float = 60.0) -> None:
    import trino

    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            trino.connect(
                host="localhost",
                port=8080,
                user="cryo-test",
                catalog="iceberg_rest",
                schema="native_compaction",
            ).cursor().execute("SELECT 1")
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(2.0)
    raise TimeoutError(f"Trino not ready: {last_error}") from last_error


def _count_trino_rows(cursor, full_table_name: str) -> int:
    cursor.execute(f'SELECT count(*) FROM "{full_table_name}"')
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _trino_optimize(cursor, full_table_name: str) -> None:
    cursor.execute(f'ALTER TABLE "{full_table_name}" EXECUTE optimize')
    cursor.fetchall()


def _clone_table_location(src_location: str, clone_suffix: str) -> str:
    src_bucket, src_prefix = src_location.replace("s3://", "").split("/", 1)
    clone_prefix = src_prefix.rstrip("/") + clone_suffix
    return f"s3://{src_bucket}/{clone_prefix}"


def _copy_s3_tree(src_location: str, dst_location: str) -> None:
    from tests.integration.native.rest_env import s3_client

    src_bucket, src_prefix = src_location.replace("s3://", "").split("/", 1)
    dst_bucket, dst_prefix = dst_location.replace("s3://", "").split("/", 1)
    client = s3_client()
    paginator = client.get_paginate("ListObjectsV2")
    params: dict[str, object] = {"Bucket": src_bucket}
    if src_prefix:
        params["Prefix"] = src_prefix
    for page in paginator.paginate(**params):
        for obj in page.get("Contents", []):
            src_key = str(obj["Key"])
            dst_key = dst_prefix + src_key[len(src_prefix) :]
            client.copy(
                {"Bucket": src_bucket, "Key": src_key},
                dst_bucket,
                dst_key,
            )


def _trino_create_clone(
    cursor, src_table_ref: str, clone_table_ref: str, clone_location: str
) -> None:
    ns, name = clone_table_ref.split(".", 1)
    cursor.execute(
        f'CREATE TABLE IF NOT EXISTS "{ns}".{name}" '
        f'WITH (location = "{clone_location}") '
        f'AS SELECT * FROM "{src_table_ref}" WITH NO DATA'
    )
    cursor.fetchall()


requires_trino = pytest.mark.skipif(
    os.getenv("CRYO_TRINO_URI") is None,
    reason="Set CRYO_TRINO_URI (e.g. http://localhost:8080) for differential e2e",
)


@requires_trino
@requires_rest
@requires_engine
@requires_go
@pytest.mark.parametrize(
    "fixture_name",
    [UNPARTITIONED_TABLE, PARTITIONED_TABLE, PARTITIONED_IDENTITY_TABLE],
)
def test_native_vs_trino_compaction_row_count_unchanged(
    fixture_name: str,
) -> None:
    """Both Trino OPTIMIZE and Go OPTIMIZE must preserve live-row count."""
    spawn_err = _spawn_error()
    if spawn_err is not None:
        pytest.skip(spawn_err)

    wait_for_rest_catalog()
    _wait_for_trino()

    from pyiceberg.catalog import load_catalog

    catalog = load_catalog("rest", **catalog_properties())

    fixture_ref = f"native_compaction.{fixture_name}"
    table = catalog.load_table((fixture_name,))
    before_count = count_rows(table)
    table_location = table.location()

    clone_location = _clone_table_location(table_location, "_trino_clone")
    clone_ref = f"native_compaction.{fixture_name}_trino_clone"

    _copy_s3_tree(table_location, clone_location)

    cursor = _trino_cursor()
    _trino_create_clone(cursor, fixture_ref, clone_ref, clone_location)
    _trino_optimize(cursor, clone_ref)
    trino_count = _count_trino_rows(cursor, clone_ref)

    ns, name = clone_ref.split(".", 1)
    cursor.execute(f'DROP TABLE "{ns}"."{name}"')
    cursor.fetchall()

    plan = MaintenancePlan(
        table_ref=fixture_ref,
        mode=Mode.STANDARD,
        operations=(
            Operation(
                OperationType.OPTIMIZE,
                _optimize_params(),
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
    assert optimize.success, optimize.error

    table = catalog.load_table((fixture_name,))
    after_count = count_rows(table)

    assert trino_count == before_count, (
        f"Trino OPTIMIZE changed row count: {before_count} -> {trino_count}"
    )
    assert after_count == before_count, (
        f"Go OPTIMIZE changed row count: {before_count} -> {after_count}"
    )
    assert trino_count == after_count, (
        f"Trino and Go disagree on row count after OPTIMIZE: "
        f"Trino={trino_count} Go={after_count} (expected {before_count})"
    )
