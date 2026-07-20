from cryo.domain.enums import OperationType
from cryo.engines.trino.catalog_bindings import (
    CatalogBinding,
    destination_serves_source,
    parse_catalog_bindings,
    resolve_trino_catalog,
    validate_catalog_bindings,
)
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
        OperationType.CONVERT_DELETES,
        OperationType.OPTIMIZE,
        OperationType.OPTIMIZE_MANIFESTS,
    }
)

__all__ = [
    "SUPPORTED_OPS",
    "CatalogBinding",
    "TrinoEngine",
    "UnsupportedPredicateError",
    "destination_serves_source",
    "parse_catalog_bindings",
    "render",
    "render_predicate",
    "resolve_trino_catalog",
    "session_setup",
    "validate_catalog_bindings",
]
