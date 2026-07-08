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
