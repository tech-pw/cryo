from __future__ import annotations

from typing import Any

from cryo.domain.models import MaintenancePlan, Operation


def plan_to_payload(plan: MaintenancePlan) -> dict[str, Any]:
    return {
        "table_ref": plan.table_ref,
        "mode": plan.mode.value,
        "operations": [_operation_payload(operation) for operation in plan.operations],
    }


def _operation_payload(operation: Operation) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": operation.type.value,
        "params": dict(operation.params),
    }
    if operation.predicates:
        payload["predicates"] = [
            {
                "terms": [
                    {
                        "base_column": term.base_column,
                        "transform": term.transform.value,
                        "value": term.value,
                        "col_type": term.col_type,
                    }
                    for term in predicate.terms
                ]
            }
            for predicate in operation.predicates
        ]
    return payload
