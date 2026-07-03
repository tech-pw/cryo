from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Any

from cryo.domain.enums import Mode, OperationType, PartitionTransform, PredicateCapability
from cryo.domain.models import MaintenancePlan, Operation, PartitionPredicate
from cryo.planner.chunking import greedy_pack
from cryo.planner.partitions import (
    DEFAULT_RENDERABLE_CAPABILITIES,
    PartitionFieldSpec,
    build_predicate,
    partition_fields_from_stats,
)


@dataclass(frozen=True)
class OptimizeCandidate:
    value: str
    size_gb: float
    small_files: int
    predicate: PartitionPredicate


def build_plan(
    stats: Mapping[str, Any],
    partitions: Sequence[Mapping[str, Any]],
    mode: Mode | str,
    params: Mapping[str, Any] | None = None,
) -> MaintenancePlan:
    params = params or {}
    resolved_mode = Mode(mode)
    operations: list[Operation] = []
    notes: list[str] = []

    if resolved_mode in {Mode.LIGHT, Mode.STANDARD, Mode.DEEP}:
        operations.append(Operation(OperationType.EXPIRE_SNAPSHOTS, _expire_params(params)))

    if resolved_mode is Mode.DEEP:
        operations.append(Operation(OperationType.REMOVE_ORPHAN_FILES, _orphan_params(params)))

    if resolved_mode in {Mode.STANDARD, Mode.DEEP}:
        operations.extend(_optimize_operations(stats, partitions, params, notes))

    if resolved_mode is Mode.DEEP:
        operations.append(Operation(OperationType.OPTIMIZE_MANIFESTS, dict(params)))

    return MaintenancePlan(
        table_ref=_table_ref(stats, params),
        mode=resolved_mode,
        operations=tuple(operations),
        notes=tuple(notes),
    )


def _optimize_operations(
    stats: Mapping[str, Any],
    partitions: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    notes: list[str],
) -> list[Operation]:
    min_small_files = int(params.get("min_small_files", 128))
    table_small_files = int(stats.get("small_files_count") or stats.get("table_small_files") or 0)
    total_size_gb = float(stats.get("total_size_gb") or 0.0)
    chunk_size_gb = float(params.get("chunk_size_gb", 100))
    fields = partition_fields_from_stats(stats, params)

    if table_small_files < min_small_files:
        notes.append(
            "Skipped optimize: table has "
            f"{table_small_files} small files below threshold {min_small_files}."
        )
        return []

    if not fields or all(field.transform is PartitionTransform.UNPARTITIONED for field in fields):
        notes.append("Planned full-table optimize: table is unpartitioned.")
        return [_optimize_operation(params)]

    if total_size_gb <= chunk_size_gb:
        notes.append("Planned full-table optimize: table is within chunk size budget.")
        return [_optimize_operation(params)]

    capabilities = _predicate_capabilities(params)
    candidates = _candidate_partitions(fields, partitions, min_small_files, capabilities, notes)
    if not candidates:
        notes.append("Skipped optimize: no renderable partition candidates met the threshold.")
        return []

    sorted_candidates = sorted(candidates, key=cmp_to_key(_compare_candidates))
    chunks = greedy_pack(
        sorted_candidates,
        gb_budget=chunk_size_gb,
        max_parts=int(params.get("max_partitions_per_chunk", 100)),
        max_chunks=_optional_int(params.get("max_chunks")),
        oversize_alone=True,
    )

    return [
        _optimize_operation(params, tuple(candidate.predicate for candidate in chunk))
        for chunk in chunks
    ]


def _candidate_partitions(
    fields: Sequence[PartitionFieldSpec],
    partitions: Sequence[Mapping[str, Any]],
    min_small_files: int,
    capabilities: set[PredicateCapability],
    notes: list[str],
) -> list[OptimizeCandidate]:
    candidates: list[OptimizeCandidate] = []
    for row in partitions:
        small_files = _small_files(row)
        if small_files < min_small_files:
            continue

        predicate = build_predicate(fields, row)
        unsupported_reason = predicate.unsupported_for(capabilities)
        if unsupported_reason:
            partition_value = row.get("partition_value", row.get("partition_values", "<unknown>"))
            notes.append(f"Skipped partition {partition_value}: {unsupported_reason}.")
            continue

        candidates.append(
            OptimizeCandidate(
                value=_sort_value(row),
                size_gb=_partition_size_gb(row),
                small_files=small_files,
                predicate=predicate,
            )
        )
    return candidates


def _optimize_operation(
    params: Mapping[str, Any],
    predicates: tuple[PartitionPredicate, ...] | None = None,
) -> Operation:
    return Operation(OperationType.OPTIMIZE, dict(params), predicates)


def _expire_params(params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "retention_days": int(params.get("retention_days", 7)),
        "retain_last": int(params.get("retain_last", 1)),
    }


def _orphan_params(params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "retention_days": int(params.get("orphan_retention_days", params.get("retention_days", 7))),
    }


def _table_ref(stats: Mapping[str, Any], params: Mapping[str, Any]) -> str:
    if params.get("table_ref"):
        return str(params["table_ref"])
    if stats.get("table_ref"):
        return str(stats["table_ref"])
    catalog = params.get("catalog") or stats.get("catalog") or "iceberg"
    schema = stats.get("schema_name") or stats.get("schema")
    table = stats.get("table_name") or stats.get("table")
    if not schema or not table:
        raise ValueError("stats must include table_ref or schema_name/table_name")
    return f"{catalog}.{schema}.{table}"


def _predicate_capabilities(params: Mapping[str, Any]) -> set[PredicateCapability]:
    raw = params.get("predicate_capabilities")
    if raw is None:
        return set(DEFAULT_RENDERABLE_CAPABILITIES)
    return {
        capability
        if isinstance(capability, PredicateCapability)
        else PredicateCapability(str(capability))
        for capability in raw
    }


def _small_files(row: Mapping[str, Any]) -> int:
    return int(
        row.get("small_files_in_partition_count")
        or row.get("small_files")
        or row.get("files_per_partition")
        or 0
    )


def _partition_size_gb(row: Mapping[str, Any]) -> float:
    return float(row.get("partition_size_gb") or row.get("size_gb") or 0.0)


def _sort_value(row: Mapping[str, Any]) -> str:
    value = row.get("partition_value", row.get("partition_values", ""))
    return str(value)


def _compare_candidates(left: OptimizeCandidate, right: OptimizeCandidate) -> int:
    if left.small_files != right.small_files:
        return -1 if left.small_files > right.small_files else 1
    if left.size_gb != right.size_gb:
        return -1 if left.size_gb < right.size_gb else 1
    if left.value == right.value:
        return 0
    return -1 if left.value > right.value else 1


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None
