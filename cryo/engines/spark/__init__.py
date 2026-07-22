from cryo.domain.enums import OperationType
from cryo.engines.spark.emr import (
    EMRServerlessClient,
    EMRServerlessConfig,
    JobRunResult,
    JobRunState,
)
from cryo.engines.spark.engine import SparkEMREngine
from cryo.engines.spark.sql import UnsupportedPredicateError, render, render_predicate

SUPPORTED_OPS: frozenset[OperationType] = frozenset(
    {
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
        OperationType.CONVERT_DELETES,
        OperationType.OPTIMIZE,
        OperationType.OPTIMIZE_MANIFESTS,
        OperationType.COMPUTE_STATISTICS,
        # sql.py's render() has always had a REWRITE_POSITION_DELETES branch (CALL
        # system.rewrite_position_delete_files) -- this registry just never listed it as
        # supported, so the plan verifier rejected any preset that tried to schedule it
        # (confirmed live, 2026-07-22, testing a preset built specifically to shrink a
        # MOR table's position-delete-dominated small-file count).
        OperationType.REWRITE_POSITION_DELETES,
    }
)

__all__ = [
    "EMRServerlessClient",
    "EMRServerlessConfig",
    "JobRunResult",
    "JobRunState",
    "SparkEMREngine",
    "SUPPORTED_OPS",
    "UnsupportedPredicateError",
    "render",
    "render_predicate",
]
