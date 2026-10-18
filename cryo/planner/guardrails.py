from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TypeGuard, cast

from cryo.domain.enums import OperationType
from cryo.domain.predicates import LEAF_OPERATORS, SUPPORTED_FIELDS

# Canonical maintenance order. Compact first, then rewrite manifests, then clean up -- so a
# single run reclaims the space its own compaction freed, instead of expiring while the
# pre-compaction data files are still live and waiting a whole cycle for the next pass.
#
# COMPUTE_STATISTICS sits right after OPTIMIZE: Puffin blobs reference specific data files, so
# stats must be computed on the post-compaction files, and before the cleanup steps.
#
# This tuple is the ONLY place the order is defined. ``build_plan`` sorts by it rather than
# hand-appending in sequence, and ``web/src/lib/operationOrder.ts`` mirrors it for the UI --
# keep the two in sync.
CORRECT_ORDER: tuple[OperationType, ...] = (
    OperationType.CONVERT_DELETES,
    OperationType.REWRITE_POSITION_DELETES,
    OperationType.OPTIMIZE,
    OperationType.COMPUTE_STATISTICS,
    OperationType.OPTIMIZE_MANIFESTS,
    OperationType.EXPIRE_DATA,
    OperationType.EXPIRE_SNAPSHOTS,
    OperationType.REMOVE_ORPHAN_FILES,
)

_CANONICAL_RANK: dict[OperationType, int] = {op: idx for idx, op in enumerate(CORRECT_ORDER)}


def canonical_rank(operation: OperationType | str) -> int:
    """Sort rank for an operation. Unknown/future ops sink to the end rather than raising."""
    try:
        return _CANONICAL_RANK[OperationType(operation)]
    except (ValueError, KeyError):
        return len(CORRECT_ORDER)


def canonical_order_phrase() -> str:
    """Human-readable canonical order, derived from CORRECT_ORDER so it cannot drift."""
    return "recommended order is " + " → ".join(op.value for op in CORRECT_ORDER)


def sort_operations_canonically[T](
    operations: Iterable[T],
    *,
    key: Callable[[T], OperationType | str] | None = None,
) -> list[T]:
    """Stable-sort operations into CORRECT_ORDER.

    Stable, so entries sharing a rank keep their input order -- notably the N per-chunk
    OPTIMIZE operations, whose relative order encodes their benefit-score ranking.

    ``key`` extracts the operation type when the items are not themselves operation values
    (e.g. ``key=lambda op: op.type`` for ``Operation`` objects).
    """
    resolve: Callable[[T], OperationType | str] = (
        key if key is not None else cast("Callable[[T], OperationType | str]", lambda item: item)
    )
    return sorted(operations, key=lambda item: canonical_rank(resolve(item)))


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
                f"{canonical_order_phrase()}."
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
        # Opt-in override of min_small_files for _candidate_partitions' per-partition gate
        # (cryo/planner/planner.py's _optimize_operations) -- see that param's own comment
        # for the thin-spread-partitions problem this exists to fix. Defaults to
        # min_small_files when absent, so omitting it is a no-op.
        "min_partition_small_files",
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
        # Spark rewrite_data_files min-file-size-bytes; the batch path pins it to the
        # scanner's small-file threshold (cryo/engines/spark/sql.py::_render_optimize).
        "min_file_size_mb",
        "min_file_size_bytes",
        # Spark max-file-size-bytes as a multiple of the target (batch: 10).
        "max_file_size_ratio",
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
    for key in (
        "compute_statistics",
        "stats_compute_ndv",
        "partial_progress",
        "remove_dangling_deletes",
        "stream_results",
    ):
        if key in params and not isinstance(params[key], bool):
            errors.append(f"params.{key} must be a boolean")
    if "rewrite_job_order" in params and params["rewrite_job_order"] not in (
        "bytes-asc",
        "bytes-desc",
        "files-asc",
        "files-desc",
        "none",
    ):
        errors.append(
            "params.rewrite_job_order must be one of bytes-asc, bytes-desc, files-asc, "
            "files-desc, none"
        )
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
