#!/usr/bin/env python3
"""Create or append fragmented Iceberg test data on demand for Cryo maintenance testing."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests" / "integration" / "hms"))

BASE_SCHEMA_SQL = """
    id BIGINT,
    user_id INT,
    region STRING,
    category STRING,
    event_date DATE,
    event_hour INT,
    event_ts TIMESTAMP,
    amount DOUBLE,
    batch_id INT
"""

NESTED_SCHEMA_SQL = (
    BASE_SCHEMA_SQL
    + """,
    payload STRUCT<device: STRING, browser: STRING, score: DOUBLE>,
    tags ARRAY<STRING>,
    attributes MAP<STRING, STRING>
"""
)

DEFAULT_NAMESPACE = {
    "rest": "load_test",
    "hms": "hms_load_test",
}


@dataclass(frozen=True)
class TablePreset:
    name: str
    schema_sql: str
    partition_sql: str
    nested: bool = False


PRESETS: dict[str, TablePreset] = {
    "unpartitioned": TablePreset("unpartitioned", BASE_SCHEMA_SQL, ""),
    "day": TablePreset("day", BASE_SCHEMA_SQL, "days(event_date)"),
    "month": TablePreset("month", BASE_SCHEMA_SQL, "months(event_date)"),
    "multi": TablePreset(
        "multi",
        BASE_SCHEMA_SQL,
        "region, category, days(event_date)",
    ),
    "nested": TablePreset(
        "nested",
        NESTED_SCHEMA_SQL,
        "region, days(event_ts)",
        nested=True,
    ),
}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_str(name: str, default: str | None = None) -> str | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or append fragmented Iceberg tables for Cryo load testing.",
    )
    parser.add_argument(
        "--catalog",
        choices=("rest", "hms"),
        default=_env_str("LOAD_CATALOG", "rest"),
        help="Iceberg catalog backend (default: rest).",
    )
    parser.add_argument(
        "--preset",
        choices=tuple(PRESETS.keys()),
        default=_env_str("LOAD_PRESET"),
        help="Named maintenance profile (implies default table name).",
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="Iceberg namespace (default: load_test or hms_load_test).",
    )
    parser.add_argument(
        "--table",
        default=_env_str("LOAD_TABLE"),
        help="Table name (default: preset name when --preset is set).",
    )
    parser.add_argument(
        "--partition",
        default=_env_str("LOAD_PARTITION"),
        help="Partition SQL override, e.g. days(event_date).",
    )
    parser.add_argument(
        "--mode",
        choices=("create", "append", "create-or-append"),
        default=_env_str("LOAD_MODE", "create-or-append"),
        help="Create table, append only, or create when missing (default).",
    )
    parser.add_argument(
        "--snapshots",
        type=int,
        default=_env_int("LOAD_SNAPSHOTS", 5),
        help="Number of append commits (new snapshots).",
    )
    parser.add_argument(
        "--rows-per-snapshot",
        type=int,
        default=_env_int("LOAD_ROWS_PER_SNAPSHOT", 1_000_000),
        help="Rows written per snapshot.",
    )
    parser.add_argument(
        "--files-per-snapshot",
        type=int,
        default=_env_int("LOAD_FILES_PER_SNAPSHOT", 8),
        help="Repartition count per snapshot for small-file fragmentation.",
    )
    parser.add_argument(
        "--max-records-per-file",
        type=int,
        default=_env_int("LOAD_MAX_RECORDS_PER_FILE", 4),
        help="Spark maxRecordsPerFile cap during writes.",
    )
    parser.add_argument(
        "--target-file-size-bytes",
        type=int,
        default=_env_int("LOAD_TARGET_FILE_SIZE_BYTES", 65_536),
        help="write.target-file-size-bytes on table create.",
    )
    parser.add_argument(
        "--id-offset",
        type=int,
        default=None,
        help="Starting row id (auto-detect max(id)+1 on append when omitted).",
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        default=os.getenv("LOAD_DROP_EXISTING", "0") == "1",
        help="Drop the table before create (requires --force).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=os.getenv("LOAD_FORCE", "0") == "1",
        help="Confirm destructive operations such as --drop-existing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.getenv("LOAD_DRY_RUN", "0") == "1",
        help="Print the plan without writing data.",
    )
    args = parser.parse_args(argv)

    if args.preset is None and args.table is None:
        parser.error("Provide --preset or --table.")

    if args.namespace is None:
        args.namespace = _env_str("LOAD_NAMESPACE") or DEFAULT_NAMESPACE[args.catalog]

    if args.table is None:
        args.table = PRESETS[args.preset].name

    if args.drop_existing and not args.force:
        parser.error("--drop-existing requires --force.")

    if args.mode == "append" and args.drop_existing:
        parser.error("--drop-existing is incompatible with --mode append.")

    return args


def catalog_name(catalog: str) -> str:
    return "rest" if catalog == "rest" else "hms"


def _prepare_catalog_env(catalog: str) -> None:
    """Set catalog connection env vars before Spark session creation."""
    in_docker = Path("/.dockerenv").exists()
    if catalog == "rest":
        os.environ["ICEBERG_CATALOG"] = "rest"
        os.environ["ICEBERG_REST_URI"] = (
            "http://iceberg-rest:8181" if in_docker else "http://localhost:8181"
        )
        os.environ["ICEBERG_WAREHOUSE"] = "s3://warehouse/"
    else:
        os.environ["ICEBERG_CATALOG"] = "hms"
        os.environ["ICEBERG_HMS_URI"] = (
            "thrift://hive-metastore:9083" if in_docker else "thrift://localhost:9083"
        )
        os.environ["ICEBERG_WAREHOUSE"] = "s3a://warehouse/"
    os.environ["S3_ENDPOINT"] = "http://minio:9000" if in_docker else "http://localhost:9000"
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "minioadmin")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "minioadmin")
    os.environ.setdefault("AWS_REGION", "us-east-1")


def create_spark_session(catalog: str, app_name: str) -> SparkSession:
    import importlib

    _prepare_catalog_env(catalog)
    if catalog == "rest":
        import spark_session

        importlib.reload(spark_session)
        return spark_session.create_spark(app_name)
    import spark_hms_session

    importlib.reload(spark_hms_session)
    return spark_hms_session.create_spark(app_name)


def full_table_name(catalog: str, namespace: str, table: str) -> str:
    return f"{catalog_name(catalog)}.{namespace}.{table}"


def resolve_schema(args: argparse.Namespace) -> tuple[str, str, bool]:
    if args.preset is not None:
        preset = PRESETS[args.preset]
        schema_sql = preset.schema_sql
        partition_sql = args.partition if args.partition is not None else preset.partition_sql
        nested = preset.nested
    else:
        schema_sql = BASE_SCHEMA_SQL
        partition_sql = args.partition or ""
        nested = False
    return schema_sql, partition_sql, nested


def table_exists(spark: SparkSession, full_name: str) -> bool:
    return spark.catalog.tableExists(full_name)


def configure_fragmentation(
    spark: SparkSession,
    *,
    files_per_snapshot: int,
    max_records_per_file: int,
) -> None:
    spark.conf.set("spark.sql.adaptive.enabled", "false")
    spark.conf.set("spark.sql.shuffle.partitions", str(files_per_snapshot))
    spark.conf.set("spark.sql.files.maxRecordsPerFile", str(max_records_per_file))


def create_table(
    spark: SparkSession,
    full_name: str,
    *,
    schema_sql: str,
    partition_sql: str,
    target_file_size_bytes: int,
) -> None:
    partition_clause = f"\nPARTITIONED BY ({partition_sql})" if partition_sql else ""
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {full_name} (
            {schema_sql}
        )
        USING iceberg{partition_clause}
        TBLPROPERTIES (
            'format-version' = '2',
            'write.distribution-mode' = 'none',
            'write.target-file-size-bytes' = '{target_file_size_bytes}'
        )
        """
    )


def next_id_offset(spark: SparkSession, full_name: str) -> int:
    row = spark.sql(f"SELECT coalesce(max(id), -1) + 1 AS next_id FROM {full_name}").first()
    return int(row.next_id)


def generate_load_rows(
    spark: SparkSession,
    *,
    start_id: int,
    row_count: int,
    snapshot_index: int,
    nested: bool,
) -> DataFrame:
    regions = ("us", "eu", "apac", "latam")
    categories = ("alpha", "beta", "gamma", "delta")
    sparse_modulo = 120

    event_date = F.date_add(
        F.to_date(F.lit("2020-01-01")),
        ((F.col("id") % F.lit(sparse_modulo)) + snapshot_index).cast("int"),
    )
    region = F.element_at(
        F.array(*[F.lit(r) for r in regions]),
        ((F.col("id") % F.lit(4)) + 1).cast("int"),
    )
    category = F.element_at(
        F.array(*[F.lit(c) for c in categories]),
        (((F.col("id") / F.lit(3)).cast("int") % F.lit(4)) + 1).cast("int"),
    )

    dataframe = (
        spark.range(start_id, start_id + row_count)
        .select(F.col("id").cast("bigint").alias("id"))
        .withColumn("user_id", (F.col("id") % F.lit(97)).cast("int"))
        .withColumn("region", region)
        .withColumn("category", category)
        .withColumn("event_date", event_date)
        .withColumn("event_hour", (F.col("id") % F.lit(24)).cast("int"))
        .withColumn(
            "event_ts",
            F.to_timestamp(
                F.concat_ws(
                    " ",
                    F.col("event_date"),
                    F.format_string("%02d:00:00", F.col("event_hour")),
                )
            ),
        )
        .withColumn("amount", (F.col("id") % F.lit(10_000)).cast("double") / F.lit(100.0))
        .withColumn("batch_id", F.lit(snapshot_index).cast("int"))
    )

    if not nested:
        return dataframe

    devices = ("ios", "android", "web", "server")
    browsers = ("safari", "chrome", "firefox", "edge")
    return (
        dataframe.withColumn(
            "payload",
            F.struct(
                F.element_at(
                    F.array(*[F.lit(d) for d in devices]),
                    ((F.col("id") % F.lit(4)) + 1).cast("int"),
                ).alias("device"),
                F.element_at(
                    F.array(*[F.lit(b) for b in browsers]),
                    ((F.col("id") % F.lit(4)) + 1).cast("int"),
                ).alias("browser"),
                ((F.col("id") % F.lit(100)).cast("double") / F.lit(10.0)).alias("score"),
            ),
        )
        .withColumn(
            "tags",
            F.array(
                F.concat(F.lit("tag_"), (F.col("id") % F.lit(5)).cast("string")),
                F.concat(F.lit("batch_"), F.col("batch_id").cast("string")),
            ),
        )
        .withColumn(
            "attributes",
            F.create_map(
                F.lit("tenant"),
                F.concat(F.lit("tenant_"), (F.col("id") % F.lit(7)).cast("string")),
                F.lit("region"),
                F.col("region"),
            ),
        )
    )


def append_snapshots(
    spark: SparkSession,
    full_name: str,
    *,
    start_id: int,
    snapshots: int,
    rows_per_snapshot: int,
    files_per_snapshot: int,
    nested: bool,
) -> int:
    next_id = start_id
    for snapshot_index in range(snapshots):
        dataframe = generate_load_rows(
            spark,
            start_id=next_id,
            row_count=rows_per_snapshot,
            snapshot_index=snapshot_index,
            nested=nested,
        )
        (
            dataframe.repartition(files_per_snapshot)
            .writeTo(full_name)
            .option("write-format", "parquet")
            .append()
        )
        next_id += rows_per_snapshot
        print(
            f"  snapshot {snapshot_index + 1}/{snapshots}: "
            f"rows [{next_id - rows_per_snapshot}, {next_id}) "
            f"via {files_per_snapshot} files"
        )
    return next_id


def table_metrics(spark: SparkSession, full_name: str) -> dict[str, int]:
    files_row = spark.sql(
        f"""
        SELECT
            count(*) AS file_count,
            coalesce(sum(record_count), 0) AS record_count,
            coalesce(sum(file_size_in_bytes), 0) AS total_bytes
        FROM {full_name}.files
        """
    ).first()
    snapshot_count = int(
        spark.sql(f"SELECT count(*) AS snapshot_count FROM {full_name}.snapshots")
        .first()
        .snapshot_count
    )
    row_count = int(spark.table(full_name).count())
    partition_count = 0
    try:
        partition_count = int(
            spark.sql(f"SELECT count(*) AS partition_count FROM {full_name}.partitions")
            .first()
            .partition_count
        )
    except Exception:
        partition_count = 0

    return {
        "file_count": int(files_row.file_count),
        "record_count_from_files": int(files_row.record_count),
        "row_count": row_count,
        "total_bytes": int(files_row.total_bytes),
        "snapshot_count": snapshot_count,
        "partition_count": partition_count,
    }


def print_plan(args: argparse.Namespace, full_name: str, schema_sql: str, partition_sql: str) -> None:
    print("Load test plan:")
    print(f"  catalog={args.catalog} table={full_name}")
    print(f"  mode={args.mode} partition={partition_sql or 'unpartitioned'}")
    print(
        f"  snapshots={args.snapshots} rows_per_snapshot={args.rows_per_snapshot:,} "
        f"files_per_snapshot={args.files_per_snapshot}"
    )
    print(f"  total_new_rows={args.snapshots * args.rows_per_snapshot:,}")
    if args.id_offset is not None:
        print(f"  id_offset={args.id_offset}")
    if args.drop_existing:
        print("  drop_existing=yes")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    schema_sql, partition_sql, nested = resolve_schema(args)
    full_name = full_table_name(args.catalog, args.namespace, args.table)

    print_plan(args, full_name, schema_sql, partition_sql)
    if args.dry_run:
        print("Dry run — no writes performed.")
        return

    spark = create_spark_session(args.catalog, "load-test-data")
    configure_fragmentation(
        spark,
        files_per_snapshot=args.files_per_snapshot,
        max_records_per_file=args.max_records_per_file,
    )

    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {catalog_name(args.catalog)}.{args.namespace}")

    existed_before = table_exists(spark, full_name)
    if args.mode == "create" and existed_before and not args.drop_existing:
        raise SystemExit(f"Table already exists: {full_name} (use --drop-existing --force to recreate)")
    if args.mode == "append" and not existed_before:
        raise SystemExit(f"Table does not exist: {full_name} (use --mode create-or-append)")

    should_create = args.mode == "create" or (args.mode == "create-or-append" and not existed_before)
    if should_create:
        if args.drop_existing and existed_before:
            spark.sql(f"DROP TABLE IF EXISTS {full_name}")
            existed_before = False
        create_table(
            spark,
            full_name,
            schema_sql=schema_sql,
            partition_sql=partition_sql,
            target_file_size_bytes=args.target_file_size_bytes,
        )
        print(f"Created table {full_name}")

    if args.snapshots <= 0:
        print("snapshots=0 — skipping append.")
        if not should_create and args.mode != "append":
            metrics = table_metrics(spark, full_name)
            print(
                f"\n{full_name}: rows={metrics['row_count']:,}, files={metrics['file_count']:,}, "
                f"snapshots={metrics['snapshot_count']:,}, partitions={metrics['partition_count']:,}, "
                f"bytes={metrics['total_bytes']:,}"
            )
        spark.stop()
        return

    start_id = args.id_offset
    if start_id is None:
        start_id = next_id_offset(spark, full_name) if existed_before else 0

    print(f"Appending from id={start_id} ...")
    append_snapshots(
        spark,
        full_name,
        start_id=start_id,
        snapshots=args.snapshots,
        rows_per_snapshot=args.rows_per_snapshot,
        files_per_snapshot=args.files_per_snapshot,
        nested=nested,
    )

    metrics = table_metrics(spark, full_name)
    print(
        f"\n{full_name}: rows={metrics['row_count']:,}, files={metrics['file_count']:,}, "
        f"snapshots={metrics['snapshot_count']:,}, partitions={metrics['partition_count']:,}, "
        f"bytes={metrics['total_bytes']:,}"
    )
    spark.stop()


if __name__ == "__main__":
    main()
