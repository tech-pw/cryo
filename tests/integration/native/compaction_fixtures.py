"""COW compaction fixture tables for native REST + MinIO e2e (Phase B)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pyarrow as pa
from pyiceberg.catalog import load_catalog
from pyiceberg.schema import Schema
from pyiceberg.transforms import DayTransform, IdentityTransform
from pyiceberg.types import DateType, LongType, NestedField, StringType

from tests.integration.native.compaction_oracles import capture_table_snapshot
from tests.integration.native.rest_env import (
    catalog_properties,
    normalize_uri,
    wait_for_rest_catalog,
)

COMPACTION_NAMESPACE = "native_compaction"
UNPARTITIONED_TABLE = "f_unpart_small"
PARTITIONED_TABLE = "f_part_small"
PARTITIONED_IDENTITY_TABLE = "f_part_identity"
# High row-density single partition (GeoIP-shaped): many small, highly compressible
# files so Arrow expansion dwarfs on-disk size. Used by P4 RSS + docker memory tests.
DENSE_PARTITION_TABLE = "f_part_dense"
# GB-scale dense fixture for local-MinIO throughput benchmarking (Phase 0).
# Sized via CRYO_PERF_FIXTURE_TARGET_MB (default 2048). Not seeded by the
# default ensure_compaction_fixtures() path — use ensure_perf_compaction_fixture().
PERF_DENSE_PARTITION_TABLE = "f_part_dense_perf"
MOR_POS_TABLE = "f_mor_pos"
MOR_EQ_TABLE = "f_mor_eq"
MOR_MIXED_TABLE = "f_mor_mixed"
MOR_PARTIAL_TABLE = "f_mor_partial"
TINY_APPEND_COUNT = 16
# Dense fixture: enough files that a 256 MB bin-pack group would otherwise hold
# them all; byte-cap splitting must break them into many rewrite groups.
DENSE_FILE_COUNT = 12
DENSE_ROWS_PER_FILE = 20_000


@dataclass(frozen=True)
class CompactionFixture:
    name: str
    table_ref: str
    location: str
    initial_snapshot: object


def ensure_compaction_fixtures() -> dict[str, CompactionFixture]:
    wait_for_rest_catalog()
    catalog = load_catalog("rest", **catalog_properties())
    namespace = (COMPACTION_NAMESPACE,)
    if namespace not in catalog.list_namespaces():
        catalog.create_namespace(COMPACTION_NAMESPACE)

    fixtures = {
        UNPARTITIONED_TABLE: _ensure_unpartitioned_small(catalog),
        PARTITIONED_TABLE: _ensure_partitioned_small(catalog),
        PARTITIONED_IDENTITY_TABLE: _ensure_partitioned_identity(catalog),
        DENSE_PARTITION_TABLE: _ensure_dense_partition(catalog),
    }
    return fixtures


def _drop_if_exists(catalog: object, identifier: tuple[str, str]) -> None:
    try:
        catalog.drop_table(identifier)  # type: ignore[attr-defined]
    except Exception:
        pass


def _ensure_unpartitioned_small(catalog: object) -> CompactionFixture:
    identifier = (COMPACTION_NAMESPACE, UNPARTITIONED_TABLE)
    _drop_if_exists(catalog, identifier)

    schema = Schema(
        NestedField(1, "id", LongType(), required=True),
        NestedField(2, "label", StringType(), required=True),
    )
    catalog.create_table(identifier, schema=schema)  # type: ignore[attr-defined]
    table = catalog.load_table(identifier)  # type: ignore[attr-defined]
    arrow_schema = pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("label", pa.string(), nullable=False),
        ]
    )

    for batch in range(TINY_APPEND_COUNT):
        arrow = pa.Table.from_arrays(
            [
                pa.array([batch], type=pa.int64()),
                pa.array([f"row-{batch}"], type=pa.string()),
            ],
            schema=arrow_schema,
        )
        table.append(arrow)
        table = catalog.load_table(identifier)  # type: ignore[attr-defined]

    snapshot = capture_table_snapshot(table)
    return CompactionFixture(
        name=UNPARTITIONED_TABLE,
        table_ref=f"{COMPACTION_NAMESPACE}.{UNPARTITIONED_TABLE}",
        location=normalize_uri(table.location()),
        initial_snapshot=snapshot,
    )


def _ensure_partitioned_small(catalog: object) -> CompactionFixture:
    identifier = (COMPACTION_NAMESPACE, PARTITIONED_TABLE)
    _drop_if_exists(catalog, identifier)

    schema = Schema(
        NestedField(1, "id", LongType(), required=True),
        NestedField(2, "event_date", DateType(), required=True),
        NestedField(3, "label", StringType(), required=True),
    )
    catalog.create_table(identifier, schema=schema)  # type: ignore[attr-defined]
    table = catalog.load_table(identifier)  # type: ignore[attr-defined]
    with table.update_spec() as update:
        update.add_field("event_date", DayTransform(), "event_date_day")
    table = catalog.load_table(identifier)  # type: ignore[attr-defined]

    arrow_schema = pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("event_date", pa.date32(), nullable=False),
            pa.field("label", pa.string(), nullable=False),
        ]
    )
    start = date(2025, 5, 1)
    row_id = 0
    for day_offset in range(4):
        event_date = start + timedelta(days=day_offset)
        for batch in range(4):
            arrow = pa.Table.from_arrays(
                [
                    pa.array([row_id], type=pa.int64()),
                    pa.array([event_date], type=pa.date32()),
                    pa.array([f"day-{day_offset}-row-{batch}"], type=pa.string()),
                ],
                schema=arrow_schema,
            )
            table.append(arrow)
            row_id += 1
            table = catalog.load_table(identifier)  # type: ignore[attr-defined]

    snapshot = capture_table_snapshot(table)
    return CompactionFixture(
        name=PARTITIONED_TABLE,
        table_ref=f"{COMPACTION_NAMESPACE}.{PARTITIONED_TABLE}",
        location=normalize_uri(table.location()),
        initial_snapshot=snapshot,
    )


def _ensure_partitioned_identity(catalog: object) -> CompactionFixture:
    identifier = (COMPACTION_NAMESPACE, PARTITIONED_IDENTITY_TABLE)
    _drop_if_exists(catalog, identifier)

    schema = Schema(
        NestedField(1, "id", LongType(), required=True),
        NestedField(2, "region", StringType(), required=True),
        NestedField(3, "label", StringType(), required=True),
    )
    catalog.create_table(identifier, schema=schema)  # type: ignore[attr-defined]
    table = catalog.load_table(identifier)  # type: ignore[attr-defined]
    with table.update_spec() as update:
        update.add_field("region", IdentityTransform(), "region")
    table = catalog.load_table(identifier)  # type: ignore[attr-defined]

    arrow_schema = pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("region", pa.string(), nullable=False),
            pa.field("label", pa.string(), nullable=False),
        ]
    )
    regions = ("us", "eu", "apac")
    row_id = 0
    for region in regions:
        for batch in range(4):
            arrow = pa.Table.from_arrays(
                [
                    pa.array([row_id], type=pa.int64()),
                    pa.array([region], type=pa.string()),
                    pa.array([f"{region}-row-{batch}"], type=pa.string()),
                ],
                schema=arrow_schema,
            )
            table.append(arrow)
            row_id += 1
            table = catalog.load_table(identifier)  # type: ignore[attr-defined]

    snapshot = capture_table_snapshot(table)
    return CompactionFixture(
        name=PARTITIONED_IDENTITY_TABLE,
        table_ref=f"{COMPACTION_NAMESPACE}.{PARTITIONED_IDENTITY_TABLE}",
        location=normalize_uri(table.location()),
        initial_snapshot=snapshot,
    )


def _ensure_dense_partition(catalog: object) -> CompactionFixture:
    """One partition, many highly compressible small files (GeoIP-shaped density).

    Repeated short strings compress tightly on disk but expand in Arrow — the
    failure mode behind run 858ad9eb. Byte-cap group splitting must keep each
    ExecuteCompactionGroup under a 1 GiB cgroup limit.
    """
    identifier = (COMPACTION_NAMESPACE, DENSE_PARTITION_TABLE)
    _drop_if_exists(catalog, identifier)

    schema = Schema(
        NestedField(1, "id", LongType(), required=True),
        NestedField(2, "ingest_date", StringType(), required=True),
        NestedField(3, "country", StringType(), required=True),
        NestedField(4, "city", StringType(), required=True),
        NestedField(5, "isp", StringType(), required=True),
    )
    catalog.create_table(identifier, schema=schema)  # type: ignore[attr-defined]
    table = catalog.load_table(identifier)  # type: ignore[attr-defined]
    with table.update_spec() as update:
        update.add_field("ingest_date", IdentityTransform(), "ingest_date")
    table = catalog.load_table(identifier)  # type: ignore[attr-defined]

    arrow_schema = pa.schema(
        [
            pa.field("id", pa.int64(), nullable=False),
            pa.field("ingest_date", pa.string(), nullable=False),
            pa.field("country", pa.string(), nullable=False),
            pa.field("city", pa.string(), nullable=False),
            pa.field("isp", pa.string(), nullable=False),
        ]
    )
    ingest_date = "2025-02-13"
    # Highly compressible: tiny cardinality across string columns.
    countries = ("IN",) * DENSE_ROWS_PER_FILE
    cities = ("delhi",) * DENSE_ROWS_PER_FILE
    isps = ("airtel",) * DENSE_ROWS_PER_FILE
    dates = (ingest_date,) * DENSE_ROWS_PER_FILE

    row_id = 0
    for _batch in range(DENSE_FILE_COUNT):
        ids = list(range(row_id, row_id + DENSE_ROWS_PER_FILE))
        arrow = pa.Table.from_arrays(
            [
                pa.array(ids, type=pa.int64()),
                pa.array(dates, type=pa.string()),
                pa.array(countries, type=pa.string()),
                pa.array(cities, type=pa.string()),
                pa.array(isps, type=pa.string()),
            ],
            schema=arrow_schema,
        )
        table.append(arrow)
        row_id += DENSE_ROWS_PER_FILE
        table = catalog.load_table(identifier)  # type: ignore[attr-defined]

    snapshot = capture_table_snapshot(table)
    return CompactionFixture(
        name=DENSE_PARTITION_TABLE,
        table_ref=f"{COMPACTION_NAMESPACE}.{DENSE_PARTITION_TABLE}",
        location=normalize_uri(table.location()),
        initial_snapshot=snapshot,
    )


def ensure_perf_compaction_fixture(
    *,
    target_mb: int | None = None,
    file_count: int | None = None,
    force: bool | None = None,
) -> CompactionFixture:
    """Create a ~2–3 GB dense, fragmented, GeoIP-shaped table on local MinIO.

    Uses many small compressible files so rewrite_data_files has real work to do
    without requiring WAN S3. Target size is approximate (highly compressible
    strings); override with CRYO_PERF_FIXTURE_TARGET_MB / CRYO_PERF_FIXTURE_FILES.
    Reuses an existing table unless force=True or CRYO_PERF_RESEED=1.
    """
    import os

    wait_for_rest_catalog()
    catalog = load_catalog("rest", **catalog_properties())
    namespace = (COMPACTION_NAMESPACE,)
    if namespace not in catalog.list_namespaces():
        catalog.create_namespace(COMPACTION_NAMESPACE)

    if force is None:
        force = os.getenv("CRYO_PERF_RESEED", "").strip() in {"1", "true", "yes"}
    if target_mb is None:
        # Default aims for ~2 GiB uncompressed Arrow-ish work; on-disk Parquet
        # is smaller. Raise CRYO_PERF_FIXTURE_FILES / ROWS_PER_FILE for fuller GB.
        target_mb = int(os.getenv("CRYO_PERF_FIXTURE_TARGET_MB", "2048"))
    if file_count is None:
        file_count = int(os.getenv("CRYO_PERF_FIXTURE_FILES", "40"))
    rows_per_file = int(os.getenv("CRYO_PERF_FIXTURE_ROWS_PER_FILE", "60_000"))

    identifier = (COMPACTION_NAMESPACE, PERF_DENSE_PARTITION_TABLE)
    if not force:
        try:
            table = catalog.load_table(identifier)  # type: ignore[attr-defined]
            snapshot = capture_table_snapshot(table)
            if snapshot.data_file_count >= max(4, file_count // 4):
                return CompactionFixture(
                    name=PERF_DENSE_PARTITION_TABLE,
                    table_ref=f"{COMPACTION_NAMESPACE}.{PERF_DENSE_PARTITION_TABLE}",
                    location=normalize_uri(table.location()),
                    initial_snapshot=snapshot,
                )
        except Exception:
            pass

    _drop_if_exists(catalog, identifier)

    schema = Schema(
        NestedField(1, "id", LongType(), required=True),
        NestedField(2, "ingest_date", StringType(), required=True),
        NestedField(3, "country", StringType(), required=True),
        NestedField(4, "city", StringType(), required=True),
        NestedField(5, "isp", StringType(), required=True),
        NestedField(6, "asn", StringType(), required=True),
        NestedField(7, "org", StringType(), required=True),
        NestedField(8, "region", StringType(), required=True),
        NestedField(9, "timezone", StringType(), required=True),
        NestedField(10, "postal_code", StringType(), required=True),
        NestedField(11, "latitude", StringType(), required=True),
        NestedField(12, "longitude", StringType(), required=True),
        NestedField(13, "network", StringType(), required=True),
        NestedField(14, "label_a", StringType(), required=True),
        NestedField(15, "label_b", StringType(), required=True),
        NestedField(16, "label_c", StringType(), required=True),
        NestedField(17, "label_d", StringType(), required=True),
        NestedField(18, "label_e", StringType(), required=True),
        NestedField(19, "label_f", StringType(), required=True),
        NestedField(20, "label_g", StringType(), required=True),
        NestedField(21, "label_h", StringType(), required=True),
        NestedField(22, "label_i", StringType(), required=True),
        NestedField(23, "label_j", StringType(), required=True),
        NestedField(24, "label_k", StringType(), required=True),
        NestedField(25, "label_l", StringType(), required=True),
        NestedField(26, "label_m", StringType(), required=True),
        NestedField(27, "label_n", StringType(), required=True),
        NestedField(28, "label_o", StringType(), required=True),
        NestedField(29, "payload", StringType(), required=True),
    )
    catalog.create_table(identifier, schema=schema)  # type: ignore[attr-defined]
    table = catalog.load_table(identifier)  # type: ignore[attr-defined]
    with table.update_spec() as update:
        update.add_field("ingest_date", IdentityTransform(), "ingest_date")
    table = catalog.load_table(identifier)  # type: ignore[attr-defined]

    arrow_fields = [
        pa.field("id", pa.int64(), nullable=False),
        pa.field("ingest_date", pa.string(), nullable=False),
        pa.field("country", pa.string(), nullable=False),
        pa.field("city", pa.string(), nullable=False),
        pa.field("isp", pa.string(), nullable=False),
        pa.field("asn", pa.string(), nullable=False),
        pa.field("org", pa.string(), nullable=False),
        pa.field("region", pa.string(), nullable=False),
        pa.field("timezone", pa.string(), nullable=False),
        pa.field("postal_code", pa.string(), nullable=False),
        pa.field("latitude", pa.string(), nullable=False),
        pa.field("longitude", pa.string(), nullable=False),
        pa.field("network", pa.string(), nullable=False),
        *[pa.field(f"label_{c}", pa.string(), nullable=False) for c in "abcdefghijklmno"],
        pa.field("payload", pa.string(), nullable=False),
    ]
    arrow_schema = pa.schema(arrow_fields)
    ingest_date = "2025-02-13"
    # Mix low-cardinality dims (GeoIP-shaped) with per-row payload so Parquet
    # does not collapse to near-zero size and ByteArrayStatistics stay hot.
    cities = ("delhi", "mumbai", "bengaluru", "chennai", "kolkata", "hyderabad")
    isps = ("airtel", "jio", "bsnl", "vodafone")
    _ = target_mb
    row_id = 0
    for batch_idx in range(file_count):
        ids = list(range(row_id, row_id + rows_per_file))
        city = cities[batch_idx % len(cities)]
        isp = isps[batch_idx % len(isps)]
        # ~180-byte mostly-unique payloads → real encode/stats cost + denser files.
        payloads = [
            f"geoip|{batch_idx:04d}|{i}|{city}|{isp}|{'x' * 96}|{i * 7919 % 9973}" for i in ids
        ]
        arrays = [
            pa.array(ids, type=pa.int64()),
            pa.array([ingest_date] * rows_per_file, type=pa.string()),
            pa.array(["IN"] * rows_per_file, type=pa.string()),
            pa.array([city] * rows_per_file, type=pa.string()),
            pa.array([isp] * rows_per_file, type=pa.string()),
            pa.array([f"AS{24560 + (batch_idx % 50)}"] * rows_per_file, type=pa.string()),
            pa.array([f"org-{isp}-{city}"] * rows_per_file, type=pa.string()),
            pa.array(["DL"] * rows_per_file, type=pa.string()),
            pa.array(["Asia/Kolkata"] * rows_per_file, type=pa.string()),
            pa.array([f"{110001 + (batch_idx % 100)}"] * rows_per_file, type=pa.string()),
            pa.array(["28.6139"] * rows_per_file, type=pa.string()),
            pa.array(["77.2090"] * rows_per_file, type=pa.string()),
            pa.array([f"103.21.{batch_idx % 256}.0/24"] * rows_per_file, type=pa.string()),
        ]
        for c in "abcdefghijklmno":
            arrays.append(
                pa.array([f"{c}-{city}-{batch_idx % 17}"] * rows_per_file, type=pa.string())
            )
        arrays.append(pa.array(payloads, type=pa.string()))
        arrow = pa.Table.from_arrays(arrays, schema=arrow_schema)
        table.append(arrow)
        row_id += rows_per_file
        table = catalog.load_table(identifier)  # type: ignore[attr-defined]
        if batch_idx % 4 == 0:
            print(
                f"perf fixture: wrote file {batch_idx + 1}/{file_count} ({row_id:,} rows)",
                flush=True,
            )

    snapshot = capture_table_snapshot(table)
    return CompactionFixture(
        name=PERF_DENSE_PARTITION_TABLE,
        table_ref=f"{COMPACTION_NAMESPACE}.{PERF_DENSE_PARTITION_TABLE}",
        location=normalize_uri(table.location()),
        initial_snapshot=snapshot,
    )
