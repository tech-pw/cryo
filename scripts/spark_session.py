"""Shared Spark session configuration for the local Iceberg stack."""

from __future__ import annotations

import os

from pyspark.sql import SparkSession

ICEBERG_VERSION = os.getenv("ICEBERG_VERSION", "1.6.1")
CATALOG_NAME = os.getenv("ICEBERG_CATALOG", "rest")
REST_URI = os.getenv("ICEBERG_REST_URI", "http://localhost:8181")
WAREHOUSE = os.getenv("ICEBERG_WAREHOUSE", "s3://warehouse/")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://localhost:9000")
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID", "minioadmin")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

SPARK_PACKAGES = ",".join(
    [
        f"org.apache.iceberg:iceberg-spark-runtime-3.5_2.12:{ICEBERG_VERSION}",
        f"org.apache.iceberg:iceberg-aws-bundle:{ICEBERG_VERSION}",
        "org.apache.hadoop:hadoop-aws:3.3.4",
        "com.amazonaws:aws-java-sdk-bundle:1.12.262",
    ]
)


def create_spark(app_name: str = "iceberg-local") -> SparkSession:
    """Create a Spark session wired to the local Iceberg REST catalog."""
    builder = (
        SparkSession.builder.appName(app_name)
        .config("spark.jars.packages", SPARK_PACKAGES)
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )
        .config(f"spark.sql.catalog.{CATALOG_NAME}", "org.apache.iceberg.spark.SparkCatalog")
        .config(f"spark.sql.catalog.{CATALOG_NAME}.type", "rest")
        .config(f"spark.sql.catalog.{CATALOG_NAME}.uri", REST_URI)
        .config(f"spark.sql.catalog.{CATALOG_NAME}.warehouse", WAREHOUSE)
        .config(
            f"spark.sql.catalog.{CATALOG_NAME}.io-impl",
            "org.apache.iceberg.aws.s3.S3FileIO",
        )
        .config(f"spark.sql.catalog.{CATALOG_NAME}.s3.endpoint", S3_ENDPOINT)
        .config(f"spark.sql.catalog.{CATALOG_NAME}.s3.path-style-access", "true")
        .config(f"spark.sql.catalog.{CATALOG_NAME}.s3.access-key-id", AWS_ACCESS_KEY)
        .config(f"spark.sql.catalog.{CATALOG_NAME}.s3.secret-access-key", AWS_SECRET_KEY)
        .config(f"spark.sql.catalog.{CATALOG_NAME}.client.region", AWS_REGION)
        .config("spark.sql.defaultCatalog", CATALOG_NAME)
        .config("spark.hadoop.fs.s3a.endpoint", S3_ENDPOINT)
        .config("spark.hadoop.fs.s3a.access.key", AWS_ACCESS_KEY)
        .config("spark.hadoop.fs.s3a.secret.key", AWS_SECRET_KEY)
        .config("spark.hadoop.fs.s3a.path.style.access", "true")
        .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
    )
    return builder.getOrCreate()
