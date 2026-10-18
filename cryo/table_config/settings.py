"""Curated catalogue of the Iceberg table settings that change what maintenance does.

Keys and defaults are from Iceberg ``TableProperties`` 1.12.0 and the configuration docs; engine
behaviour is from the Trino 484 Iceberg connector docs/release notes, the Iceberg Spark procedure
docs, and iceberg-go ``table/properties.go``. Every entry is a real key that PyIceberg can write
with ``Transaction.set_properties`` unless ``kind`` says otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SettingGroup = Literal["file_layout", "row_level", "commits", "metadata", "retention", "format"]
SettingKind = Literal["property", "cryo_param", "bloom_columns", "format_version"]
ValueType = Literal[
    "bytes", "int", "bool", "enum", "duration_ms", "duration_days", "columns", "text"
]
MinRole = Literal["operator", "admin"]

MIB = 1024 * 1024
GIB = 1024 * MIB
DAY_MS = 86_400_000
HOUR_MS = 3_600_000

ICEBERG_WRITE_DOCS = "https://iceberg.apache.org/docs/latest/configuration/#write-properties"
ICEBERG_BEHAVIOR_DOCS = (
    "https://iceberg.apache.org/docs/latest/configuration/#table-behavior-properties"
)
ICEBERG_RESERVED_DOCS = (
    "https://iceberg.apache.org/docs/latest/configuration/#reserved-table-properties"
)
ICEBERG_MAINTENANCE_DOCS = "https://iceberg.apache.org/docs/latest/maintenance/"
SPARK_DISTRIBUTION_DOCS = (
    "https://iceberg.apache.org/docs/latest/spark-writes/#writing-distribution-modes"
)
FLINK_UPSERT_DOCS = "https://iceberg.apache.org/docs/latest/flink-writes/#upsert"
SPEC_RETENTION_DOCS = "https://iceberg.apache.org/spec/#snapshot-retention-policy"
TRINO_ICEBERG_DOCS = "https://trino.io/docs/current/connector/iceberg.html"

BLOOM_FILTER_PREFIX = "write.parquet.bloom-filter-enabled.column."
BLOOM_COLUMNS_KEY = "write.parquet.bloom-filter-columns"
CRYO_PARAM_PREFIX = "cryo."

ROW_LEVEL_MODES = ("copy-on-write", "merge-on-read")
METRICS_MODES = (
    "none",
    "counts",
    "truncate(8)",
    "truncate(16)",
    "truncate(32)",
    "truncate(64)",
    "full",
)


@dataclass(frozen=True)
class GroupSpec:
    id: SettingGroup
    label: str
    description: str


GROUPS: tuple[GroupSpec, ...] = (
    GroupSpec(
        "file_layout",
        "File layout",
        "How big new files are, how rows are spread across them, and how they are encoded.",
    ),
    GroupSpec(
        "row_level",
        "Row-level operations",
        "How DELETE, UPDATE and MERGE change data: rewrite files or write delete files.",
    ),
    GroupSpec(
        "commits",
        "Commits and manifests",
        "Retry behaviour under concurrent writers and how manifests are merged on commit.",
    ),
    GroupSpec(
        "metadata",
        "Metadata and statistics",
        "metadata.json retention and the column statistics that drive file pruning.",
    ),
    GroupSpec(
        "retention",
        "Retention and GC",
        "What snapshot expiry and orphan-file removal keep, and whether they may delete at all.",
    ),
    GroupSpec(
        "format",
        "Format and identity",
        "Table format version and descriptive metadata.",
    ),
)


@dataclass(frozen=True)
class EngineNote:
    engine: Literal["trino", "spark", "flink", "native", "cryo"]
    note: str


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    group: SettingGroup
    value_type: ValueType
    summary: str
    takes_effect: str
    reversible: str
    doc_url: str
    kind: SettingKind = "property"
    default: str | None = None
    default_label: str | None = None
    options: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None
    unit: str | None = None
    engine_notes: tuple[EngineNote, ...] = field(default_factory=tuple)
    editable: bool = True
    read_only_reason: str | None = None
    min_role: MinRole = "operator"
    allow_unset: bool = True
    read_only_risk: Literal["low", "medium", "high"] = "low"


_FUTURE_WRITES = "Future writes and compactions only. Existing files keep their old layout."
_COMPACT_TO_APPLY = "Future writes only. Existing files change only when they are next compacted."
_METADATA_ONLY_REVERSIBLE = "Yes. A metadata-only commit; set it back at any time."


SETTINGS: tuple[SettingSpec, ...] = (
    # ── File layout ─────────────────────────────────────────────────────────────
    SettingSpec(
        key="write.target-file-size-bytes",
        label="Target data file size",
        group="file_layout",
        value_type="bytes",
        summary=(
            "The size writers aim for when they roll over to a new data file. The main lever "
            "against small files."
        ),
        takes_effect=_COMPACT_TO_APPLY,
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        default=str(512 * MIB),
        minimum=16 * MIB,
        maximum=4 * GIB,
        engine_notes=(
            EngineNote(
                "trino",
                "Honoured from Trino 482 (table property target_max_file_size). Before 482 "
                "Trino ignores it and uses the catalog's iceberg.target-max-file-size "
                "(default 1 GB).",
            ),
            EngineNote(
                "spark",
                "Default target for rewrite_data_files. Spark writes cannot exceed one task's "
                "output, so AQE advisory partition size must be at least this large.",
            ),
            EngineNote("flink", "Default rolling-file target unless the job overrides it."),
            EngineNote(
                "cryo",
                "Cryo's Spark and native compactions set this to the preset target after every "
                "optimize, so a different value is reset on the next run.",
            ),
        ),
    ),
    SettingSpec(
        key="write.delete.target-file-size-bytes",
        label="Target delete file size",
        group="file_layout",
        value_type="bytes",
        summary="Target size for position and equality delete files written by merge-on-read.",
        takes_effect=_FUTURE_WRITES,
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        default=str(64 * MIB),
        minimum=8 * MIB,
        maximum=1 * GIB,
        engine_notes=(
            EngineNote("spark", "Default target for rewrite_position_delete_files."),
            EngineNote("trino", "Not read by Trino."),
        ),
    ),
    SettingSpec(
        key="write.distribution-mode",
        label="Write distribution",
        group="file_layout",
        value_type="enum",
        summary=(
            "How rows are shuffled before writing. hash groups rows by partition so each "
            "partition gets few, large files; range also orders by the sort key; none writes "
            "whatever each task holds."
        ),
        takes_effect=_FUTURE_WRITES,
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=SPARK_DISTRIBUTION_DOCS,
        options=("none", "hash", "range"),
        default=None,
        default_label="Not set: engine decides",
        engine_notes=(
            EngineNote(
                "spark",
                "When unset, Spark requests hash since Iceberg 1.2, or range if the table has a "
                "sort order.",
            ),
            EngineNote("flink", "When unset, Flink does not shuffle (none)."),
            EngineNote("trino", "Not read by Trino; Trino has its own writer scaling."),
            EngineNote(
                "cryo",
                "Cryo's Spark and native compactions set this to hash after every optimize.",
            ),
        ),
    ),
    SettingSpec(
        key="write.format.default",
        label="Data file format",
        group="file_layout",
        value_type="enum",
        summary="File format for new data files.",
        takes_effect=(
            "Future writes only. Existing files stay in their format until compacted, so the "
            "table holds mixed formats for a while."
        ),
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        options=("parquet", "orc", "avro"),
        default="parquet",
        engine_notes=(
            EngineNote("trino", "Trino table property format."),
            EngineNote(
                "cryo",
                "Cryo's bloom-filter, row-group and column-statistics analysis reads Parquet.",
            ),
        ),
    ),
    SettingSpec(
        key="write.parquet.compression-codec",
        label="Parquet compression",
        group="file_layout",
        value_type="enum",
        summary="Compression codec for new Parquet data files.",
        takes_effect=_COMPACT_TO_APPLY,
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        options=("zstd", "snappy", "gzip", "lz4", "brotli", "uncompressed"),
        default="zstd",
        default_label="zstd for tables created by Iceberg 1.4+; older tables fall back to gzip",
        engine_notes=(
            EngineNote(
                "trino",
                "Trino table property compression_codec (477+); new Trino tables get the "
                "catalog's iceberg.compression-codec.",
            ),
        ),
    ),
    SettingSpec(
        key="write.parquet.row-group-size-bytes",
        label="Parquet row group size",
        group="file_layout",
        value_type="bytes",
        summary=(
            "Target size of a Parquet row group, the unit readers skip using min/max statistics."
        ),
        takes_effect=_COMPACT_TO_APPLY,
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        default=str(128 * MIB),
        minimum=8 * MIB,
        maximum=1 * GIB,
        engine_notes=(
            EngineNote(
                "trino",
                "Honoured from Trino 482 (parquet_writer_row_group_size); earlier versions use "
                "the catalog's parquet.writer.row-group-size.",
            ),
        ),
    ),
    SettingSpec(
        key=BLOOM_COLUMNS_KEY,
        kind="bloom_columns",
        label="Parquet bloom filter columns",
        group="file_layout",
        value_type="columns",
        summary=(
            "Columns that get a Parquet bloom filter, so equality lookups on high-cardinality "
            "values skip row groups. Stored as one write.parquet.bloom-filter-enabled.column.<c> "
            "property per column."
        ),
        takes_effect=_COMPACT_TO_APPLY,
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        default="",
        default_label="None",
        engine_notes=(
            EngineNote("trino", "Trino table property parquet_bloom_filter_columns (469+)."),
            EngineNote(
                "spark",
                "Written by Spark Parquet writers; each filter adds up to 1 MB per row group.",
            ),
        ),
    ),
    SettingSpec(
        key="write.object-storage.enabled",
        label="Object-storage file layout",
        group="file_layout",
        value_type="bool",
        summary=(
            "Adds a hash component to new data file paths so writes spread across S3 key "
            "prefixes instead of throttling on one."
        ),
        takes_effect="New files only. Existing file paths never change.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        default="false",
        engine_notes=(EngineNote("trino", "Trino table property object_store_layout_enabled."),),
    ),
    SettingSpec(
        key="write.data.path",
        label="Data file location",
        group="file_layout",
        value_type="text",
        summary="Base location for new data files. Defaults to the table location plus /data.",
        takes_effect="New files only. Existing files are not moved.",
        reversible="Only for new files; files already written there stay there.",
        doc_url=ICEBERG_WRITE_DOCS,
        default=None,
        default_label="<table location>/data",
        editable=False,
        read_only_risk="high",
        read_only_reason=(
            "Orphan-file removal lists files under the table location. Moving new data "
            "elsewhere changes what that scan sees, so change it deliberately with engine DDL."
        ),
    ),
    # ── Row-level operations ────────────────────────────────────────────────────
    SettingSpec(
        key="write.delete.mode",
        label="DELETE mode",
        group="row_level",
        value_type="enum",
        summary=(
            "copy-on-write rewrites every data file a DELETE touches; merge-on-read writes "
            "small delete files that readers apply and compaction later folds in."
        ),
        takes_effect="The next DELETE statement.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        options=ROW_LEVEL_MODES,
        default="copy-on-write",
        engine_notes=(
            EngineNote("spark", "Read by Spark DELETE."),
            EngineNote(
                "trino",
                "Trino always writes position deletes (deletion vectors on v3) regardless.",
            ),
        ),
    ),
    SettingSpec(
        key="write.update.mode",
        label="UPDATE mode",
        group="row_level",
        value_type="enum",
        summary=(
            "copy-on-write rewrites every data file an UPDATE touches; merge-on-read writes "
            "delete files plus new rows."
        ),
        takes_effect="The next UPDATE statement.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        options=ROW_LEVEL_MODES,
        default="copy-on-write",
        engine_notes=(
            EngineNote("spark", "Read by Spark UPDATE."),
            EngineNote("trino", "Not read; Trino uses merge-on-read semantics."),
        ),
    ),
    SettingSpec(
        key="write.merge.mode",
        label="MERGE mode",
        group="row_level",
        value_type="enum",
        summary=(
            "copy-on-write rewrites every data file a MERGE touches; merge-on-read writes "
            "delete files plus new rows."
        ),
        takes_effect="The next MERGE statement.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        options=ROW_LEVEL_MODES,
        default="copy-on-write",
        engine_notes=(
            EngineNote("spark", "Read by Spark MERGE INTO."),
            EngineNote("trino", "Not read; Trino uses merge-on-read semantics."),
        ),
    ),
    SettingSpec(
        key="write.delete.granularity",
        label="Position delete granularity",
        group="row_level",
        value_type="enum",
        summary=(
            "Whether a merge-on-read commit writes one position-delete file per partition or "
            "per data file. file-scoped deletes are read only with their data file and are "
            "dropped when that file is compacted."
        ),
        takes_effect="The next merge-on-read commit.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        options=("partition", "file"),
        default="partition",
        engine_notes=(
            EngineNote(
                "spark",
                "Spark's delete-granularity write option defaults to file and overrides this.",
            ),
            EngineNote(
                "trino",
                "Format v3 tables use deletion vectors, which are always per data file.",
            ),
        ),
    ),
    SettingSpec(
        key="write.upsert.enabled",
        label="Upsert writes",
        group="row_level",
        value_type="bool",
        summary=(
            "Makes every write an upsert keyed on the identifier fields: the old row is "
            "removed with an equality delete and the new row appended."
        ),
        takes_effect="The next write from an engine that honours it.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=FLINK_UPSERT_DOCS,
        default="false",
        engine_notes=(
            EngineNote(
                "flink",
                "Read by Flink for batch and streaming writes. Requires format v2+ and "
                "identifier fields.",
            ),
            EngineNote("spark", "Not read by Spark."),
            EngineNote("trino", "Not read by Trino."),
        ),
    ),
    # ── Commits and manifests ───────────────────────────────────────────────────
    SettingSpec(
        key="commit.retry.num-retries",
        label="Commit retries",
        group="commits",
        value_type="int",
        summary=(
            "How many times a writer retries a commit that lost an optimistic-concurrency race "
            "before failing."
        ),
        takes_effect="The next commit from any writer.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_BEHAVIOR_DOCS,
        default="4",
        minimum=0,
        maximum=100,
        engine_notes=(EngineNote("trino", "Trino table property max_commit_retry (470+)."),),
    ),
    SettingSpec(
        key="commit.retry.min-wait-ms",
        label="Commit retry minimum wait",
        group="commits",
        value_type="int",
        unit="ms",
        summary="Initial backoff before the first commit retry.",
        takes_effect="The next commit from any writer.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_BEHAVIOR_DOCS,
        default="100",
        minimum=0,
        maximum=600_000,
    ),
    SettingSpec(
        key="commit.retry.max-wait-ms",
        label="Commit retry maximum wait",
        group="commits",
        value_type="int",
        unit="ms",
        summary="Upper bound on the exponential backoff between commit retries.",
        takes_effect="The next commit from any writer.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_BEHAVIOR_DOCS,
        default="60000",
        minimum=0,
        maximum=3_600_000,
    ),
    SettingSpec(
        key="commit.retry.total-timeout-ms",
        label="Commit retry total timeout",
        group="commits",
        value_type="int",
        unit="ms",
        summary="Total time a writer keeps retrying a commit before giving up.",
        takes_effect="The next commit from any writer.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_BEHAVIOR_DOCS,
        default="1800000",
        minimum=1_000,
        maximum=86_400_000,
    ),
    SettingSpec(
        key="commit.manifest-merge.enabled",
        label="Merge manifests on commit",
        group="commits",
        value_type="bool",
        summary=(
            "Lets merge-append commits fold small manifests together. Off means every append "
            "adds a manifest, which slows query planning until manifests are rewritten."
        ),
        takes_effect="The next append commit.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_BEHAVIOR_DOCS,
        default="true",
        engine_notes=(
            EngineNote(
                "spark",
                "Spark streaming uses fast appends, which never merge, unless "
                "use-merge-append-for-streaming is set.",
            ),
        ),
    ),
    SettingSpec(
        key="commit.manifest.target-size-bytes",
        label="Target manifest size",
        group="commits",
        value_type="bytes",
        summary="Target size of manifest files produced by commits and manifest rewrites.",
        takes_effect="The next commit and the next manifest rewrite.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_BEHAVIOR_DOCS,
        default=str(8 * MIB),
        minimum=1 * MIB,
        maximum=256 * MIB,
        engine_notes=(
            EngineNote("trino", "Controls the output of optimize_manifests."),
            EngineNote("spark", "Controls the output of rewrite_manifests."),
        ),
    ),
    SettingSpec(
        key="commit.manifest.min-count-to-merge",
        label="Manifests before merge",
        group="commits",
        value_type="int",
        summary="Minimum number of manifests to accumulate before a merge-append merges them.",
        takes_effect="The next append commit.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_BEHAVIOR_DOCS,
        default="100",
        minimum=1,
        maximum=10_000,
    ),
    # ── Metadata and statistics ────────────────────────────────────────────────
    SettingSpec(
        key="write.metadata.delete-after-commit.enabled",
        label="Delete old metadata files",
        group="metadata",
        value_type="bool",
        summary=(
            "After each commit, delete the oldest tracked metadata.json beyond the "
            "previous-versions limit. Without it, metadata files accumulate on storage."
        ),
        takes_effect=(
            "The next commit. Files that already fell off the metadata log are not touched; "
            "only orphan-file removal reclaims those."
        ),
        reversible=("The setting is reversible; metadata files deleted while it was on are gone."),
        doc_url=ICEBERG_MAINTENANCE_DOCS + "#remove-old-metadata-files",
        default="false",
        engine_notes=(
            EngineNote("trino", "Trino table property delete_after_commit_enabled (480+)."),
        ),
    ),
    SettingSpec(
        key="write.metadata.previous-versions-max",
        label="Metadata versions kept",
        group="metadata",
        value_type="int",
        summary=(
            "How many previous metadata.json files the metadata log tracks. With delete-after-"
            "commit on, older ones are deleted."
        ),
        takes_effect="The next commit.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_MAINTENANCE_DOCS + "#remove-old-metadata-files",
        default="100",
        minimum=1,
        maximum=1_000,
        engine_notes=(EngineNote("trino", "Trino table property max_previous_versions (480+)."),),
    ),
    SettingSpec(
        key="write.metadata.metrics.default",
        label="Column metrics",
        group="metadata",
        value_type="enum",
        summary=(
            "Which per-column statistics go into manifests. truncate(N) and full keep min/max "
            "bounds that let engines skip files; counts and none do not."
        ),
        takes_effect="Files written from now on. Existing files keep their stored metrics.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        options=METRICS_MODES,
        default="truncate(16)",
    ),
    SettingSpec(
        key="write.metadata.metrics.max-inferred-column-defaults",
        label="Columns with default metrics",
        group="metadata",
        value_type="int",
        summary=(
            "Only the first N columns get the default metrics mode. Filters on later columns "
            "cannot prune files by min/max."
        ),
        takes_effect="Files written from now on.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_WRITE_DOCS,
        default="100",
        minimum=1,
        maximum=10_000,
    ),
    # ── Retention and GC ────────────────────────────────────────────────────────
    SettingSpec(
        key="gc.enabled",
        label="Garbage collection",
        group="retention",
        value_type="bool",
        summary=(
            "Whether snapshot expiry and orphan-file removal may delete this table's files. "
            "Owners turn it off when another table shares these files."
        ),
        takes_effect="The next expiry or orphan-removal run.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=ICEBERG_BEHAVIOR_DOCS,
        default="true",
        engine_notes=(
            EngineNote(
                "trino",
                "Enforced from Trino 484 (gc_enabled). Earlier Trino deletes files regardless.",
            ),
            EngineNote("spark", "expire_snapshots and remove_orphan_files refuse to run."),
            EngineNote("native", "iceberg-go expiry refuses to run."),
            EngineNote(
                "cryo",
                "Cryo skips expire_snapshots and remove_orphan_files while this is false.",
            ),
        ),
    ),
    SettingSpec(
        key="history.expire.max-snapshot-age-ms",
        label="Snapshot age limit (table)",
        group="retention",
        value_type="duration_ms",
        summary=(
            "Default age after which snapshots may be expired, for the table and every branch "
            "without its own limit."
        ),
        takes_effect="The next snapshot expiry that does not pass an explicit age.",
        reversible="The setting is reversible; snapshots expired under it are gone.",
        doc_url=SPEC_RETENTION_DOCS,
        default=str(5 * DAY_MS),
        minimum=HOUR_MS,
        maximum=3650 * DAY_MS,
        engine_notes=(
            EngineNote(
                "cryo",
                "Cryo passes its own retention to every engine, so this only affects expiry run "
                "outside Cryo.",
            ),
            EngineNote("spark", "Default older_than for expire_snapshots."),
            EngineNote("trino", "Ignored; Trino's retention_threshold defaults to 7 days."),
            EngineNote("native", "Default for iceberg-go expiry."),
        ),
    ),
    SettingSpec(
        key="history.expire.min-snapshots-to-keep",
        label="Snapshots always kept (table)",
        group="retention",
        value_type="int",
        summary="Expiry never removes the most recent N ancestors of each branch.",
        takes_effect="The next snapshot expiry that does not pass an explicit count.",
        reversible="The setting is reversible; snapshots expired under it are gone.",
        doc_url=SPEC_RETENTION_DOCS,
        default="1",
        minimum=1,
        maximum=1_000,
        engine_notes=(
            EngineNote(
                "cryo",
                "Cryo passes retain_last explicitly, so this only affects expiry run outside Cryo.",
            ),
            EngineNote("spark", "Default retain_last for expire_snapshots."),
        ),
    ),
    SettingSpec(
        key="history.expire.max-ref-age-ms",
        label="Branch and tag age limit",
        group="retention",
        value_type="duration_ms",
        summary=(
            "Age after which non-main branches and tags are removed by expiry. Unset means they "
            "live forever and keep every snapshot they point to."
        ),
        takes_effect="The next snapshot expiry.",
        reversible="The setting is reversible; branches and tags removed under it are gone.",
        doc_url=SPEC_RETENTION_DOCS,
        default=None,
        default_label="Forever",
        minimum=DAY_MS,
        maximum=36_500 * DAY_MS,
    ),
    SettingSpec(
        key=CRYO_PARAM_PREFIX + "retention_days",
        kind="cryo_param",
        label="Cryo snapshot retention",
        group="retention",
        value_type="duration_days",
        summary="Snapshots older than this are expired when Cryo runs expire_snapshots.",
        takes_effect="Cryo's next expire_snapshots run on this table.",
        reversible="The override is reversible; snapshots expired under it are gone.",
        doc_url=ICEBERG_MAINTENANCE_DOCS + "#expire-snapshots",
        default="7",
        minimum=1,
        maximum=3650,
        min_role="admin",
        engine_notes=(
            EngineNote(
                "cryo",
                "The planner shortens this to 1 to 3 days for streaming or snapshot-backlogged "
                "tables.",
            ),
            EngineNote(
                "trino",
                "Trino rejects values below iceberg.expire-snapshots.min-retention (7d default).",
            ),
        ),
    ),
    SettingSpec(
        key=CRYO_PARAM_PREFIX + "retain_last",
        kind="cryo_param",
        label="Cryo snapshots always kept",
        group="retention",
        value_type="int",
        summary="Cryo's expire_snapshots always keeps at least this many recent snapshots.",
        takes_effect="Cryo's next expire_snapshots run on this table.",
        reversible="The override is reversible; snapshots expired under it are gone.",
        doc_url=ICEBERG_MAINTENANCE_DOCS + "#expire-snapshots",
        default="1",
        minimum=1,
        maximum=1_000,
        min_role="admin",
    ),
    SettingSpec(
        key=CRYO_PARAM_PREFIX + "orphan_retention_days",
        kind="cryo_param",
        label="Cryo orphan-file age",
        group="retention",
        value_type="duration_days",
        summary=(
            "remove_orphan_files only deletes unreferenced files older than this. It must exceed "
            "the longest write, or in-flight files are deleted."
        ),
        takes_effect="Cryo's next remove_orphan_files run on this table.",
        reversible="The override is reversible; files deleted under it are gone.",
        doc_url=ICEBERG_MAINTENANCE_DOCS + "#delete-orphan-files",
        default=None,
        default_label="Same as snapshot retention",
        minimum=1,
        maximum=3650,
        min_role="admin",
        engine_notes=(
            EngineNote(
                "trino",
                "Trino rejects values below iceberg.remove-orphan-files.min-retention "
                "(7d default).",
            ),
            EngineNote("spark", "Spark's own default is 3 days."),
            EngineNote("native", "iceberg-go's own default is 72 hours."),
        ),
    ),
    # ── Format and identity ─────────────────────────────────────────────────────
    SettingSpec(
        key="format-version",
        kind="format_version",
        label="Format version",
        group="format",
        value_type="enum",
        summary=(
            "Iceberg table format. v2 adds row-level deletes; v3 adds deletion vectors, row "
            "lineage, default values and new types."
        ),
        takes_effect="Immediately for every reader and writer.",
        reversible="No. Format upgrades are one-way; there is no downgrade.",
        doc_url=ICEBERG_RESERVED_DOCS,
        options=("1", "2", "3"),
        default="2",
        min_role="admin",
        allow_unset=False,
        engine_notes=(
            EngineNote(
                "trino",
                "v3 support is experimental; add_files is not supported on v3.",
            ),
            EngineNote(
                "spark",
                "Every reader and writer needs Iceberg 1.8+ (1.9+ for row lineage).",
            ),
        ),
    ),
    SettingSpec(
        key="comment",
        label="Table description",
        group="format",
        value_type="text",
        summary="Free-text description shown by engines and catalogs.",
        takes_effect="Immediately.",
        reversible=_METADATA_ONLY_REVERSIBLE,
        doc_url=TRINO_ICEBERG_DOCS,
        default=None,
        default_label="Not set",
    ),
)

SETTINGS_BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in SETTINGS}


def spec_for(key: str) -> SettingSpec | None:
    return SETTINGS_BY_KEY.get(key)


def cryo_param_name(key: str) -> str:
    return key.removeprefix(CRYO_PARAM_PREFIX)
