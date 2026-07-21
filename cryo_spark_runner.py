#!/usr/bin/env python3
"""PySpark entrypoint for Cryo maintenance jobs.

Runs identically under EMR Serverless, the local docker-compose Spark container,
and Cryo's self-managed Spark-on-Kubernetes dispatch (cryo/engines/spark/k8s.py) --
none of those callers are baked in here, only how to reach an Iceberg catalog and
run one SQL statement against it.

Usage:
    spark-submit cryo_spark_runner.py --sql "CALL catalog.system.rewrite_data_files(...)" \
        --catalog cdp --catalog-impl rest --catalog-uri http://iceberg-rest:8181 \
        --catalog-warehouse s3://warehouse/

Catalog auth/S3 credentials are read from the environment (AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY, standard for S3A/S3FileIO) rather than CLI args, so they
never show up in `ps`/K8s pod specs/job event logs.
"""
from __future__ import annotations

import argparse
import sys
from typing import NoReturn

from pyspark.sql import SparkSession

_CATALOG_IMPL_SHORTHAND = {
    "rest": "org.apache.iceberg.rest.RESTCatalog",
    "hive": "org.apache.iceberg.hive.HiveCatalog",
    "glue": "org.apache.iceberg.aws.glue.GlueCatalog",
    "jdbc": "org.apache.iceberg.jdbc.JdbcCatalog",
}


def _resolve_catalog_impl(value: str) -> str:
    return _CATALOG_IMPL_SHORTHAND.get(value.strip().lower(), value)


def _repair_missing_files(spark: SparkSession, table_ref: str, *, dry_run: bool) -> int:
    """Remove manifest entries whose data file no longer exists in storage.

    rewrite_manifests does NOT do this -- it only reorganizes manifest pointer files,
    it never verifies the files they point at still exist (confirmed live,
    2026-07-21: a table with dangling references still broke compute_table_stats and
    rewrite_data_files right after a successful rewrite_manifests run). This lists every
    file the current snapshot references via Iceberg's own `<table>.files` metadata table
    (cheap, metadata-only -- no data is read), checks each against S3 with a HEAD request,
    and for confirmed-missing paths only, commits a DeleteFiles transaction that drops
    just those entries. Never touches a path it hasn't itself verified is gone.
    """
    import boto3
    from urllib.parse import urlparse

    print(f"[cryo] Listing current data files for {table_ref} via metadata table...")
    paths = [row["file_path"] for row in spark.sql(f"SELECT file_path FROM {table_ref}.files").collect()]
    print(f"[cryo] {len(paths)} data files in current snapshot")

    s3 = boto3.client("s3")
    missing: list[str] = []
    for path in paths:
        parsed = urlparse(path.replace("s3a://", "s3://").replace("s3n://", "s3://"))
        bucket, key = parsed.netloc, parsed.path.lstrip("/")
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except s3.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchKey"):
                missing.append(path)
            else:
                raise

    print(f"[cryo] {len(missing)} of {len(paths)} files are missing from storage")
    for path in missing:
        print(f"[cryo]   missing: {path}")

    if not missing:
        print("[cryo] Nothing to repair.")
        return 0

    table = spark._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable(
        spark._jsparkSession, table_ref
    )

    # deleteFile(CharSequence path) matches by exact string equality against the path
    # stored on the DataFile in the manifest. This looked right (paths came straight from
    # the table's own `.files` metadata table) but confirmed live, 2026-07-21: two commits
    # with deleteFile(path) -- one with validateFilesExist() -- both committed a new
    # "delete" snapshot with zero actual effect (summary total-data-files unchanged,
    # deleted-data-files/added-data-files both null) and neither raised. validateFilesExist()
    # evidently does not catch this case. Scanning for the live DataFile objects and passing
    # the object itself to deleteFile(DataFile) instead sidesteps string matching entirely --
    # it deletes the exact manifest entry Iceberg already has a reference to.
    missing_set = set(missing)
    matched_files = []
    scan = table.newScan()
    tasks = scan.planFiles()
    iterator = tasks.iterator()
    try:
        while iterator.hasNext():
            data_file = iterator.next().file()
            if str(data_file.path()) in missing_set:
                matched_files.append(data_file)
    finally:
        tasks.close()

    matched_paths = {str(f.path()) for f in matched_files}
    print(f"[cryo] Matched {len(matched_files)} of {len(missing)} missing paths to live DataFile objects via table scan")
    for path in missing_set - matched_paths:
        print(f"[cryo]   unmatched (not found in current scan): {path!r}")

    if not matched_files:
        print("[cryo] No matching DataFile objects found -- nothing to delete.")
        return 0

    if dry_run:
        print(f"[cryo] Dry run -- not committing. Re-run with --commit to remove {len(matched_files)} entries.")
        return 0

    delete = table.newDelete()
    for data_file in matched_files:
        delete.deleteFile(data_file)
    delete.validateFilesExist()
    delete.commit()
    print(f"[cryo] Committed removal of {len(matched_files)} missing file entries from {table_ref}")
    return 0


def main() -> int | NoReturn:
    parser = argparse.ArgumentParser(description="Execute Cryo maintenance SQL via Spark")
    parser.add_argument("--sql", default=None, help="SQL statement(s) to execute, ';\\n'-joined")
    parser.add_argument(
        "--repair-missing-files",
        action="store_true",
        help="Remove manifest entries for data files confirmed missing from storage",
    )
    parser.add_argument(
        "--table",
        default=None,
        help="Full catalog.namespace.table reference (--repair-missing-files mode)",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually commit the repair (default is dry-run: list missing files only)",
    )
    parser.add_argument("--catalog", default="glue_catalog", help="Iceberg catalog name in SQL")
    parser.add_argument(
        "--catalog-impl",
        default="glue",
        help="Catalog implementation: rest, hive, glue, jdbc, or a full class name",
    )
    parser.add_argument("--catalog-uri", default=None, help="REST/Hive/JDBC catalog URI")
    parser.add_argument("--catalog-warehouse", default=None, help="Warehouse location")
    parser.add_argument("--s3-endpoint", default=None, help="S3-compatible endpoint (MinIO, etc.)")
    args = parser.parse_args()

    if args.repair_missing_files:
        if not args.table:
            parser.error("--table is required with --repair-missing-files")
    elif not args.sql:
        parser.error("--sql is required unless --repair-missing-files is set")

    builder = (
        SparkSession.builder.appName("cryo-maintenance")
        .config(f"spark.sql.catalog.{args.catalog}", "org.apache.iceberg.spark.SparkCatalog")
        .config(
            f"spark.sql.catalog.{args.catalog}.catalog-impl",
            _resolve_catalog_impl(args.catalog_impl),
        )
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
    )
    if args.catalog_uri:
        builder = builder.config(f"spark.sql.catalog.{args.catalog}.uri", args.catalog_uri)
    if args.catalog_warehouse:
        builder = builder.config(
            f"spark.sql.catalog.{args.catalog}.warehouse", args.catalog_warehouse
        )
        if args.catalog_warehouse.startswith("s3://") or args.catalog_warehouse.startswith(
            "s3a://"
        ):
            # Iceberg's own S3FileIO (from iceberg-aws-bundle, already on the classpath) --
            # not Hadoop's S3AFileSystem, which needs the separate hadoop-aws jar we don't
            # ship. HiveCatalog defaults to Hadoop's FileIO unless told otherwise, which
            # throws ClassNotFoundException: S3AFileSystem (confirmed live, 2026-07-21).
            builder = builder.config(
                f"spark.sql.catalog.{args.catalog}.io-impl", "org.apache.iceberg.aws.s3.S3FileIO"
            )
    if args.s3_endpoint:
        builder = builder.config(
            f"spark.sql.catalog.{args.catalog}.s3.endpoint", args.s3_endpoint
        )
    # Hive metastore catalogs need Hive support enabled; harmless no-op for REST/Glue/JDBC.
    if _resolve_catalog_impl(args.catalog_impl) == _CATALOG_IMPL_SHORTHAND["hive"]:
        builder = builder.enableHiveSupport()

    spark = builder.getOrCreate()

    try:
        if args.repair_missing_files:
            return _repair_missing_files(spark, args.table, dry_run=not args.commit)

        exit_code = 0
        for statement in args.sql.split(";\n"):
            statement = statement.strip()
            if not statement:
                continue
            print(f"[cryo] Executing SQL: {statement[:200]}{'...' if len(statement) > 200 else ''}")
            result = spark.sql(statement)
            if result is not None:
                row_count = result.count()
                print(f"[cryo] SQL executed successfully, rows affected/returned: {row_count}")
                if 0 < row_count <= 100:
                    result.show(truncate=False)
            else:
                print("[cryo] SQL executed successfully (no result set)")
        return exit_code
    except Exception as exc:
        print(f"[cryo] SQL execution failed: {exc}", file=sys.stderr)
        raise
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
