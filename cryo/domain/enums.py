from __future__ import annotations

from enum import StrEnum


class OperationType(StrEnum):
    EXPIRE_SNAPSHOTS = "expire_snapshots"
    REMOVE_ORPHAN_FILES = "remove_orphan_files"
    CONVERT_DELETES = "convert_deletes"
    OPTIMIZE = "optimize"
    OPTIMIZE_MANIFESTS = "optimize_manifests"
    COMPUTE_STATISTICS = "compute_statistics"  # Puffin NDV + Bloom filter stats
    # Okolnychyi et al. (VLDB'24) "minor" delete compaction: merges only the accumulated
    # position-delete files, cheap relative to a full data rewrite (CONVERT_DELETES / OPTIMIZE
    # is the "major" path for this same problem -- distinct type for audit-log clarity).
    REWRITE_POSITION_DELETES = "rewrite_position_deletes"


class EngineType(StrEnum):
    TRINO = "trino"
    SPARK_EMR = "spark_emr"
    NATIVE = "native"
    FLINK = "flink"


class Mode(StrEnum):
    LIGHT = "light"
    STANDARD = "standard"
    DEEP = "deep"
    MANIFEST_ONLY = "manifest_only"


class PartitionTransform(StrEnum):
    IDENTITY = "identity"
    DAY = "day"
    MONTH = "month"
    YEAR = "year"
    BUCKET = "bucket"
    UNPARTITIONED = "unpartitioned"
    UNSUPPORTED = "unsupported"


class PredicateCapability(StrEnum):
    BASIC_COMPARISON = "basic_comparison"
    BUCKET_FILTER = "bucket_filter"


class Priority(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class CostPrecision(StrEnum):
    HEURISTIC = "heuristic"
    METADATA = "metadata"
    STORAGE_TRUTH = "storage_truth"


class JobStatus(StrEnum):
    QUEUED = "queued"
    ADMITTED = "admitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    DEFERRED = "deferred"
    PARTIAL = "partial"


class JobStepStatus(StrEnum):
    """Write-ahead statement status on ``cryo.job_steps``.

    See ``docs/WORKER_STEP_LEDGER.md``.
    """

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
