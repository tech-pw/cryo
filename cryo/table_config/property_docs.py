"""Plain-language explanations for every key in a table's property map.

Keys and defaults follow the Iceberg configuration reference
(https://iceberg.apache.org/docs/latest/configuration/). Keys Cryo curates as settings link
to their setting row instead of repeating it here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

PropertyKind = Literal[
    "setting", "read", "write", "behavior", "reserved", "catalog", "engine", "custom"
]

_CONFIG_DOC = "https://iceberg.apache.org/docs/latest/configuration/"
_READ = f"{_CONFIG_DOC}#read-properties"
_WRITE = f"{_CONFIG_DOC}#write-properties"
_BEHAVIOR = f"{_CONFIG_DOC}#table-behavior-properties"
_RESERVED = f"{_CONFIG_DOC}#reserved-table-properties"


@dataclass(frozen=True, slots=True)
class PropertyDoc:
    kind: PropertyKind
    summary: str
    doc_url: str | None = None


_EXACT: dict[str, PropertyDoc] = {
    "read.split.target-size": PropertyDoc(
        "read",
        "Target size of each read split when combining files into tasks (default 128 MiB).",
        _READ,
    ),
    "read.split.metadata-target-size": PropertyDoc(
        "read",
        "Target split size when reading metadata tables such as $files (default 32 MiB).",
        _READ,
    ),
    "read.split.planning-lookback": PropertyDoc(
        "read", "How many bins split planning looks back when packing files (default 10).", _READ
    ),
    "read.split.open-file-cost": PropertyDoc(
        "read",
        "Estimated cost of opening a file, in bytes, so planning doesn't pack too many small "
        "files into one split (default 4 MiB).",
        _READ,
    ),
    "read.parquet.vectorization.enabled": PropertyDoc(
        "read", "Whether Spark reads Parquet with vectorized readers (default true).", _READ
    ),
    "read.parquet.vectorization.batch-size": PropertyDoc(
        "read", "Rows per batch for vectorized Parquet reads (default 5000).", _READ
    ),
    "read.orc.vectorization.enabled": PropertyDoc(
        "read", "Whether Spark reads ORC with vectorized readers (default false).", _READ
    ),
    "read.orc.vectorization.batch-size": PropertyDoc(
        "read", "Rows per batch for vectorized ORC reads (default 5000).", _READ
    ),
    "write.delete.format.default": PropertyDoc(
        "write", "File format for delete files. Defaults to the data file format.", _WRITE
    ),
    "write.parquet.page-size-bytes": PropertyDoc(
        "write", "Target Parquet page size before compression (default 1 MiB).", _WRITE
    ),
    "write.parquet.page-row-limit": PropertyDoc(
        "write", "Maximum rows per Parquet page (default 20000).", _WRITE
    ),
    "write.parquet.dict-size-bytes": PropertyDoc(
        "write", "Parquet dictionary page size limit (default 2 MiB).", _WRITE
    ),
    "write.parquet.compression-level": PropertyDoc(
        "write", "Compression level for the Parquet codec. Unset uses the codec's default.", _WRITE
    ),
    "write.parquet.bloom-filter-max-bytes": PropertyDoc(
        "write", "Upper bound on each Parquet bloom filter's size (default 1 MiB).", _WRITE
    ),
    "write.parquet.row-group-limit": PropertyDoc(
        "write", "Maximum rows per Parquet row group, used by PyIceberg writers.", _WRITE
    ),
    "write.avro.compression-codec": PropertyDoc(
        "write", "Codec for Avro data files: gzip (default), zstd, snappy or uncompressed.", _WRITE
    ),
    "write.avro.compression-level": PropertyDoc(
        "write", "Compression level for the Avro codec. Unset uses the codec's default.", _WRITE
    ),
    "write.orc.stripe-size-bytes": PropertyDoc(
        "write", "Target ORC stripe size (default 64 MiB).", _WRITE
    ),
    "write.orc.block-size-bytes": PropertyDoc(
        "write", "ORC file-system block size (default 256 MiB).", _WRITE
    ),
    "write.orc.compression-codec": PropertyDoc(
        "write", "Codec for ORC data files (default zlib).", _WRITE
    ),
    "write.orc.compression-strategy": PropertyDoc(
        "write", "ORC compression strategy: speed (default) or compression.", _WRITE
    ),
    "write.orc.bloom.filter.columns": PropertyDoc(
        "write", "Comma-separated columns that get ORC bloom filters.", _WRITE
    ),
    "write.orc.bloom.filter.fpp": PropertyDoc(
        "write", "False-positive probability for ORC bloom filters (default 0.05).", _WRITE
    ),
    "write.location-provider.impl": PropertyDoc(
        "write", "Java class that decides where new data files are written.", _WRITE
    ),
    "write.py-location-provider.impl": PropertyDoc(
        "write", "Python class PyIceberg uses to decide where new data files are written.", _WRITE
    ),
    "write.metadata.compression-codec": PropertyDoc(
        "write", "Compression for metadata.json files: none (default) or gzip.", _WRITE
    ),
    "write.metadata.path": PropertyDoc(
        "write",
        "Base location for metadata files. Defaults to the table's metadata folder.",
        _WRITE,
    ),
    "write.object-storage.partitioned-paths": PropertyDoc(
        "write",
        "With object-storage layout, whether partition values stay in file paths (default true).",
        _WRITE,
    ),
    "write.delete.distribution-mode": PropertyDoc(
        "write", "How rows are distributed across writers for DELETE (none, hash or range).", _WRITE
    ),
    "write.update.distribution-mode": PropertyDoc(
        "write", "How rows are distributed across writers for UPDATE (none, hash or range).", _WRITE
    ),
    "write.merge.distribution-mode": PropertyDoc(
        "write", "How rows are distributed across writers for MERGE (none, hash or range).", _WRITE
    ),
    "write.delete.isolation-level": PropertyDoc(
        "write", "Isolation for DELETE: serializable (default) or snapshot.", _WRITE
    ),
    "write.update.isolation-level": PropertyDoc(
        "write", "Isolation for UPDATE: serializable (default) or snapshot.", _WRITE
    ),
    "write.merge.isolation-level": PropertyDoc(
        "write", "Isolation for MERGE: serializable (default) or snapshot.", _WRITE
    ),
    "write.wap.enabled": PropertyDoc(
        "write",
        "Enables write-audit-publish: Spark writes are staged and only published after "
        "review (default false).",
        _WRITE,
    ),
    "write.summary.partition-limit": PropertyDoc(
        "write",
        "Snapshot summaries include per-partition stats only when a commit touches at most "
        "this many partitions (default 0, never).",
        _WRITE,
    ),
    "write.spark.fanout.enabled": PropertyDoc(
        "write",
        "Lets Spark keep a file open per partition so input needn't be sorted, at the cost "
        "of memory (default false).",
        _WRITE,
    ),
    "commit.status-check.num-retries": PropertyDoc(
        "behavior",
        "Times to check whether a commit landed after a connection was lost (default 3).",
        _BEHAVIOR,
    ),
    "commit.status-check.min-wait-ms": PropertyDoc(
        "behavior", "Minimum wait between commit status checks (default 1000 ms).", _BEHAVIOR
    ),
    "commit.status-check.max-wait-ms": PropertyDoc(
        "behavior", "Maximum wait between commit status checks (default 60000 ms).", _BEHAVIOR
    ),
    "commit.status-check.total-timeout-ms": PropertyDoc(
        "behavior", "Total time to keep checking a commit's status (default 30 minutes).", _BEHAVIOR
    ),
    "schema.name-mapping.default": PropertyDoc(
        "behavior",
        "JSON mapping from column names to field IDs, used to read files written without "
        "Iceberg field IDs (for example after migrating a Hive table).",
        _CONFIG_DOC,
    ),
    "uuid": PropertyDoc("reserved", "The table's UUID, mirrored from metadata.", _RESERVED),
    "snapshot-count": PropertyDoc(
        "reserved", "Number of snapshots, mirrored into the catalog.", _RESERVED
    ),
    "current-snapshot-id": PropertyDoc(
        "reserved", "ID of the current snapshot, mirrored into the catalog.", _RESERVED
    ),
    "current-snapshot-summary": PropertyDoc(
        "reserved", "Summary of the current snapshot, mirrored into the catalog.", _RESERVED
    ),
    "current-snapshot-timestamp-ms": PropertyDoc(
        "reserved", "Commit time of the current snapshot, mirrored into the catalog.", _RESERVED
    ),
    "current-schema": PropertyDoc(
        "reserved", "The current schema as JSON, mirrored into the catalog.", _RESERVED
    ),
    "default-partition-spec": PropertyDoc(
        "reserved", "The default partition spec as JSON, mirrored into the catalog.", _RESERVED
    ),
    "default-sort-order": PropertyDoc(
        "reserved", "The default sort order as JSON, mirrored into the catalog.", _RESERVED
    ),
    "table_type": PropertyDoc(
        "catalog", "Marks the catalog entry as an Iceberg table (Hive Metastore, Glue)."
    ),
    "metadata_location": PropertyDoc(
        "catalog", "Pointer to the current metadata.json, kept by Hive Metastore or Glue."
    ),
    "previous_metadata_location": PropertyDoc(
        "catalog", "Pointer to the previous metadata.json, kept by Hive Metastore or Glue."
    ),
    "EXTERNAL": PropertyDoc(
        "catalog", "Hive marker: dropping the table entry does not delete its files by itself."
    ),
    "transient_lastDdlTime": PropertyDoc(
        "catalog", "Hive Metastore's last DDL time, in epoch seconds."
    ),
    "storage_handler": PropertyDoc(
        "catalog", "Hive storage handler class that lets Hive read the Iceberg table."
    ),
    "engine.hive.enabled": PropertyDoc(
        "catalog", "Whether the Hive engine integration is enabled for this table."
    ),
    "numFiles": PropertyDoc("catalog", "Hive Metastore statistic. Iceberg doesn't use it."),
    "numRows": PropertyDoc("catalog", "Hive Metastore statistic. Iceberg doesn't use it."),
    "totalSize": PropertyDoc("catalog", "Hive Metastore statistic. Iceberg doesn't use it."),
    "rawDataSize": PropertyDoc("catalog", "Hive Metastore statistic. Iceberg doesn't use it."),
    "COLUMN_STATS_ACCURATE": PropertyDoc(
        "catalog", "Hive Metastore statistics flag. Iceberg doesn't use it."
    ),
    "bucketing_version": PropertyDoc(
        "catalog", "Hive bucketing hash version. Iceberg doesn't use it."
    ),
    "owner": PropertyDoc("engine", "Owner recorded by the engine that created the table."),
}

_PREFIXES: tuple[tuple[str, PropertyDoc], ...] = (
    (
        "write.parquet.bloom-filter-enabled.column.",
        PropertyDoc(
            "write",
            "Turns on a Parquet bloom filter for this column, so point lookups can skip row "
            "groups that cannot contain the value.",
            _WRITE,
        ),
    ),
    (
        "write.parquet.bloom-filter-fpp.column.",
        PropertyDoc(
            "write", "False-positive probability of this column's Parquet bloom filter.", _WRITE
        ),
    ),
    (
        "write.metadata.metrics.column.",
        PropertyDoc(
            "write",
            "Metrics mode for this column (none, counts, truncate(N) or full), overriding "
            "write.metadata.metrics.default.",
            _WRITE,
        ),
    ),
    (
        "trino.",
        PropertyDoc("engine", "Written by Trino. Informational; Iceberg ignores it."),
    ),
    (
        "trino_",
        PropertyDoc("engine", "Written by Trino. Informational; Iceberg ignores it."),
    ),
    (
        "presto_",
        PropertyDoc("engine", "Written by Presto or older Trino. Iceberg ignores it."),
    ),
    (
        "spark.",
        PropertyDoc("engine", "Written by Spark. Informational; Iceberg ignores it."),
    ),
    (
        "flink.",
        PropertyDoc("engine", "Written by Flink. Informational; Iceberg ignores it."),
    ),
)

_CUSTOM = PropertyDoc(
    "custom",
    "Not an Iceberg property. A writer, tool or person added it; Iceberg stores it but "
    "doesn't act on it.",
)


@dataclass(frozen=True, slots=True)
class PropertyDetail:
    key: str
    value: str
    kind: PropertyKind
    summary: str
    doc_url: str | None
    setting_key: str | None


def describe_property(
    key: str, value: str, settings: Mapping[str, tuple[str, str, str]]
) -> PropertyDetail:
    """``settings`` maps a curated property key to (setting key, summary, doc URL)."""
    curated = settings.get(key)
    if curated is not None:
        setting_key, summary, doc_url = curated
        return PropertyDetail(key, value, "setting", summary, doc_url, setting_key)
    doc = _EXACT.get(key)
    if doc is None:
        doc = next((d for prefix, d in _PREFIXES if key.startswith(prefix)), _CUSTOM)
    return PropertyDetail(key, value, doc.kind, doc.summary, doc.doc_url, None)
