from __future__ import annotations

from enum import StrEnum


class OperationType(StrEnum):
    EXPIRE_SNAPSHOTS = "expire_snapshots"
    REMOVE_ORPHAN_FILES = "remove_orphan_files"
    OPTIMIZE = "optimize"
    OPTIMIZE_MANIFESTS = "optimize_manifests"


class EngineType(StrEnum):
    TRINO = "trino"
    SPARK_EMR = "spark_emr"
    NATIVE = "native"


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
