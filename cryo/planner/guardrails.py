from __future__ import annotations

from collections.abc import Mapping, Sequence

from cryo.domain.enums import OperationType

CORRECT_ORDER: tuple[OperationType, ...] = (
    OperationType.EXPIRE_SNAPSHOTS,
    OperationType.REMOVE_ORPHAN_FILES,
    OperationType.OPTIMIZE,
    OperationType.OPTIMIZE_MANIFESTS,
)


def validate_operation_order(operations: Sequence[OperationType | str]) -> list[str]:
    """Return warnings if operations are requested out of the canonical maintenance order."""
    resolved = [OperationType(op) if not isinstance(op, OperationType) else op for op in operations]
    warnings: list[str] = []
    last_index = -1
    for operation in resolved:
        try:
            index = CORRECT_ORDER.index(operation)
        except ValueError:
            continue
        if index < last_index:
            warnings.append(
                f"Operation {operation.value} should run after earlier maintenance steps; "
                "recommended order is expire → orphan → optimize → manifests."
            )
        last_index = max(last_index, index)
    return warnings


def is_safe_manual_sequence(operations: Sequence[OperationType | str]) -> bool:
    return not validate_operation_order(operations)


def validate_guardrails(guardrails: Mapping[str, object]) -> list[str]:
    """Return validation errors for preset guardrails JSON."""
    errors: list[str] = []
    int_keys = (
        "cooldown_hours",
        "sla_freshness_hours",
        "sla_maintenance_days",
        "sla_p95_latency_ms",
    )
    for key in int_keys:
        if key not in guardrails:
            continue
        value = guardrails[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"guardrails.{key} must be a number")
            continue
        if int(value) < 0:
            errors.append(f"guardrails.{key} must be non-negative")
    return errors
