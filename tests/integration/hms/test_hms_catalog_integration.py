"""Opt-in live tests for Cryo HMS catalog integration."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from cryo.config import Settings
from cryo.db.models import Source, Table, TablePartition, TableStats
from cryo.db.session import SessionLocal
from cryo.scanner.run import run_scan
from cryo.sources.factory import catalog_from_config

HMS_URI = os.getenv("CRYO_HMS_URI")
HMS_NAMESPACE = os.getenv("HMS_FIXTURE_NAMESPACE", "hms_issues")
HMS_WAREHOUSE = os.getenv("ICEBERG_WAREHOUSE", "s3://warehouse/")
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://localhost:9000")

EXPECTED_TABLES = {
    "balanced_control",
    "unpartitioned_many_small_files",
    "day_hot_partition_skew",
    "daily_sparse_many_partitions",
    "hourly_many_partitions",
    "region_category_skew",
    "month_transform_fragmented",
    "year_transform_low_partition_count",
    "oversized_single_partition",
    "nested_payload_tiny_files",
}

requires_hms = pytest.mark.skipif(not HMS_URI, reason="Set CRYO_HMS_URI for live HMS tests")


def hms_config() -> dict[str, object]:
    return {
        "catalog_name": "hms",
        "type": "hive",
        "uri": HMS_URI,
        "warehouse": HMS_WAREHOUSE,
        "s3.endpoint": S3_ENDPOINT,
        "s3.access-key-id": os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
        "s3.secret-access-key": os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin"),
        "s3.path-style-access": "true",
    }


def postgres_available() -> bool:
    try:
        with SessionLocal() as session:
            session.scalar(text("select 1"))
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not postgres_available(),
    reason="Local Postgres (cryo DB) required for scanner integration test",
)


@requires_hms
def test_hms_catalog_lists_fixture_tables() -> None:
    catalog = catalog_from_config("hms-test", "hive", hms_config())
    discovered = {
        table_name
        for schema_name, table_name in catalog.list_tables()
        if schema_name == HMS_NAMESPACE
    }
    missing = EXPECTED_TABLES - discovered
    assert not missing, f"Missing HMS fixture tables: {sorted(missing)}"


@requires_hms
def test_hms_catalog_loads_fragmented_table_metadata() -> None:
    catalog = catalog_from_config("hms-test", "hive", hms_config())
    table = catalog.load_table(HMS_NAMESPACE, "unpartitioned_many_small_files")
    scan = table.scan()
    arrow_table = scan.to_arrow()
    assert arrow_table.num_rows > 0

    files = list(table.scan().plan_files())
    assert len(files) > 1


@requires_hms
def test_hms_catalog_loads_partitioned_table() -> None:
    catalog = catalog_from_config("hms-test", "hive", hms_config())
    table = catalog.load_table(HMS_NAMESPACE, "day_hot_partition_skew")
    assert table.scan().to_arrow().num_rows > 0
    metadata = table.metadata
    assert metadata is not None
    assert len(metadata.partition_specs) >= 1


def _delete_hms_test_source(source_id) -> None:
    """Remove a transient HMS source and any scanner rows it created."""
    from sqlalchemy import delete

    from cryo.db.models import QueryLog, Table

    with SessionLocal() as session:
        table_ids = list(
            session.scalars(select(Table.id).where(Table.source_id == source_id)).all()
        )
        if table_ids:
            session.execute(delete(QueryLog).where(QueryLog.table_id.in_(table_ids)))
            for table_id in table_ids:
                table = session.get(Table, table_id)
                if table is not None:
                    session.delete(table)
        session.execute(delete(QueryLog).where(QueryLog.source_id == source_id))
        source = session.get(Source, source_id)
        if source is not None:
            session.delete(source)
        session.commit()


@requires_hms
@requires_postgres
def test_scanner_discovers_and_persists_hms_fixture_stats() -> None:
    source_name = f"test-hms-{uuid4()}"
    with SessionLocal() as session:
        source = Source(
            name=source_name,
            type="hive",
            config=hms_config(),
            enabled=True,
        )
        session.add(source)
        session.commit()
        source_id = source.id

    try:
        with SessionLocal() as session:
            summary = run_scan(
                session,
                settings=Settings(_env_file=None, scan_num_chunks=1),
                source_id=source_id,
                schema_names=[HMS_NAMESPACE],
                num_chunks=1,
                full_scan=True,
                parallel_tables=0,
            )
            assert summary.discovered >= len(EXPECTED_TABLES)
            assert summary.scanned >= 1
            assert summary.failed == 0

            tables = session.scalars(
                select(Table).where(
                    Table.source_id == source_id,
                    Table.schema_name == HMS_NAMESPACE,
                )
            ).all()
            table_names = {table.table_name for table in tables}
            assert EXPECTED_TABLES.issubset(table_names)

            fragmented = session.scalar(
                select(TableStats)
                .join(Table, Table.id == TableStats.table_id)
                .where(
                    Table.source_id == source_id,
                    Table.table_name == "unpartitioned_many_small_files",
                )
            )
            assert fragmented is not None
            assert fragmented.small_files_count > 0

            partitioned = session.scalar(
                select(Table).where(
                    Table.source_id == source_id,
                    Table.table_name == "day_hot_partition_skew",
                )
            )
            assert partitioned is not None
            partition_rows = session.scalars(
                select(TablePartition).where(TablePartition.table_id == partitioned.id)
            ).all()
            assert partition_rows
    finally:
        _delete_hms_test_source(source_id)
