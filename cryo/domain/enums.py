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


# Real incident, 2026-09-15/16: cryo/governor/native_admit.py (the sole source of these reason
# strings, via _defer_spec's `reason=` callers) and cryo/db/repositories/run_finalizer.py (the
# sole consumer, deciding how long a run stays open once one of its jobs is DEFERRED) each kept
# their own independent idea of which defer reasons mean "a fleet-wide/shared-resource capacity
# gate, expected to clear on its own given real time" vs. "a job-specific reason, expected to
# clear within a wave or two." Those two designs disagreed -- the 10-minute grace built for the
# job-specific case was silently applied to capacity defers too, because nothing forced the two
# modules to agree on the classification -- and that's what let a metastore-congestion incident
# fast-fail 29,832 jobs as "orphaned_deferred: parent run already finalized" while the capacity
# constraint that deferred them was still genuinely active. Both modules now import this single
# list instead of each declaring/duplicating their own copy of it, so a new capacity-class gate
# added to native_admit.py (or a renamed one) can't silently drift out of sync with what
# run_finalizer.py grants a longer grace to -- there is exactly one place this is decided.
# Matched by prefix since metastore_capacity's reason string appends the specific URI
# (f"metastore_capacity:{uri}") and can vary per destination.
METASTORE_CAPACITY_DEFER_REASON_PREFIX = "metastore_capacity:"
NATIVE_CLUSTER_CAPACITY_DEFER_REASON = "native_cluster_capacity"
FLEET_ADMISSION_RATE_CAP_DEFER_REASON = "fleet_admission_rate_cap"

CAPACITY_DEFER_REASON_PREFIXES: tuple[str, ...] = (
    METASTORE_CAPACITY_DEFER_REASON_PREFIX,
    NATIVE_CLUSTER_CAPACITY_DEFER_REASON,
    FLEET_ADMISSION_RATE_CAP_DEFER_REASON,
)


def is_capacity_defer_reason(error: str | None) -> bool:
    """True when a MaintenanceJob.error value names a fleet-wide capacity gate (as opposed to a
    job- or plan-specific defer reason like native_admit_plan_rebuild_failed or a k8s_job_create
    failure) -- see CAPACITY_DEFER_REASON_PREFIXES' docstring for why this must be the one
    shared classification both the admission side and the run-finalization side rely on."""
    if not error:
        return False
    return error.startswith(CAPACITY_DEFER_REASON_PREFIXES)
