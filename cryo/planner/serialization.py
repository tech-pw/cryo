from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from cryo.domain.enums import Mode, OperationType, PartitionTransform
from cryo.domain.models import (
    MaintenancePlan,
    Operation,
    OptimizeTruncation,
    PartitionPredicate,
    PartitionPredicateTerm,
)


def plan_to_dict(plan: MaintenancePlan) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "table_ref": plan.table_ref,
        "mode": plan.mode.value,
        "operations": [_operation_to_dict(operation) for operation in plan.operations],
        "notes": list(plan.notes),
    }
    # Only present when set, so every untruncated plan (and its hash) is unchanged.
    if plan.optimize_truncation is not None:
        payload["optimize_truncation"] = {
            "planned_partitions": plan.optimize_truncation.planned_partitions,
            "candidate_partitions": plan.optimize_truncation.candidate_partitions,
            "max_chunks": plan.optimize_truncation.max_chunks,
        }
    return payload


def plan_from_dict(payload: Mapping[str, Any]) -> MaintenancePlan:
    operations_raw = payload.get("operations")
    if not isinstance(operations_raw, list):
        raise ValueError("Bound plan operations must be a list")

    operations: list[Operation] = []
    for operation_raw in operations_raw:
        if not isinstance(operation_raw, Mapping):
            raise ValueError("Bound plan operation must be an object")
        params_raw = operation_raw.get("params")
        if params_raw is None:
            params: dict[str, Any] = {}
        elif isinstance(params_raw, Mapping):
            params = dict(params_raw)
        else:
            raise ValueError("Bound plan operation params must be an object")

        predicates_raw = operation_raw.get("predicates")
        predicates: tuple[PartitionPredicate, ...] | None
        if predicates_raw is None:
            predicates = None
        elif isinstance(predicates_raw, list):
            predicates = tuple(_predicate_from_dict(item) for item in predicates_raw)
        else:
            raise ValueError("Bound plan predicates must be a list or null")

        operations.append(
            Operation(
                type=OperationType(str(operation_raw.get("type"))),
                params=params,
                predicates=predicates,
            )
        )

    notes_raw = payload.get("notes")
    if notes_raw is None:
        notes: tuple[str, ...] = ()
    elif isinstance(notes_raw, list) and all(isinstance(note, str) for note in notes_raw):
        notes = tuple(notes_raw)
    else:
        raise ValueError("Bound plan notes must be a list of strings")

    table_ref = payload.get("table_ref")
    if not isinstance(table_ref, str) or not table_ref:
        raise ValueError("Bound plan table_ref must be a non-empty string")
    return MaintenancePlan(
        table_ref=table_ref,
        mode=Mode(str(payload.get("mode"))),
        operations=tuple(operations),
        notes=notes,
        optimize_truncation=_truncation_from_dict(payload.get("optimize_truncation")),
    )


def _truncation_from_dict(raw: object) -> OptimizeTruncation | None:
    if not isinstance(raw, Mapping):
        return None
    try:
        return OptimizeTruncation(
            planned_partitions=int(raw["planned_partitions"]),
            candidate_partitions=int(raw["candidate_partitions"]),
            max_chunks=int(raw["max_chunks"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def canonical_plan_bytes(plan: MaintenancePlan) -> bytes:
    return json.dumps(
        plan_to_dict(plan),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    ).encode("utf-8")


def hash_plan(plan: MaintenancePlan) -> str:
    return hashlib.sha256(canonical_plan_bytes(plan)).hexdigest()


def hash_plan_dict(payload: Mapping[str, Any]) -> str:
    return hash_plan(plan_from_dict(payload))


def _operation_to_dict(operation: Operation) -> dict[str, Any]:
    return {
        "type": operation.type.value,
        "params": dict(operation.params),
        "predicates": (
            None
            if operation.predicates is None
            else [_predicate_to_dict(predicate) for predicate in operation.predicates]
        ),
    }


def _predicate_to_dict(predicate: PartitionPredicate) -> dict[str, Any]:
    return {
        "unsupported_reason": predicate.unsupported_reason,
        "terms": [
            {
                "base_column": term.base_column,
                "transform": term.transform.value,
                "value": term.value,
                "col_type": term.col_type,
                "transform_param": term.transform_param,
                "partition_field": term.partition_field,
                "unsupported_reason": term.unsupported_reason,
            }
            for term in predicate.terms
        ],
    }


def _predicate_from_dict(payload: object) -> PartitionPredicate:
    if not isinstance(payload, Mapping):
        raise ValueError("Bound plan predicate must be an object")
    terms_raw = payload.get("terms")
    if not isinstance(terms_raw, list):
        raise ValueError("Bound plan predicate terms must be a list")
    return PartitionPredicate(
        terms=tuple(_term_from_dict(term) for term in terms_raw),
        unsupported_reason=_optional_string(payload.get("unsupported_reason")),
    )


def _term_from_dict(payload: object) -> PartitionPredicateTerm:
    if not isinstance(payload, Mapping):
        raise ValueError("Bound plan predicate term must be an object")
    base_column = payload.get("base_column")
    if not isinstance(base_column, str) or not base_column:
        raise ValueError("Bound plan predicate base_column must be a non-empty string")
    transform_param_raw = payload.get("transform_param")
    transform_param = int(transform_param_raw) if transform_param_raw is not None else None
    return PartitionPredicateTerm(
        base_column=base_column,
        transform=PartitionTransform(str(payload.get("transform"))),
        value=payload.get("value"),
        col_type=str(payload.get("col_type") or ""),
        transform_param=transform_param,
        partition_field=_optional_string(payload.get("partition_field")),
        unsupported_reason=_optional_string(payload.get("unsupported_reason")),
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Bound plan optional text fields must be strings or null")
    return value


def _json_default(value: object) -> object:
    if isinstance(value, set):
        return sorted(value, key=str)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Unsupported canonical plan value: {type(value).__name__}")
