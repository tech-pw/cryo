#!/usr/bin/env python3
"""Inspect Iceberg data-file counts and optionally compact fixture tables."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from pyspark.sql import SparkSession

# Allow running as `python scripts/smoke_file_counts.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spark_session import CATALOG_NAME, create_spark  # noqa: E402

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class FileMetrics:
    table: str
    file_count: int
    record_count: int
    total_bytes: int


def quote_simple_identifier(identifier: str) -> str:
    if not IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Unsupported identifier: {identifier!r}")
    return identifier


def table_identifier(namespace: str, table: str) -> str:
    return (
        f"{quote_simple_identifier(CATALOG_NAME)}."
        f"{quote_simple_identifier(namespace)}."
        f"{quote_simple_identifier(table)}"
    )


def discover_tables(spark: SparkSession, namespace: str) -> list[str]:
    rows = spark.sql(
        f"SHOW TABLES IN {quote_simple_identifier(CATALOG_NAME)}.{quote_simple_identifier(namespace)}"
    ).collect()
    return sorted(row.tableName for row in rows if not row.isTemporary)


def read_file_metrics(spark: SparkSession, namespace: str, table: str) -> FileMetrics:
    full_name = table_identifier(namespace, table)
    row = spark.sql(
        f"""
        SELECT
            count(*) AS file_count,
            coalesce(sum(record_count), 0) AS record_count,
            coalesce(sum(file_size_in_bytes), 0) AS total_bytes
        FROM {full_name}.files
        """
    ).first()
    return FileMetrics(
        table=full_name,
        file_count=int(row.file_count),
        record_count=int(row.record_count),
        total_bytes=int(row.total_bytes),
    )


def compact_table(
    spark: SparkSession,
    namespace: str,
    table: str,
    target_file_size_bytes: int,
    min_input_files: int,
) -> None:
    table_arg = f"{quote_simple_identifier(namespace)}.{quote_simple_identifier(table)}"
    spark.sql(
        f"""
        CALL {quote_simple_identifier(CATALOG_NAME)}.system.rewrite_data_files(
            table => '{table_arg}',
            options => map(
                'target-file-size-bytes', '{target_file_size_bytes}',
                'min-input-files', '{min_input_files}'
            )
        )
        """
    ).show(truncate=False)


def print_metrics(label: str, metrics: list[FileMetrics]) -> None:
    print(f"\n{label}")
    print("table,file_count,record_count,total_bytes")
    for item in metrics:
        print(f"{item.table},{item.file_count},{item.record_count},{item.total_bytes}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect local Iceberg fixture file counts before/after compaction."
    )
    parser.add_argument("--namespace", default="fixtures")
    parser.add_argument(
        "--table",
        action="append",
        dest="tables",
        help="Table to inspect. Repeat for multiple tables. Defaults to all tables in namespace.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Run Iceberg rewrite_data_files before collecting after metrics.",
    )
    parser.add_argument(
        "--assert-drop",
        action="store_true",
        help="Exit non-zero if compaction does not reduce at least one selected table's file count.",
    )
    parser.add_argument("--target-file-size-bytes", type=int, default=134_217_728)
    parser.add_argument("--min-input-files", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    spark = create_spark("smoke-file-counts")
    namespace = quote_simple_identifier(args.namespace)
    tables = args.tables or discover_tables(spark, namespace)
    if not tables:
        raise SystemExit(f"No tables found in {CATALOG_NAME}.{namespace}")

    before = [read_file_metrics(spark, namespace, table) for table in tables]
    print_metrics("before", before)

    after = before
    if args.compact:
        for table in tables:
            compact_table(
                spark,
                namespace,
                table,
                args.target_file_size_bytes,
                args.min_input_files,
            )
        after = [read_file_metrics(spark, namespace, table) for table in tables]
        print_metrics("after", after)

    spark.stop()

    if args.compact and args.assert_drop:
        reductions = [
            (pre.table, pre.file_count, post.file_count)
            for pre, post in zip(before, after, strict=True)
            if post.file_count < pre.file_count
        ]
        if not reductions:
            raise SystemExit("Compaction did not reduce file counts for any selected table")


if __name__ == "__main__":
    main()
