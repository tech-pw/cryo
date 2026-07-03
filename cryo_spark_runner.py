#!/usr/bin/env python3
"""PySpark entrypoint for Cryo maintenance jobs on EMR Serverless.

This script is uploaded to S3 and executed by EMR Serverless. It receives
the maintenance SQL statement via command line and executes it using Spark SQL.

Usage:
    spark-submit cryo_spark_runner.py --sql "CALL catalog.system.rewrite_data_files(...)"
"""
from __future__ import annotations

import argparse
import sys
from typing import NoReturn

from pyspark.sql import SparkSession


def main() -> int | NoReturn:
    parser = argparse.ArgumentParser(description="Execute Cryo maintenance SQL via Spark")
    parser.add_argument("--sql", required=True, help="SQL statement to execute")
    parser.add_argument("--catalog", default="glue_catalog", help="Iceberg catalog name")
    args = parser.parse_args()

    spark = (
        SparkSession.builder
        .appName("cryo-maintenance")
        .config(f"spark.sql.catalog.{args.catalog}", "org.apache.iceberg.spark.SparkCatalog")
        .config(f"spark.sql.catalog.{args.catalog}.catalog-impl", "org.apache.iceberg.aws.glue.GlueCatalog")
        .config("spark.sql.extensions", "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions")
        .enableHiveSupport()
        .getOrCreate()
    )

    try:
        print(f"[cryo] Executing SQL: {args.sql[:200]}{'...' if len(args.sql) > 200 else ''}")
        result = spark.sql(args.sql)
        if result is not None:
            row_count = result.count()
            print(f"[cryo] SQL executed successfully, rows affected/returned: {row_count}")
            if row_count > 0 and row_count <= 100:
                result.show(truncate=False)
        else:
            print("[cryo] SQL executed successfully (no result set)")
        return 0
    except Exception as exc:
        print(f"[cryo] SQL execution failed: {exc}", file=sys.stderr)
        raise
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
