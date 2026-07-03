from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cryo.domain.enums import (
    CostPrecision,
    Mode,
    OperationType,
    PartitionTransform,
    PredicateCapability,
)


@dataclass(frozen=True)
class PartitionPredicateTerm:
    base_column: str
    transform: PartitionTransform
    value: Any
    col_type: str = ""
    transform_param: int | None = None
    partition_field: str | None = None
    unsupported_reason: str | None = None

    @property
    def required_capability(self) -> PredicateCapability:
        if self.transform is PartitionTransform.BUCKET:
            return PredicateCapability.BUCKET_FILTER
        return PredicateCapability.BASIC_COMPARISON


@dataclass(frozen=True)
class PartitionPredicate:
    terms: tuple[PartitionPredicateTerm, ...]
    unsupported_reason: str | None = None

    @property
    def required_capabilities(self) -> set[PredicateCapability]:
        return {term.required_capability for term in self.terms}

    @property
    def is_supported(self) -> bool:
        return self.unsupported_reason is None and all(
            term.unsupported_reason is None and term.transform is not PartitionTransform.UNSUPPORTED
            for term in self.terms
        )

    def unsupported_for(self, capabilities: set[PredicateCapability]) -> str | None:
        if self.unsupported_reason:
            return self.unsupported_reason
        for term in self.terms:
            if term.unsupported_reason:
                return term.unsupported_reason
            if term.transform is PartitionTransform.UNSUPPORTED:
                field_name = term.partition_field or term.base_column
                return f"unsupported partition transform for {field_name}"
        missing = self.required_capabilities - capabilities
        if missing:
            names = ", ".join(sorted(capability.value for capability in missing))
            return f"requires unsupported predicate capability: {names}"
        return None


@dataclass(frozen=True)
class Operation:
    type: OperationType
    params: dict[str, Any] = field(default_factory=dict)
    predicates: tuple[PartitionPredicate, ...] | None = None


@dataclass(frozen=True)
class MaintenancePlan:
    table_ref: str
    mode: Mode
    operations: tuple[Operation, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class StorageBreakdown:
    live_bytes: int
    dead_bytes: int
    metadata_bytes: int
    delete_bytes: int
    orphan_bytes: int | None = None
    listed_bytes: int | None = None


@dataclass(frozen=True)
class SavingsProjection:
    precision: CostPrecision
    current_monthly_cost_usd: float
    projected_monthly_cost_usd: float
    projected_savings_usd_month: float
    reclaimable_bytes: int
    by_operation: dict[OperationType, float]
    query_request_savings_usd_month: float = 0.0
    assumptions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CapacitySnapshot:
    capacity: int
    used: int
    queued: int = 0

    @property
    def available(self) -> int:
        return max(0, self.capacity - max(self.used, self.queued))
