#!/usr/bin/env python3
"""Generate HMS-backed Iceberg fixture tables with intentional maintenance issues.

Data is written only to MinIO (s3://warehouse/). Nothing is stored in the git repo.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType,
    DateType,
    DoubleType,
    IntegerType,
    LongType,
    MapType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spark_hms_session import CATALOG_NAME, create_spark  # noqa: E402

NAMESPACE = os.getenv("HMS_FIXTURE_NAMESPACE", "hms_issues")
DROP_EXISTING = os.getenv("HMS_FIXTURE_DROP_EXISTING", "1") == "1"
SCALE = os.getenv("HMS_FIXTURE_SCALE", "smoke").lower()

SCALE_PROFILES: dict[str, dict[str, int]] = {
    "smoke": {
        "balanced_control": 20_000,
        "unpartitioned_many_small_files": 15_000,
        "day_hot_partition_skew": 50_000,
        "daily_sparse_many_partitions": 30_000,
        "hourly_many_partitions": 40_000,
        "region_category_skew": 35_000,
        "month_transform_fragmented": 30_000,
        "year_transform_low_partition_count": 25_000,
        "oversized_single_partition": 60_000,
        "nested_payload_tiny_files": 20_000,
        "append_batches": 3,
        "files_per_batch": 2,
        "max_records_per_file": 16,
        "target_file_size_bytes": 131_072,
        "sparse_partition_modulo": 120,
    },
    "full": {
        "balanced_control": 1_500_000,
        "unpartitioned_many_small_files": 1_000_000,
        "day_hot_partition_skew": 5_000_000,
        "daily_sparse_many_partitions": 2_000_000,
        "hourly_many_partitions": 3_000_000,
        "region_category_skew": 4_000_000,
        "month_transform_fragmented": 3_000_000,
        "year_transform_low_partition_count": 2_000_000,
        "oversized_single_partition": 10_000_000,
        "nested_payload_tiny_files": 1_000_000,
        "append_batches": 12,
        "files_per_batch": 8,
        "max_records_per_file": 4,
        "target_file_size_bytes": 65_536,
        "sparse_partition_modulo": 3000,
    },
}

BASE_SCHEMA = StructType(
    [
        StructField("id", LongType(), False),
        StructField("user_id", IntegerType(), False),
        StructField("region", StringType(), False),
        StructField("category", StringType(), False),
        StructField("event_date", DateType(), False),
        StructField("event_hour", IntegerType(), False),
        StructField("event_ts", TimestampType(), False),
        StructField("amount", DoubleType(), False),
        StructField("batch_id", IntegerType(), False),
    ]
)

NESTED_SCHEMA = StructType(
    list(BASE_SCHEMA.fields)
    + [
        StructField(
            "payload",
            StructType(
                [
                    StructField("device", StringType(), False),
                    StructField("browser", StringType(), False),
                    StructField("score", DoubleType(), False),
                ]
            ),
            False,
        ),
        StructField("tags", ArrayType(StringType()), False),
        StructField("attributes", MapType(StringType(), StringType()), False),
    ]
)


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    row_count_key: str
    issue_profile: str
    partition_sql: str
    schema: StructType = BASE_SCHEMA
    dataframe_factory: Callable[[SparkSession, int], DataFrame] | None = None


def profile() -> dict[str, int]:
    if SCALE not in SCALE_PROFILES:
        raise ValueError(f"Unsupported HMS_FIXTURE_SCALE={SCALE!r}; use smoke or full")
    return SCALE_PROFILES[SCALE]


def full_name(table_name: str) -> str:
    return f"{CATALOG_NAME}.{NAMESPACE}.{table_name}"


def configure_fragmentation(spark: SparkSession, *, aggressive: bool) -> None:
    settings = profile()
    spark.conf.set("spark.sql.adaptive.enabled", "false")
    spark.conf.set("spark.sql.shuffle.partitions", str(settings["files_per_batch"]))
    if aggressive:
        spark.conf.set("spark.sql.files.maxRecordsPerFile", str(settings["max_records_per_file"]))


def base_rows(spark: SparkSession, row_count: int, *, skew_hot_day: bool = False) -> DataFrame:
    regions = ("us", "eu", "apac", "latam")
    categories = ("alpha", "beta", "gamma", "delta")
    hot_date = F.to_date(F.lit("2025-06-15"))
    sparse_date = F.date_add(
        F.to_date(F.lit("2020-01-01")),
        (F.col("id") % F.lit(profile()["sparse_partition_modulo"])).cast("int"),
    )
    event_date = (
        F.when(F.col("id") % F.lit(10) < F.lit(9), hot_date).otherwise(sparse_date)
        if skew_hot_day
        else sparse_date
    )
    region = (
        F.when(F.col("id") % F.lit(20) < F.lit(17), F.lit("us")).otherwise(
            F.element_at(
                F.array(*[F.lit(r) for r in regions]),
                ((F.col("id") % F.lit(4)) + 1).cast("int"),
            )
        )
        if skew_hot_day
        else F.element_at(
            F.array(*[F.lit(r) for r in regions]),
            ((F.col("id") % F.lit(4)) + 1).cast("int"),
        )
    )
    category = (
        F.when(F.col("id") % F.lit(25) < F.lit(22), F.lit("alpha")).otherwise(
            F.element_at(
                F.array(*[F.lit(c) for c in categories]),
                ((F.col("id") % F.lit(4)) + 1).cast("int"),
            )
        )
        if skew_hot_day
        else F.element_at(
            F.array(*[F.lit(c) for c in categories]),
            (((F.col("id") / F.lit(3)).cast("int") % F.lit(4)) + 1).cast("int"),
        )
    )
    return (
        spark.range(0, row_count)
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
        .withColumn("batch_id", (F.col("id") % F.lit(profile()["append_batches"])).cast("int"))
    )


def nested_rows(spark: SparkSession, row_count: int) -> DataFrame:
    devices = ("ios", "android", "web", "server")
    browsers = ("safari", "chrome", "firefox", "edge")
    return (
        base_rows(spark, row_count)
        .withColumn(
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
                F.concat(F.lit("batch_"), (F.col("batch_id") % F.lit(3)).cast("string")),
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


def oversized_rows(spark: SparkSession, row_count: int) -> DataFrame:
    return (
        spark.range(0, row_count)
        .select(F.col("id").cast("bigint").alias("id"))
        .withColumn("user_id", (F.col("id") % F.lit(97)).cast("int"))
        .withColumn("region", F.lit("us"))
        .withColumn("category", F.lit("alpha"))
        .withColumn("event_date", F.to_date(F.lit("2025-06-15")))
        .withColumn("event_hour", F.lit(0))
        .withColumn("event_ts", F.to_timestamp(F.lit("2025-06-15 00:00:00")))
        .withColumn("amount", (F.col("id") % F.lit(10_000)).cast("double") / F.lit(100.0))
        .withColumn("batch_id", (F.col("id") % F.lit(profile()["append_batches"])).cast("int"))
    )


FIXTURES: tuple[FixtureSpec, ...] = (
    FixtureSpec(
        "balanced_control",
        "balanced_control",
        "healthy baseline with moderate file count",
        "",
    ),
    FixtureSpec(
        "unpartitioned_many_small_files",
        "unpartitioned_many_small_files",
        "unpartitioned table with many tiny files",
        "",
    ),
    FixtureSpec(
        "day_hot_partition_skew",
        "day_hot_partition_skew",
        "day partition with 90% rows in one hot day",
        "days(event_date)",
        dataframe_factory=lambda spark, count: base_rows(spark, count, skew_hot_day=True),
    ),
    FixtureSpec(
        "daily_sparse_many_partitions",
        "daily_sparse_many_partitions",
        "day partition spread across thousands of dates",
        "days(event_date)",
    ),
    FixtureSpec(
        "hourly_many_partitions",
        "hourly_many_partitions",
        "multi-column partition with many day/hour combinations",
        "days(event_date), event_hour",
    ),
    FixtureSpec(
        "region_category_skew",
        "region_category_skew",
        "multi-column partition with dominant region/category pair",
        "region, category, days(event_date)",
        dataframe_factory=lambda spark, count: base_rows(spark, count, skew_hot_day=True),
    ),
    FixtureSpec(
        "month_transform_fragmented",
        "month_transform_fragmented",
        "month transform with fragmented small files",
        "months(event_date)",
    ),
    FixtureSpec(
        "year_transform_low_partition_count",
        "year_transform_low_partition_count",
        "year transform with fewer large partitions",
        "years(event_date)",
    ),
    FixtureSpec(
        "oversized_single_partition",
        "oversized_single_partition",
        "single dominant partition for planner oversize-alone behavior",
        "days(event_date)",
        dataframe_factory=lambda spark, count: oversized_rows(spark, count),
    ),
    FixtureSpec(
        "nested_payload_tiny_files",
        "nested_payload_tiny_files",
        "nested columns with fragmented tiny files",
        "region, days(event_ts)",
        schema=NESTED_SCHEMA,
        dataframe_factory=lambda spark, count: nested_rows(spark, count),
    ),
)


def schema_sql(schema: StructType) -> str:
    type_map = {
        LongType: "BIGINT",
        IntegerType: "INT",
        StringType: "STRING",
        DateType: "DATE",
        DoubleType: "DOUBLE",
        TimestampType: "TIMESTAMP",
        ArrayType: "ARRAY<STRING>",
        MapType: "MAP<STRING, STRING>",
        StructType: "STRUCT<device: STRING, browser: STRING, score: DOUBLE>",
    }
    parts: list[str] = []
    for field in schema.fields:
        spark_type = type(field.dataType)
        sql_type = type_map.get(spark_type)
        if sql_type is None:
            raise TypeError(f"Unsupported Spark type for fixture schema: {field.dataType}")
        parts.append(f"{field.name} {sql_type}")
    return ",\n            ".join(parts)


def create_table(spark: SparkSession, spec: FixtureSpec) -> None:
    table = full_name(spec.name)
    if DROP_EXISTING:
        spark.sql(f"DROP TABLE IF EXISTS {table}")
    partition_clause = (
        f"\n        PARTITIONED BY ({spec.partition_sql})" if spec.partition_sql else ""
    )
    settings = profile()
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            {schema_sql(spec.schema)}
        )
        USING iceberg{partition_clause}
        TBLPROPERTIES (
            'format-version' = '2',
            'write.distribution-mode' = 'none',
            'write.target-file-size-bytes' = '{settings["target_file_size_bytes"]}'
        )
        """
    )


def append_fragmented(spark: SparkSession, spec: FixtureSpec, dataframe: DataFrame) -> None:
    settings = profile()
    table = full_name(spec.name)
    aggressive = spec.name != "balanced_control"
    configure_fragmentation(spark, aggressive=aggressive)
    high_partition_table = spec.name in {
        "daily_sparse_many_partitions",
        "hourly_many_partitions",
    }
    for batch_id in range(settings["append_batches"]):
        batch_df = dataframe.where(F.col("batch_id") == batch_id)
        if spec.name == "balanced_control":
            batch_df.writeTo(table).append()
        elif high_partition_table:
            batch_df.coalesce(settings["files_per_batch"]).writeTo(table).append()
        else:
            (
                batch_df.repartition(settings["files_per_batch"])
                .writeTo(table)
                .option("write-format", "parquet")
                .append()
            )


def table_metrics(spark: SparkSession, table_name: str) -> tuple[int, int, int, int]:
    table = full_name(table_name)
    row = spark.sql(
        f"""
        SELECT
            count(*) AS file_count,
            coalesce(sum(record_count), 0) AS record_count,
            coalesce(sum(file_size_in_bytes), 0) AS total_bytes
        FROM {table}.files
        """
    ).first()
    partition_count = 0
    try:
        partition_count = int(
            spark.sql(f"SELECT count(*) AS partition_count FROM {table}.partitions")
            .first()
            .partition_count
        )
    except Exception:
        partition_count = 0
    rows = spark.table(table).count()
    return (
        int(row.file_count),
        int(rows),
        partition_count,
        int(row.total_bytes),
    )


def main() -> None:
    settings = profile()
    spark = create_spark("generate-hms-fixtures")
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {CATALOG_NAME}.{NAMESPACE}")

    print(
        f"Generating HMS fixtures (namespace={NAMESPACE}, scale={SCALE}, "
        f"batches={settings['append_batches']}, files_per_batch={settings['files_per_batch']})"
    )

    for spec in FIXTURES:
        row_count = settings[spec.row_count_key]
        create_table(spark, spec)
        factory = spec.dataframe_factory or (lambda s, count: base_rows(s, count))
        dataframe = factory(spark, row_count)
        append_fragmented(spark, spec, dataframe)
        file_count, rows, partitions, total_bytes = table_metrics(spark, spec.name)
        print(
            f"{full_name(spec.name)}: rows={rows}, files={file_count}, "
            f"partitions={partitions}, bytes={total_bytes}, issue={spec.issue_profile}"
        )

    print("\nTables in namespace:")
    spark.sql(f"SHOW TABLES IN {CATALOG_NAME}.{NAMESPACE}").show(truncate=False)
    spark.stop()


if __name__ == "__main__":
    main()
