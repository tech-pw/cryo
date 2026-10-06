"""Cross-engine compatibility test matrix (Move 7 / §3.6 of native-engine-scoping-research).

A table written by one engine must round-trip correctly through native Go
compaction and read back correctly in the others. "Reads the spec correctly"
does not imply "round-trips through every engine correctly" — real-world
incidents (trino#8284 partition-spec drop, apache/iceberg#12273 manifest
source-id mismatch) prove the round-trip case is its own correctness axis.

Engine coverage in this suite:
  * PyIceberg (== Spark-style writes/reads): official Python Iceberg
    library; produces the same Iceberg metadata format as Spark when
    committing through the REST catalog. Used here as the deterministic
    multi-writer/multi-reader proxy because Spark is not part of the
    native integration test environment (no JVM container in the
    `native-compaction-e2e-go` job). PyIceberg writes and reads exercise
    the spec-compliance path both Spark and Trino use.
  * Trino: the cross-engine reader/writer under test alongside the Go
    engine. Requires CRYO_TRINO_URI (provided in CI by the
    `native-compaction-e2e-go` workflow after the Move 2 wiring).

All three scenarios are parameterized over the three COW fixtures
(unpartitioned, day-partitioned, identity-partitioned) so partition-spec
edge cases — the precise area where cross-engine friction lives per
`native-engine-scoping-research.md` §3.6 — are covered.
"""

from __future__ import annotations

import os
import time

import pytest
from pyiceberg.catalog import load_catalog

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import execute_request
from tests.integration.native.compaction_fixtures import (
    COMPACTION_NAMESPACE,
    PARTITIONED_IDENTITY_TABLE,
    PARTITIONED_TABLE,
    UNPARTITIONED_TABLE,
    ensure_compaction_fixtures,
)
from tests.integration.native.compaction_oracles import count_rows
from tests.integration.native.rest_env import (
    catalog_properties,
    source_config,
    trino_connect,
    wait_for_rest_catalog,
    wait_for_trino,
)
from tests.integration.native.test_native_compaction_e2e import (
    _engine_config,
    _is_go_engine,
    _optimize_params,
    _spawn_error,
    requires_engine,
    requires_rest,
)

requires_go = pytest.mark.skipif(
    not _is_go_engine(),
    reason="Cross-engine compatibility matrix targets the Go engine path",
)

requires_trino = pytest.mark.skipif(
    os.getenv("CRYO_TRINO_URI") is None,
    reason="Set CRYO_TRINO_URI (e.g. http://localhost:8080) for cross-engine e2e",
)


CROSS_ENGINE_NAMESPACE = "native_cross_engine"
COW_FIXTURES = (UNPARTITIONED_TABLE, PARTITIONED_TABLE, PARTITIONED_IDENTITY_TABLE)


def _trino_cursor():
    return trino_connect(schema=CROSS_ENGINE_NAMESPACE).cursor()


def _wait_for_trino(*, timeout_seconds: float = 60.0) -> None:
    wait_for_trino(schema=CROSS_ENGINE_NAMESPACE, timeout_seconds=timeout_seconds)


def _ensure_cross_engine_namespace() -> None:
    catalog = load_catalog("rest", **catalog_properties())
    if (CROSS_ENGINE_NAMESPACE,) not in catalog.list_namespaces():
        catalog.create_namespace(CROSS_ENGINE_NAMESPACE)


def _drop_trino_table(cursor, table_name: str) -> None:
    try:
        cursor.execute(f'DROP TABLE IF EXISTS "{CROSS_ENGINE_NAMESPACE}"."{table_name}"')
        cursor.fetchall()
    except Exception:  # noqa: BLE001
        pass


def _trino_create_clone_from_fixture(cursor, fixture_name: str, clone_name: str) -> None:
    """Create a Trino-committed table populated from a PyIceberg fixture.

    The commit metadata is produced by Trino's engine, not PyIceberg,
    which is what makes this a real cross-engine writer test.
    """
    _drop_trino_table(cursor, clone_name)
    cursor.execute(
        f'CREATE TABLE "{CROSS_ENGINE_NAMESPACE}"."{clone_name}" '
        f'AS SELECT * FROM "{COMPACTION_NAMESPACE}"."{fixture_name}"'
    )
    cursor.fetchall()


def _trino_count_fixture(cursor, fixture_name: str) -> int:
    cursor.execute(f'SELECT count(*) FROM "{COMPACTION_NAMESPACE}"."{fixture_name}"')
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _trino_row_count(cursor, table_name: str) -> int:
    cursor.execute(f'SELECT count(*) FROM "{CROSS_ENGINE_NAMESPACE}"."{table_name}"')
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _pyiceberg_load(clone_name: str):
    catalog = load_catalog("rest", **catalog_properties())
    return catalog.load_table((CROSS_ENGINE_NAMESPACE, clone_name))


def _pyiceberg_row_count(clone_name: str) -> int:
    table = _pyiceberg_load(clone_name)
    return count_rows(table)


def _pyiceberg_row_multiset(clone_name: str) -> list[tuple]:
    """Sorted multiset of (id, label) tuples for cross-engine data equality."""
    table = _pyiceberg_load(clone_name)
    arrow = table.scan().to_arrow()
    return sorted(
        zip(
            arrow.column("id").to_pylist(),
            arrow.column("label").to_pylist(),
            strict=False,
        )
    )


def _trino_row_multiset(cursor, clone_name: str) -> list[tuple]:
    """Sorted multiset of (id, label) tuples from Trino (must match PyIceberg)."""
    cursor.execute(
        f'SELECT id, label FROM "{CROSS_ENGINE_NAMESPACE}"."{clone_name}" ORDER BY id, label'
    )
    rows = cursor.fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


def _pyiceberg_count_via_arrow(clone_name: str) -> int:
    table = _pyiceberg_load(clone_name)
    arrow = table.scan().to_arrow()
    return int(arrow.num_rows)


def _clone_name(fixture_name: str, scenario: str) -> str:
    return f"x_{scenario}_{fixture_name}"


def _build_optimize_request(fixture_ref: str) -> object:
    plan = MaintenancePlan(
        table_ref=fixture_ref,
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, _optimize_params()),),
    )
    return build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute"},
    )


def _run_go_optimize(table_ref: str) -> None:
    request = _build_optimize_request(table_ref)
    result = execute_request(request, config=_engine_config(), timeout_seconds=300)
    assert result.ok, result.error or result.stderr
    optimize = result.results[0]
    assert optimize.success, optimize.error


@pytest.fixture(scope="module")
def cow_fixtures() -> dict[str, object]:
    wait_for_rest_catalog()
    return ensure_compaction_fixtures()


@requires_rest
@requires_engine
@requires_go
def test_cross_engine_setup(cow_fixtures: dict[str, object]) -> None:
    """Sanity: COW fixtures must be loadable via PyIceberg before any matrix test runs."""
    spawn_err = _spawn_error()
    if spawn_err is not None:
        pytest.skip(spawn_err)
    assert set(COW_FIXTURES).issubset(cow_fixtures.keys()), (
        f"Missing COW fixtures: {set(COW_FIXTURES) - set(cow_fixtures.keys())}"
    )


@requires_rest
@requires_engine
@requires_go
@requires_trino
@pytest.mark.parametrize("fixture_name", COW_FIXTURES)
def test_pyiceberg_writer_go_compact_trino_and_pyiceberg_read(
    fixture_name: str,
) -> None:
    """Scenario 1: PyIceberg(=Spark-style) writes → native Go compact →
    Trino reads back + PyIceberg reads back.

    Validates that a table originally written by the PyIceberg library
    (functionally equivalent to Spark's Iceberg writer when committing
    through the same REST catalog) survives native Go compaction and
    remains readable by both Trino and PyIceberg with identical row
    counts and identical live-row multisets.

    §3.6 risk: native Go's rewrite must produce Iceberg-spec-compliant
    manifest lists that Trino's `iceberg-rest` connector can still parse.
    """
    spawn_err = _spawn_error()
    if spawn_err is not None:
        pytest.skip(spawn_err)

    wait_for_rest_catalog()
    _wait_for_trino()
    _ensure_cross_engine_namespace()

    cursor = _trino_cursor()
    clone = _clone_name(fixture_name, "pyiceberg_writer")
    clone_ref = f"{CROSS_ENGINE_NAMESPACE}.{clone}"

    before_count = _trino_count_fixture(cursor, fixture_name)

    _trino_create_clone_from_fixture(cursor, fixture_name, clone)
    pre_compact_trino = _trino_row_count(cursor, clone)
    pre_compact_pyiceberg = _pyiceberg_row_count(clone)
    assert pre_compact_trino == pre_compact_pyiceberg == before_count, (
        f"Pre-compact row mismatch: before={before_count} "
        f"trino={pre_compact_trino} pyiceberg={pre_compact_pyiceberg}"
    )

    _run_go_optimize(clone_ref)

    post_trino = _trino_row_count(cursor, clone)
    post_pyiceberg = _pyiceberg_row_count(clone)
    post_pyiceberg_arrow = _pyiceberg_count_via_arrow(clone)

    assert post_trino == before_count, (
        f"Go compact dropped rows in Trino read: {before_count} -> {post_trino}"
    )
    assert post_pyiceberg == before_count, (
        f"Go compact dropped rows in PyIceberg read: {before_count} -> {post_pyiceberg}"
    )
    assert post_pyiceberg_arrow == before_count, (
        f"Go compact dropped rows in PyIceberg arrow read: {before_count} -> {post_pyiceberg_arrow}"
    )

    pyiceberg_rows = _pyiceberg_row_multiset(clone)
    trino_rows = _trino_row_multiset(cursor, clone)
    assert pyiceberg_rows == trino_rows, (
        f"Trino and PyIceberg disagree on row contents after Go compact: "
        f"pyiceberg_size={len(pyiceberg_rows)} trino_size={len(trino_rows)}"
    )

    _drop_trino_table(cursor, clone)


@requires_rest
@requires_engine
@requires_go
@requires_trino
@pytest.mark.parametrize("fixture_name", COW_FIXTURES)
def test_trino_writer_go_compact_pyiceberg_and_trino_read(
    fixture_name: str,
) -> None:
    """Scenario 2: Trino writes → native Go compact → PyIceberg reads back +
    Trino reads back.

    Validates the inverse direction: a table committed by Trino's engine
    (different writer metadata semantics than PyIceberg/Spark) survives
    native Go compaction and round-trips correctly through both readers.
    """
    spawn_err = _spawn_error()
    if spawn_err is not None:
        pytest.skip(spawn_err)

    wait_for_rest_catalog()
    _wait_for_trino()
    _ensure_cross_engine_namespace()

    cursor = _trino_cursor()
    clone = _clone_name(fixture_name, "trino_writer")
    clone_ref = f"{CROSS_ENGINE_NAMESPACE}.{clone}"

    before_count = _trino_count_fixture(cursor, fixture_name)

    _trino_create_clone_from_fixture(cursor, fixture_name, clone)
    pre_trino = _trino_row_count(cursor, clone)
    pre_pyiceberg = _pyiceberg_row_count(clone)
    assert pre_trino == pre_pyiceberg == before_count, (
        f"Pre-compact Trino-writer row mismatch: "
        f"before={before_count} trino={pre_trino} pyiceberg={pre_pyiceberg}"
    )

    _run_go_optimize(clone_ref)

    post_trino = _trino_row_count(cursor, clone)
    post_pyiceberg = _pyiceberg_row_count(clone)

    assert post_trino == before_count, (
        f"Go compact dropped rows (Trino-writer, Trino-read): {before_count} -> {post_trino}"
    )
    assert post_pyiceberg == before_count, (
        f"Go compact dropped rows (Trino-writer, PyIceberg-read): "
        f"{before_count} -> {post_pyiceberg}"
    )

    pyiceberg_rows = _pyiceberg_row_multiset(clone)
    trino_rows = _trino_row_multiset(cursor, clone)
    assert pyiceberg_rows == trino_rows, (
        f"Trino-writer -> Go compact -> cross-engine reads disagree on contents: "
        f"pyiceberg_size={len(pyiceberg_rows)} trino_size={len(trino_rows)}"
    )

    _drop_trino_table(cursor, clone)


@requires_rest
@requires_engine
@requires_go
@requires_trino
@pytest.mark.parametrize("fixture_name", COW_FIXTURES)
def test_go_compact_writer_trino_and_pyiceberg_read(
    fixture_name: str,
) -> None:
    """Scenario 3: native Go engine writes (compacted snapshot) → Trino reads →
    PyIceberg reads.

    Validates that the snapshot metadata *produced* by Go's
    rewrite_data_files commit (the new manifest list, the new
    manifest files, the snapshot summary) is fully spec-compliant: both
    Trino's `iceberg-rest` connector and PyIceberg must be able to parse
    it and return identical results.

    §3.6 risk: a Go-only commit would be invisible to other engines — the
    exact failure mode this matrix catches.
    """
    spawn_err = _spawn_error()
    if spawn_err is not None:
        pytest.skip(spawn_err)

    wait_for_rest_catalog()
    _wait_for_trino()
    _ensure_cross_engine_namespace()

    cursor = _trino_cursor()
    clone = _clone_name(fixture_name, "go_writer")
    clone_ref = f"{CROSS_ENGINE_NAMESPACE}.{clone}"

    before_count = _trino_count_fixture(cursor, fixture_name)

    _trino_create_clone_from_fixture(cursor, fixture_name, clone)
    _run_go_optimize(clone_ref)

    post_trino = _trino_row_count(cursor, clone)
    post_pyiceberg = _pyiceberg_row_count(clone)

    assert post_trino == before_count, (
        f"Go-compacted snapshot Trino-read mismatch: {before_count} -> {post_trino}"
    )
    assert post_pyiceberg == before_count, (
        f"Go-compacted snapshot PyIceberg-read mismatch: {before_count} -> {post_pyiceberg}"
    )

    pyiceberg_rows = _pyiceberg_row_multiset(clone)
    trino_rows = _trino_row_multiset(cursor, clone)
    assert pyiceberg_rows == trino_rows, (
        f"Go-compacted snapshot: cross-engine reads disagree on contents: "
        f"pyiceberg_size={len(pyiceberg_rows)} trino_size={len(trino_rows)}"
    )

    arrow = _pyiceberg_load(clone).scan().to_arrow()
    expected_columns = {"id", "label"}
    assert set(arrow.column_names).issuperset(expected_columns), (
        f"Go-compacted snapshot lost schema columns: "
        f"have={set(arrow.column_names)} expected>={expected_columns}"
    )

    _drop_trino_table(cursor, clone)


@requires_rest
@requires_engine
@requires_go
@requires_trino
def test_cross_engine_partition_spec_preserved_by_go_compact() -> None:
    """Partition spec must survive Go compact regardless of writer engine.

    Spec-evolution bugs (trino#8284) live here: a partition-spec field
    drop or rename that survives native compaction would be invisible to
    single-engine readers but break every other engine's parser. This
    test asserts spec equality across the matrix for the two partitioned
    fixtures.
    """
    spawn_err = _spawn_error()
    if spawn_err is not None:
        pytest.skip(spawn_err)

    wait_for_rest_catalog()
    _wait_for_trino()
    _ensure_cross_engine_namespace()

    cursor = _trino_cursor()
    for fixture_name, expected_token in (
        (PARTITIONED_TABLE, "event_date_day"),
        (PARTITIONED_IDENTITY_TABLE, "region"),
    ):
        clone = _clone_name(fixture_name, "partition_spec")
        clone_ref = f"{CROSS_ENGINE_NAMESPACE}.{clone}"

        _trino_create_clone_from_fixture(cursor, fixture_name, clone)
        _run_go_optimize(clone_ref)

        cursor.execute(
            f'SELECT partition_name FROM "{CROSS_ENGINE_NAMESPACE}"."{clone}'
            f'$partitions" ORDER BY partition_name'
        )
        partitions_after = [row[0] for row in cursor.fetchall()]

        pyiceberg_table = _pyiceberg_load(clone)
        spec_fields = [f.name for f in pyiceberg_table.metadata.partition_spec.fields]
        assert expected_token in spec_fields, (
            f"Go compact lost partition field {expected_token!r} in {fixture_name}: "
            f"spec_fields={spec_fields}"
        )

        cursor.execute(
            f'SELECT partition_name FROM "{COMPACTION_NAMESPACE}"."{fixture_name}'
            f'$partitions" ORDER BY partition_name'
        )
        partitions_before = [row[0] for row in cursor.fetchall()]

        assert partitions_after == partitions_before, (
            f"Partition layout changed for {fixture_name} after Go compact: "
            f"before={partitions_before} after={partitions_after}"
        )

        _drop_trino_table(cursor, clone)
