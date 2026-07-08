"""Go-backed live row reads for MOR tables PyIceberg cannot scan."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from pyiceberg.table import Table

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_BINARY_ENV = "CRYO_SEED_NATIVE_FIXTURES_BINARY"


def _resolve_inspect_binary() -> Path:
    configured = os.getenv(SEED_BINARY_ENV)
    if configured:
        return Path(configured)
    return REPO_ROOT / "engine-go" / "bin" / "seed-native-fixtures"


def table_ref(table: Table) -> str:
    return f"{table.name()[0]}.{table.name()[1]}"


def is_mor_table(table: Table) -> bool:
    props = table.properties or {}
    return str(props.get("write.delete.mode", "")).lower() == "merge-on-read"


def count_live_rows(table: Table) -> int:
    if is_mor_table(table):
        return _run_inspect("count-live-rows", table_ref(table))
    return _count_rows_pyiceberg(table)


def live_int_column_values(table: Table, column: str) -> list[int]:
    if is_mor_table(table):
        raw = _run_inspect("live-int-column", table_ref(table), column)
        if not raw:
            return []
        return [int(part) for part in raw.split(",")]
    return _live_int_column_pyiceberg(table, column)


def _live_int_column_pyiceberg(table: Table, column: str) -> list[int]:
    import pyarrow as pa

    arrow = table.scan(selected_fields=(column,)).to_arrow()
    if isinstance(arrow, pa.Table):
        col = arrow.column(column)
    elif isinstance(arrow, pa.RecordBatch):
        col = arrow.column(0)
    else:
        raise TypeError(f"unexpected scan result type: {type(arrow)!r}")
    return [int(col[idx].as_py()) for idx in range(len(col))]


def _count_rows_pyiceberg(table: Table) -> int:
    import pyarrow as pa

    arrow = table.scan().to_arrow()
    if isinstance(arrow, pa.RecordBatch):
        return arrow.num_rows
    return arrow.num_rows


def _run_inspect(command: str, *args: str) -> int | str:
    binary = _resolve_inspect_binary()
    if not binary.is_file():
        raise FileNotFoundError(
            f"MOR inspect binary not found at {binary}; run `make build-seed-native-fixtures`"
        )
    result = subprocess.run(
        [str(binary), command, *args],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    output = result.stdout.strip()
    if command == "count-live-rows":
        return int(output)
    return output
