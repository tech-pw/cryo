#!/usr/bin/env python3
"""Create partition-rich, intentionally fragmented Iceberg fixture tables."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from pyspark.sql import DataFrame, Row, SparkSession
from pyspark.sql import functions as F

# Allow running as `python scripts/create_partition_fixture_tables.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spark_session import CATALOG_NAME, create_spark  # noqa: E402

NAMESPACE = os.getenv("FIXTURE_NAMESPACE", "fixtures")
BATCHES = int(os.getenv("FIXTURE_BATCHES", "12"))
FILES_PER_BATCH = int(os.getenv("FIXTURE_FILES_PER_BATCH", "8"))
ROWS_PER_BATCH = int(os.getenv("FIXTURE_ROWS_PER_BATCH", "64"))
DROP_EXISTING = os.getenv("FIXTURE_DROP_EXISTING", "1") == "1"
TARGET_FILE_SIZE_BYTES = os.getenv("FIXTURE_TARGET_FILE_SIZE_BYTES", "131072")
MAX_RECORDS_PER_FILE = os.getenv("FIXTURE_MAX_RECORDS_PER_FILE", "4")

BASE_SCHEMA_SQL = """
    id BIGINT,
    user_id INT,
    region STRING,
    category STRING,
    event_date DATE,
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


@dataclass(frozen=True)
class FixtureTable:
    name: str
    schema_sql: str
    partition_sql: str
    dataframe_factory: Callable[[SparkSession], DataFrame]


def full_table_name(table_name: str) -> str:
    return f"{CATALOG_NAME}.{NAMESPACE}.{table_name}"


def build_base_rows(include_nested: bool = False) -> list[Row]:
    regions = ("us", "eu", "apac", "latam")
    categories = ("alpha", "beta", "gamma", "delta")
    devices = ("ios", "android", "web", "server")
    browsers = ("safari", "chrome", "firefox", "edge")
    start = datetime(2025, 1, 1, 0, 0, 0)
    rows: list[Row] = []

    for batch_id in range(BATCHES):
        for offset in range(ROWS_PER_BATCH):
            row_id = batch_id * ROWS_PER_BATCH + offset
            event_ts = start + timedelta(
                days=row_id % 420,
                hours=row_id % 24,
                minutes=row_id % 60,
            )
            base = {
                "id": row_id,
                "user_id": row_id % 97,
                "region": regions[row_id % len(regions)],
                "category": categories[(row_id // 3) % len(categories)],
                "event_date": event_ts.date(),
                "event_ts": event_ts,
                "amount": float((row_id % 10_000) / 100),
                "batch_id": batch_id,
            }

            if include_nested:
                base.update(
                    {
                        "payload": Row(
                            device=devices[row_id % len(devices)],
                            browser=browsers[row_id % len(browsers)],
                            score=float((row_id % 100) / 10),
                        ),
                        "tags": [f"tag_{row_id % 5}", f"batch_{batch_id % 3}"],
                        "attributes": {
                            "tenant": f"tenant_{row_id % 7}",
                            "region": regions[row_id % len(regions)],
                        },
                    }
                )

            rows.append(Row(**base))

    return rows


def base_dataframe(spark: SparkSession) -> DataFrame:
    return spark.createDataFrame(build_base_rows())


def nested_dataframe(spark: SparkSession) -> DataFrame:
    return spark.createDataFrame(build_base_rows(include_nested=True))


FIXTURE_TABLES = (
    FixtureTable("fixture_unpartitioned", BASE_SCHEMA_SQL, "", base_dataframe),
    FixtureTable("fixture_day", BASE_SCHEMA_SQL, "days(event_date)", base_dataframe),
    FixtureTable("fixture_month", BASE_SCHEMA_SQL, "months(event_date)", base_dataframe),
    FixtureTable("fixture_year", BASE_SCHEMA_SQL, "years(event_date)", base_dataframe),
    FixtureTable("fixture_bucket", BASE_SCHEMA_SQL, "bucket(8, user_id)", base_dataframe),
    FixtureTable(
        "fixture_multi_column",
        BASE_SCHEMA_SQL,
        "region, category, days(event_date)",
        base_dataframe,
    ),
    FixtureTable("fixture_datetime", BASE_SCHEMA_SQL, "days(event_ts)", base_dataframe),
    FixtureTable(
        "fixture_nested_object",
        NESTED_SCHEMA_SQL,
        "region, days(event_ts)",
        nested_dataframe,
    ),
)


def configure_for_fragmentation(spark: SparkSession) -> None:
    spark.conf.set("spark.sql.adaptive.enabled", "false")
    spark.conf.set("spark.sql.shuffle.partitions", str(FILES_PER_BATCH))
    spark.conf.set("spark.sql.files.maxRecordsPerFile", MAX_RECORDS_PER_FILE)


def create_empty_table(spark: SparkSession, fixture: FixtureTable) -> None:
    full_name = full_table_name(fixture.name)
    if DROP_EXISTING:
        spark.sql(f"DROP TABLE IF EXISTS {full_name}")

    partition_clause = (
        f"\nPARTITIONED BY ({fixture.partition_sql})" if fixture.partition_sql else ""
    )
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {full_name} (
            {fixture.schema_sql}
        )
        USING iceberg{partition_clause}
        TBLPROPERTIES (
            'format-version' = '2',
            'write.distribution-mode' = 'none',
            'write.target-file-size-bytes' = '{TARGET_FILE_SIZE_BYTES}'
        )
        """
    )


def append_fragmented_batches(
    spark: SparkSession,
    fixture: FixtureTable,
    dataframe: DataFrame,
) -> None:
    full_name = full_table_name(fixture.name)
    for batch_id in range(BATCHES):
        (
            dataframe.where(F.col("batch_id") == batch_id)
            .repartition(FILES_PER_BATCH)
            .writeTo(full_name)
            .append()
        )


def count_files(spark: SparkSession, table_name: str) -> tuple[int, int, int]:
    full_name = full_table_name(table_name)
    row = spark.sql(
        f"""
        SELECT
            count(*) AS file_count,
            coalesce(sum(record_count), 0) AS record_count,
            coalesce(sum(file_size_in_bytes), 0) AS total_bytes
        FROM {full_name}.files
        """
    ).first()
    return int(row.file_count), int(row.record_count), int(row.total_bytes)


def main() -> None:
    spark = create_spark("create-partition-fixtures")
    configure_for_fragmentation(spark)
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {CATALOG_NAME}.{NAMESPACE}")

    print(
        "Creating fragmented Iceberg fixtures "
        f"(namespace={NAMESPACE}, batches={BATCHES}, files_per_batch={FILES_PER_BATCH})"
    )

    for fixture in FIXTURE_TABLES:
        create_empty_table(spark, fixture)
        dataframe = fixture.dataframe_factory(spark)
        append_fragmented_batches(spark, fixture, dataframe)
        file_count, record_count, total_bytes = count_files(spark, fixture.name)
        partition_label = fixture.partition_sql or "unpartitioned"
        print(
            f"{full_table_name(fixture.name)}: "
            f"{file_count} files, {record_count} records, {total_bytes} bytes, "
            f"partition={partition_label}"
        )

    spark.stop()


if __name__ == "__main__":
    main()
