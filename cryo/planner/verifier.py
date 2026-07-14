"""Plan Verifier (arXiv:2510.09567, "Proof-Carrying AI Agents").

Statically checks a ``MaintenancePlan`` against the live catalog and engine capabilities before
it is enqueued for execution. Inspired by proof-carrying code: every plan carries a SHA-256 hash
of its canonical JSON form, and the verifier produces a list of ``invariants_checked`` plus any
``violations`` so downstream callers (the API, the worker) can reject or warn about plans that
reference partitions that don't exist, columns the table doesn't have, ops the destination
engine can't run, or chunk sizes that would blow up Trino.

Invariants are pluggable: see ``VerifierRegistry``. New invariants register themselves at import
time so this file rarely needs to grow when a new check is added.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.domain.enums import OperationType, PartitionTransform
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    PartitionPredicate,
    PartitionPredicateTerm,
)

if TYPE_CHECKING:
    from cryo.config import Settings

logger = logging.getLogger(__name__)

Severity = Literal["critical", "warning"]
VERIFIER_VERSION = "1.0.0"

# Cap matches the chunking guard in cryo/planner/chunking.py -- past 256 partitions in a single
# chunk the Trino FilterNode becomes expensive and queries time out.
DEFAULT_MAX_PARTITIONS_PER_CHUNK = 256

# Default per-chunk rewrite budget in bytes. Matches cryo/planner/chunking.py::DEFAULT_BUDGET.
DEFAULT_COMPACTION_BUDGET_BYTES = 8 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class PlanViolation:
    invariant: str
    severity: Severity
    message: str
    location: str


@dataclass(frozen=True)
class PlanVerdict:
    verified: bool
    plan_hash: str
    verifier_version: str
    invariants_checked: tuple[str, ...] = ()
    violations: tuple[PlanViolation, ...] = ()


@dataclass
class InvariantContext:
    plan: MaintenancePlan
    session: Session
    settings: Settings
    violations: list[PlanViolation] = field(default_factory=list)
    known_columns_by_table: dict[str, set[str]] = field(default_factory=dict)
    known_partitions_by_table: dict[str, set[str]] = field(default_factory=dict)
    verifier: PlanVerifier | None = None

    def record(self, violation: PlanViolation) -> None:
        self.violations.append(violation)


InvariantFn = Callable[[InvariantContext], None]


# ----- plan hashing -----


def canonical_plan_bytes(plan: MaintenancePlan) -> bytes:
    return json.dumps(_plan_to_dict(plan), sort_keys=True, separators=(",", ":")).encode("utf-8")


def hash_plan(plan: MaintenancePlan) -> str:
    return hashlib.sha256(canonical_plan_bytes(plan)).hexdigest()


def _plan_to_dict(plan: MaintenancePlan) -> dict[str, Any]:
    return {
        "table_ref": plan.table_ref,
        "mode": str(plan.mode),
        "operations": [_operation_to_dict(op) for op in plan.operations],
        "notes": list(plan.notes),
    }


def _operation_to_dict(op: Operation) -> dict[str, Any]:
    return {
        "type": str(op.type),
        "params": _sorted_dict(op.params),
        "predicates": [_predicate_to_dict(pred) for pred in (op.predicates or ())],
    }


def _predicate_to_dict(pred: PartitionPredicate) -> dict[str, Any]:
    return {
        "terms": [_term_to_dict(term) for term in pred.terms],
        "unsupported_reason": pred.unsupported_reason,
    }


def _term_to_dict(term: PartitionPredicateTerm) -> dict[str, Any]:
    return {
        "base_column": term.base_column,
        "transform": str(term.transform),
        "value": _jsonable(term.value),
        "col_type": term.col_type,
        "transform_param": term.transform_param,
        "partition_field": term.partition_field,
        "unsupported_reason": term.unsupported_reason,
    }


def _sorted_dict(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    return {k: _jsonable(v) for k, v in sorted(value.items())}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, set):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ----- registry -----


class VerifierRegistry:
    """Holds the ordered list of invariants. Add new invariants via ``register``; remove by name
    via ``deregister``. ``PlanVerifier`` walks the registry on each ``verify`` call."""

    _INSTANCES: dict[str, VerifierRegistry] = {}

    def __init__(self, name: str) -> None:
        self.name = name
        self._invariants: list[tuple[str, InvariantFn]] = []

    @classmethod
    def get(cls, name: str) -> VerifierRegistry:
        if name not in cls._INSTANCES:
            cls._INSTANCES[name] = cls(name)
        return cls._INSTANCES[name]

    def register(self, invariant_name: str, fn: InvariantFn) -> None:
        for existing_name, _ in self._invariants:
            if existing_name == invariant_name:
                raise ValueError(f"Invariant {invariant_name!r} already registered")
        self._invariants.append((invariant_name, fn))

    def deregister(self, invariant_name: str) -> None:
        self._invariants = [(name, fn) for name, fn in self._invariants if name != invariant_name]

    def invariants(self) -> list[tuple[str, InvariantFn]]:
        return list(self._invariants)

    def names(self) -> list[str]:
        return [name for name, _ in self._invariants]


# ----- verifier -----


class PlanVerifier:
    """Runs every registered invariant against a plan + live DB session."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        registry: VerifierRegistry | None = None,
        *,
        max_partitions_per_chunk: int = DEFAULT_MAX_PARTITIONS_PER_CHUNK,
        compaction_budget_bytes: int = DEFAULT_COMPACTION_BUDGET_BYTES,
    ) -> None:
        self.session = session
        self.settings = settings
        self.registry = registry or VerifierRegistry.get("default")
        self.max_partitions_per_chunk = max_partitions_per_chunk
        self.compaction_budget_bytes = compaction_budget_bytes

    def verify(self, plan: MaintenancePlan) -> PlanVerdict:
        ctx = InvariantContext(
            plan=plan, session=self.session, settings=self.settings, verifier=self
        )
        # Catalog preload must never 500 the enqueue path: multi-catalog fleets often have
        # the same schema.table in more than one source, and name resolution can be ambiguous.
        try:
            ctx = self._load_catalog_context(ctx)
        except Exception:
            logger.exception(
                "Plan verifier catalog preload failed for %s; continuing without scan context",
                plan.table_ref,
            )
        for name, fn in self.registry.invariants():
            try:
                fn(ctx)
            except Exception:
                logger.exception("Invariant %s crashed; recording as critical violation", name)
                ctx.record(
                    PlanViolation(
                        invariant=name,
                        severity="critical",
                        message="Invariant raised an unexpected exception (see server logs).",
                        location=f"plan:{plan.table_ref}",
                    )
                )
        return PlanVerdict(
            verified=not any(v.severity == "critical" for v in ctx.violations),
            plan_hash=hash_plan(plan),
            verifier_version=VERIFIER_VERSION,
            invariants_checked=tuple(name for name, _ in self.registry.invariants()),
            violations=tuple(ctx.violations),
        )

    # ----- catalog preload -----

    def _load_catalog_context(self, ctx: InvariantContext) -> InvariantContext:
        table = _resolve_table(ctx.session, ctx.plan.table_ref)
        if table is None:
            return ctx
        table_key = str(table.id)
        ctx.known_columns_by_table[table_key] = _scan_columns(ctx.session, table.id)
        ctx.known_partitions_by_table[table_key] = _scan_partitions(ctx.session, table.id)
        ctx.known_columns_by_table[ctx.plan.table_ref] = ctx.known_columns_by_table[table_key]
        ctx.known_partitions_by_table[ctx.plan.table_ref] = ctx.known_partitions_by_table[table_key]
        return ctx


# ----- built-in invariants -----


def _invariant_partitions_exist(ctx: InvariantContext) -> None:
    table = _resolve_table(ctx.session, ctx.plan.table_ref)
    if table is None:
        return  # other invariants report this
    known = ctx.known_partitions_by_table.get(str(table.id)) or set()
    for index, op in enumerate(ctx.plan.operations):
        if not op.predicates:
            continue
        for pred_index, predicate in enumerate(op.predicates):
            for term_index, term in enumerate(predicate.terms):
                if term.partition_field is None and term.transform is PartitionTransform.IDENTITY:
                    value = str(term.value)
                    if value not in known:
                        ctx.record(
                            PlanViolation(
                                invariant="all_partitions_exist",
                                severity="critical",
                                message=(
                                    f"Partition '{value}' is not in the latest scan snapshot "
                                    "for this table. Re-scan or remove the predicate."
                                ),
                                location=(
                                    f"plan:{ctx.plan.table_ref}#op{index}."
                                    f"predicate{pred_index}.term{term_index}"
                                ),
                            )
                        )


def _invariant_columns_exist(ctx: InvariantContext) -> None:
    table = _resolve_table(ctx.session, ctx.plan.table_ref)
    if table is None:
        return
    known = ctx.known_columns_by_table.get(str(table.id)) or set()
    if not known:
        return
    for index, op in enumerate(ctx.plan.operations):
        if not op.predicates:
            continue
        for pred_index, predicate in enumerate(op.predicates):
            for term_index, term in enumerate(predicate.terms):
                if term.base_column and term.base_column not in known:
                    ctx.record(
                        PlanViolation(
                            invariant="all_columns_exist",
                            severity="critical",
                            message=(
                                f"Column '{term.base_column}' is not present in this table's "
                                "schema or column_quality stats."
                            ),
                            location=(
                                f"plan:{ctx.plan.table_ref}#op{index}."
                                f"predicate{pred_index}.term{term_index}"
                            ),
                        )
                    )


def _invariant_operation_ordering(ctx: InvariantContext) -> None:
    from cryo.planner.guardrails import CORRECT_ORDER

    canonical_rank = {op: idx for idx, op in enumerate(CORRECT_ORDER)}
    last_rank = -1
    for index, op in enumerate(ctx.plan.operations):
        rank = canonical_rank.get(op.type)
        if rank is None:
            continue
        if rank < last_rank:
            ctx.record(
                PlanViolation(
                    invariant="operation_ordering",
                    severity="warning",
                    message=(
                        f"Operation {op.type.value} should run after earlier maintenance steps "
                        "(expire → orphan → optimize → manifests)."
                    ),
                    location=f"plan:{ctx.plan.table_ref}#op{index}",
                )
            )
        last_rank = max(last_rank, rank)


def _invariant_non_negative_costs(ctx: InvariantContext) -> None:
    for index, op in enumerate(ctx.plan.operations):
        for key, value in op.params.items():
            if not _is_cost_like(key):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number < 0:
                ctx.record(
                    PlanViolation(
                        invariant="non_negative_costs",
                        severity="critical",
                        message=(
                            f"Cost estimate '{key}' is negative ({number}); engine drivers "
                            "reject negative budgets."
                        ),
                        location=f"plan:{ctx.plan.table_ref}#op{index}.params.{key}",
                    )
                )


_COST_KEYWORDS = ("cost", "bytes", "budget", "estimated_size", "rewrite_size", "rewritten_bytes")


def _is_cost_like(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _COST_KEYWORDS)


def _invariant_supported_operations(ctx: InvariantContext) -> None:
    SPARK_OPS: frozenset[Any] = getattr(
        __import__("cryo.engines.spark", fromlist=["SUPPORTED_OPS"]),
        "SUPPORTED_OPS",
        frozenset(),
    )
    TRINO_OPS: frozenset[Any] = getattr(
        __import__("cryo.engines.trino", fromlist=["SUPPORTED_OPS"]),
        "SUPPORTED_OPS",
        frozenset(),
    )
    combined = set(TRINO_OPS) | set(SPARK_OPS)
    for index, op in enumerate(ctx.plan.operations):
        if combined and op.type not in combined:
            ctx.record(
                PlanViolation(
                    invariant="supported_operations",
                    severity="critical",
                    message=(
                        f"Operation {op.type.value} is not implemented by any registered engine "
                        f"(supported: {sorted(str(o) for o in combined)})."
                    ),
                    location=f"plan:{ctx.plan.table_ref}#op{index}",
                )
            )


def _invariant_no_predicate_on_partition_col_in_optimize(ctx: InvariantContext) -> None:
    for index, op in enumerate(ctx.plan.operations):
        if op.type is not OperationType.OPTIMIZE:
            continue
        if not op.predicates:
            continue
        for pred_index, predicate in enumerate(op.predicates):
            non_partition = [
                term
                for term in predicate.terms
                if term.transform is PartitionTransform.IDENTITY and term.partition_field is None
            ]
            if non_partition:
                continue
            ctx.record(
                PlanViolation(
                    invariant="no_predicate_on_partition_col_in_optimize",
                    severity="warning",
                    message=(
                        "OPTIMIZE predicates must reference a partition column "
                        "(transform != identity or partition_field set); otherwise Trino/Spark "
                        "fall back to a full table scan."
                    ),
                    location=f"plan:{ctx.plan.table_ref}#op{index}.predicate{pred_index}",
                )
            )


def _invariant_max_partitions_per_chunk(ctx: InvariantContext) -> None:
    cap = (
        ctx.verifier.max_partitions_per_chunk
        if ctx.verifier is not None
        else DEFAULT_MAX_PARTITIONS_PER_CHUNK
    )
    for index, op in enumerate(ctx.plan.operations):
        if not op.predicates:
            continue
        for pred_index, predicate in enumerate(op.predicates):
            if len(predicate.terms) > cap:
                ctx.record(
                    PlanViolation(
                        invariant="max_partitions_per_chunk",
                        severity="critical",
                        message=(
                            f"Predicate references {len(predicate.terms)} partitions, exceeding "
                            f"the per-chunk cap of {cap}. Split into smaller chunks."
                        ),
                        location=f"plan:{ctx.plan.table_ref}#op{index}.predicate{pred_index}",
                    )
                )


def _invariant_compaction_budget(ctx: InvariantContext) -> None:
    budget = (
        ctx.verifier.compaction_budget_bytes
        if ctx.verifier is not None
        else DEFAULT_COMPACTION_BUDGET_BYTES
    )
    total = 0
    for op in ctx.plan.operations:
        if op.type is not OperationType.OPTIMIZE:
            continue
        for key in ("rewrite_size", "estimated_size", "rewritten_bytes", "bytes"):
            if key in op.params:
                try:
                    total += int(op.params[key])
                except (TypeError, ValueError):
                    continue
                break
    if total > budget:
        ctx.record(
            PlanViolation(
                invariant="compaction_budget",
                severity="warning",
                message=(
                    f"Total OPTIMIZE rewrite size ({total} bytes) exceeds configured budget "
                    f"({budget} bytes)."
                ),
                location=f"plan:{ctx.plan.table_ref}",
            )
        )


# ----- registry bootstrap -----


_DEFAULT_REGISTRY = VerifierRegistry.get("default")
_DEFAULT_REGISTRY.register("all_partitions_exist", _invariant_partitions_exist)
_DEFAULT_REGISTRY.register("all_columns_exist", _invariant_columns_exist)
_DEFAULT_REGISTRY.register("operation_ordering", _invariant_operation_ordering)
_DEFAULT_REGISTRY.register("non_negative_costs", _invariant_non_negative_costs)
_DEFAULT_REGISTRY.register("supported_operations", _invariant_supported_operations)
_DEFAULT_REGISTRY.register(
    "no_predicate_on_partition_col_in_optimize",
    _invariant_no_predicate_on_partition_col_in_optimize,
)
_DEFAULT_REGISTRY.register("max_partitions_per_chunk", _invariant_max_partitions_per_chunk)
_DEFAULT_REGISTRY.register("compaction_budget", _invariant_compaction_budget)


# ----- catalog helpers -----


def _resolve_table(session: Session, table_ref: str) -> Any:
    from cryo.db.model_utils import catalog_name_for_table
    from cryo.db.models import Table

    if not table_ref:
        return None
    # table_ref is conventionally "schema.table" or "catalog.schema.table"; the Planner also
    # stores it that way. We try to find a row by name first, then by id-as-string.
    #
    # Multi-catalog deployments (e.g. cdp + cdp_dev) routinely share the same schema.table
    # across sources. Never use scalar_one_or_none() here — MultipleResultsFound becomes an
    # unhandled 500 on POST /maintenance/run.
    parts = table_ref.split(".")
    if len(parts) >= 2:
        schema_name = parts[-2]
        table_name = parts[-1]
        catalog = parts[0] if len(parts) >= 3 else None
        rows = list(
            session.execute(
                select(Table).where(
                    Table.schema_name == schema_name, Table.table_name == table_name
                )
            )
            .scalars()
            .all()
        )
        if len(rows) == 1:
            return rows[0]
        if len(rows) > 1 and catalog is not None:
            for row in rows:
                try:
                    if catalog_name_for_table(row) == catalog:
                        return row
                except Exception:
                    continue
            return None
        if len(rows) > 1:
            logger.warning(
                "Ambiguous table_ref %r matches %d rows; pass catalog.schema.table",
                table_ref,
                len(rows),
            )
            return None
    try:
        from uuid import UUID

        row = session.get(Table, UUID(table_ref))
        if row is not None:
            return row
    except (ValueError, TypeError):
        return None
    return None


def _scan_columns(session: Session, table_id: Any) -> set[str]:
    from cryo.db.models import TableStats

    stats = session.get(TableStats, table_id)
    if stats is None or not stats.raw:
        return set()
    raw = stats.raw or {}
    column_quality = raw.get("column_quality")
    if isinstance(column_quality, dict):
        return {str(name) for name in column_quality.keys()}
    columns = raw.get("columns")
    if isinstance(columns, list):
        return {str(name) for name in columns if isinstance(name, (str, int))}
    return set()


def _scan_partitions(session: Session, table_id: Any) -> set[str]:
    from cryo.db.models import TablePartition

    rows = (
        session.execute(
            select(TablePartition.partition_value).where(TablePartition.table_id == table_id)
        )
        .scalars()
        .all()
    )
    return {str(value) for value in rows if value is not None}


# ----- re-export -----


__all__ = [
    "DEFAULT_COMPACTION_BUDGET_BYTES",
    "DEFAULT_MAX_PARTITIONS_PER_CHUNK",
    "InvariantContext",
    "InvariantFn",
    "PlanVerdict",
    "PlanVerifier",
    "PlanViolation",
    "Severity",
    "VERIFIER_VERSION",
    "VerifierRegistry",
    "canonical_plan_bytes",
    "hash_plan",
]


def _ensure_iterable(value: Iterable[Any] | None) -> list[Any]:
    return list(value) if value is not None else []
