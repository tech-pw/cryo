"""Shared helpers for native vs Trino differential e2e tests (§8.7)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pyiceberg.catalog import load_catalog

from cryo.domain.enums import Mode, OperationType
from cryo.domain.models import MaintenancePlan, Operation
from cryo.engines.native.payload import build_execute_request_for_operations
from cryo.engines.native.runner import execute_request
from tests.integration.native.compaction_fixtures import COMPACTION_NAMESPACE
from tests.integration.native.mor_inspect import live_int_column_values
from tests.integration.native.rest_env import (
    catalog_properties,
    source_config,
    trino_connect,
    wait_for_trino,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_BINARY_ENV = "CRYO_SEED_NATIVE_FIXTURES_BINARY"


def seed_binary() -> Path:
    configured = os.getenv(SEED_BINARY_ENV)
    if configured:
        return Path(configured)
    return REPO_ROOT / "engine-go" / "bin" / "seed-native-fixtures"


def seed_mor_table_copy(template: str, dest: str) -> None:
    binary = seed_binary()
    if not binary.is_file():
        raise FileNotFoundError(
            f"MOR fixture seeder not found at {binary}; run `make build-seed-native-fixtures`"
        )
    subprocess.run(
        [str(binary), "seed-table-copy", template, dest],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def trino_cursor():
    return trino_connect(schema=COMPACTION_NAMESPACE).cursor()


def wait_for_trino_ready(*, timeout_seconds: float = 90.0) -> None:
    wait_for_trino(schema=COMPACTION_NAMESPACE, timeout_seconds=timeout_seconds)


def drop_trino_table(cursor, table_name: str) -> None:
    try:
        cursor.execute(f'DROP TABLE IF EXISTS "{COMPACTION_NAMESPACE}"."{table_name}"')
        cursor.fetchall()
    except Exception:  # noqa: BLE001
        pass


def trino_create_clone(cursor, src_name: str, clone_name: str) -> None:
    drop_trino_table(cursor, clone_name)
    cursor.execute(
        f'CREATE TABLE "{COMPACTION_NAMESPACE}"."{clone_name}" '
        f'AS SELECT * FROM "{COMPACTION_NAMESPACE}"."{src_name}"'
    )
    cursor.fetchall()


def trino_count(cursor, table_name: str) -> int:
    cursor.execute(f'SELECT count(*) FROM "{COMPACTION_NAMESPACE}"."{table_name}"')
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def trino_live_id_multiset(cursor, table_name: str) -> tuple[int, ...]:
    cursor.execute(f'SELECT id FROM "{COMPACTION_NAMESPACE}"."{table_name}" ORDER BY id')
    return tuple(int(row[0]) for row in cursor.fetchall())


def go_live_id_multiset(table_ref: str) -> tuple[int, ...]:
    namespace, name = table_ref.split(".", 1)
    catalog = load_catalog("rest", **catalog_properties())
    table = catalog.load_table((namespace, name))
    return tuple(sorted(live_int_column_values(table, "id")))


def live_id_multiset(cursor, table_name: str, *, prefer_trino: bool) -> tuple[int, ...]:
    if prefer_trino:
        return trino_live_id_multiset(cursor, table_name)
    return go_live_id_multiset(f"{COMPACTION_NAMESPACE}.{table_name}")


def trino_optimize(cursor, table_name: str) -> None:
    cursor.execute(f'ALTER TABLE "{COMPACTION_NAMESPACE}"."{table_name}" EXECUTE optimize')
    cursor.fetchall()


def mor_optimize_params() -> dict[str, int]:
    return {
        "target_file_size_bytes": 4 * 1024 * 1024,
        "min_file_size_bytes": 512 * 1024,
        "min_input_files": 1,
    }


def cow_optimize_params() -> dict[str, int]:
    return {
        "target_file_size_bytes": 4 * 1024 * 1024,
        "min_file_size_bytes": 512 * 1024,
        "min_input_files": 3,
    }


def run_go_optimize(
    table_ref: str, engine_config: dict[str, str], params: dict[str, int]
) -> object:
    plan = MaintenancePlan(
        table_ref=table_ref,
        mode=Mode.STANDARD,
        operations=(Operation(OperationType.OPTIMIZE, params),),
    )
    request = build_execute_request_for_operations(
        plan=plan,
        operations=plan.operations,
        source_config=source_config(),
        destination_config={"operation_mode": "execute"},
    )
    return execute_request(request, config=engine_config, timeout_seconds=300)
