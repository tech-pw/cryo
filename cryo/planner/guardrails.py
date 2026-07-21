from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeGuard

from cryo.domain.enums import OperationType
from cryo.domain.predicates import LEAF_OPERATORS, SUPPORTED_FIELDS

CORRECT_ORDER: tuple[OperationType, ...] = (
    OperationType.EXPIRE_SNAPSHOTS,
    OperationType.REMOVE_ORPHAN_FILES,
    OperationType.CONVERT_DELETES,
    OperationType.REWRITE_POSITION_DELETES,
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
                "recommended order is expire → orphan → convert_deletes → optimize → manifests."
            )
        last_index = max(last_index, index)
    return warnings


def is_safe_manual_sequence(operations: Sequence[OperationType | str]) -> bool:
    return not validate_operation_order(operations)


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_non_negative_number(errors: list[str], prefix: str, value: object) -> None:
    if not _is_number(value):
        errors.append(f"{prefix} must be a number")
        return
    if float(value) < 0:
        errors.append(f"{prefix} must be non-negative")


def _validate_positive_number(errors: list[str], prefix: str, value: object) -> None:
    if not _is_number(value):
        errors.append(f"{prefix} must be a number")
        return
    if float(value) < 1:
        errors.append(f"{prefix} must be at least 1")


def _validate_string_list(errors: list[str], prefix: str, value: object) -> None:
    if value is None:
        return
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        errors.append(f"{prefix} must be a list of non-empty strings")


def _validate_routing_rules(errors: list[str], value: object) -> None:
    if value is None:
        return
    rules = [value] if isinstance(value, Mapping) else value
    if not isinstance(rules, list):
        errors.append("guardrails.routing_rules must be a list")
        return
    for index, rule in enumerate(rules):
        prefix = f"guardrails.routing_rules[{index}]"
        if not isinstance(rule, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        predicate = rule.get("predicate") or rule.get("when")
        destination = rule.get("destination") or rule.get("destination_name")
        if not isinstance(predicate, Mapping):
            errors.append(f"{prefix}.predicate must be an object")
            continue
        field_name = predicate.get("field")
        if not isinstance(field_name, str) or field_name not in SUPPORTED_FIELDS:
            errors.append(f"{prefix}.predicate.field is unsupported")
            continue
        op = str(predicate.get("op") or "=").lower()
        if op not in LEAF_OPERATORS:
            errors.append(f"{prefix}.predicate.op is unsupported")
            continue
        if "value" not in predicate:
            errors.append(f"{prefix}.predicate.value is required")
            continue
        if not isinstance(destination, str) or not destination:
            errors.append(f"{prefix}.destination must be a non-empty string")


def validate_params(params: Mapping[str, object]) -> list[str]:
    """Return validation errors for known preset params keys."""
    errors: list[str] = []
    non_negative_keys = (
        "min_small_files",
        "delete_ratio_trigger",
        "partition_write_idle_hours",
        # 0 = unlimited (bytes-only split); positive = optional soft file ceiling.
        "max_files_per_group",
    )
    positive_keys = (
        "max_partitions_per_chunk",
        "max_chunks",
        "retention_days",
        "retain_last",
        "orphan_retention_days",
        "file_size_mb",
        "group_concurrency",
        "compaction_memory_budget_bytes",
        "memory_expansion_factor",
        "read_batch_size",
        "parquet_row_group_limit",
        "parquet_row_group_bytes",
        "scan_concurrency",
        "max_bytes_per_group",
        "min_input_files",
        "target_file_size_bytes",
        # Optional leftover; native derives chunk at runtime. Still validate if present.
        "chunk_size_gb",
    )
    for key in non_negative_keys:
        if key not in params:
            continue
        _validate_non_negative_number(errors, f"params.{key}", params[key])
    for key in positive_keys:
        if key not in params:
            continue
        _validate_positive_number(errors, f"params.{key}", params[key])
    if "compression_level" in params:
        _validate_non_negative_number(
            errors,
            "params.compression_level",
            params["compression_level"],
        )
    if "delete_ratio_trigger" in params and _is_number(params["delete_ratio_trigger"]):
        ratio = float(params["delete_ratio_trigger"])
        if ratio > 100:
            errors.append("params.delete_ratio_trigger must be at most 100")
    for key in ("compute_statistics", "stats_compute_ndv", "partial_progress"):
        if key in params and not isinstance(params[key], bool):
            errors.append(f"params.{key} must be a boolean")
    if "metrics_mode" in params and not isinstance(params["metrics_mode"], str):
        errors.append("params.metrics_mode must be a string")
    if "compression_codec" in params and not isinstance(params["compression_codec"], str):
        errors.append("params.compression_codec must be a string")
    _validate_string_list(errors, "params.stats_columns", params.get("stats_columns"))
    _validate_string_list(errors, "params.bloom_filter_columns", params.get("bloom_filter_columns"))
    return errors


def validate_guardrails(guardrails: Mapping[str, object]) -> list[str]:
    """Return validation errors for preset guardrails JSON."""
    errors: list[str] = []
    int_keys = (
        "cooldown_hours",
        "sla_freshness_hours",
        "sla_maintenance_days",
        "sla_p95_latency_ms",
        "deadline_minutes",
    )
    for key in int_keys:
        if key not in guardrails:
            continue
        value = guardrails[key]
        if key == "deadline_minutes":
            _validate_positive_number(errors, f"guardrails.{key}", value)
            continue
        _validate_non_negative_number(errors, f"guardrails.{key}", value)

    if "adaptive_routing" in guardrails and not isinstance(guardrails["adaptive_routing"], bool):
        errors.append("guardrails.adaptive_routing must be a boolean")

    engine_pool = guardrails.get("engine_pool")
    if engine_pool is not None:
        pool = [engine_pool] if isinstance(engine_pool, str) else engine_pool
        if not isinstance(pool, list) or any(
            not isinstance(item, str) or not item for item in pool
        ):
            errors.append("guardrails.engine_pool must be a list of non-empty strings")

    preferences = guardrails.get("engine_preferences")
    if preferences is not None:
        if not isinstance(preferences, Mapping):
            errors.append("guardrails.engine_preferences must be an object")
        else:
            for key in ("spark_min_gb", "native_min_gb", "latency_p99_ms"):
                if key in preferences:
                    _validate_non_negative_number(
                        errors, f"guardrails.engine_preferences.{key}", preferences[key]
                    )

    _validate_routing_rules(errors, guardrails.get("routing_rules"))
    return errors
