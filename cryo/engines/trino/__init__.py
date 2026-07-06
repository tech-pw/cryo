from cryo.domain.enums import OperationType
from cryo.engines.trino.engine import TrinoEngine
from cryo.engines.trino.sql import (
    UnsupportedPredicateError,
    render,
    render_predicate,
    session_setup,
)

# Operations this engine actually knows how to render + execute. Used by the Plan Verifier
# (cryo.planner.verifier) to reject plans that target an op the engine can't perform.
SUPPORTED_OPS: frozenset[OperationType] = frozenset(
    {
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.REMOVE_ORPHAN_FILES,
        OperationType.OPTIMIZE,
        OperationType.OPTIMIZE_MANIFESTS,
    }
)

__all__ = [
    "SUPPORTED_OPS",
    "TrinoEngine",
    "UnsupportedPredicateError",
    "render",
    "render_predicate",
    "session_setup",
]
