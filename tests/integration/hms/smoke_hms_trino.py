#!/usr/bin/env python3
"""Smoke-check Trino's iceberg_hms catalog against HMS fixture tables."""

from __future__ import annotations

import os
import sys

from trino.dbapi import connect

TRINO_HOST = os.getenv("TRINO_HOST", "localhost")
TRINO_PORT = int(os.getenv("TRINO_PORT", "8080"))
TRINO_USER = os.getenv("TRINO_USER", "cryo")
TRINO_CATALOG = os.getenv("TRINO_HMS_CATALOG", "iceberg_hms")
NAMESPACE = os.getenv("HMS_FIXTURE_NAMESPACE", "hms_issues")

SAMPLE_TABLES = (
    "balanced_control",
    "unpartitioned_many_small_files",
    "day_hot_partition_skew",
)


def main() -> int:
    conn = connect(host=TRINO_HOST, port=TRINO_PORT, user=TRINO_USER, catalog=TRINO_CATALOG)
    cursor = conn.cursor()
    cursor.execute(f"SHOW TABLES FROM {NAMESPACE}")
    tables = {row[0] for row in cursor.fetchall()}
    print(f"Found {len(tables)} tables in {TRINO_CATALOG}.{NAMESPACE}")
    missing = set(SAMPLE_TABLES) - tables
    if missing:
        print(f"Missing sample tables: {sorted(missing)}", file=sys.stderr)
        return 1

    for table_name in SAMPLE_TABLES:
        cursor.execute(f"SELECT count(*) FROM {NAMESPACE}.{table_name}")
        count = cursor.fetchone()[0]
        print(f"{NAMESPACE}.{table_name}: {count} rows")
        if count <= 0:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
