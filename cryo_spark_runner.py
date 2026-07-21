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


def main() -> int | NoReturn:
    parser = argparse.ArgumentParser(description="Execute Cryo maintenance SQL via Spark")
    parser.add_argument("--sql", required=True, help="SQL statement(s) to execute, ';\\n'-joined")
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
