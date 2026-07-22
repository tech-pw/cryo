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
    benefit_score: float = 0.0  # AutoComp score; 0 = not computed (fall back to small_files)
    # AutoComp §A2 MOOP scalarization (autocomp_traits["s_c"]); can be negative (T2 cost is
    # subtracted), so None -- not a 0/positive sentinel -- means "not computed".
    moop_score: float | None = None


def build_plan(
    stats: Mapping[str, Any],
    partitions: Sequence[Mapping[str, Any]],
    mode: Mode | str,
    params: Mapping[str, Any] | None = None,
    *,
    protected_snapshot_ids: Sequence[int] | None = None,
) -> MaintenancePlan:
    params = params or {}
    resolved_mode = Mode(mode)
    operations: list[Operation] = []
    notes: list[str] = []

    if resolved_mode is Mode.MANIFEST_ONLY:
        operations.append(Operation(OperationType.OPTIMIZE_MANIFESTS, dict(params)))
        return MaintenancePlan(
            table_ref=_table_ref(stats, params),
            mode=resolved_mode,
            operations=tuple(operations),
            notes=tuple(notes),
        )

    if resolved_mode in {Mode.LIGHT, Mode.STANDARD, Mode.DEEP}:
        operations.append(
            Operation(
                OperationType.EXPIRE_SNAPSHOTS,
                _expire_params(
                    params, protected_snapshot_ids=list(protected_snapshot_ids or [])
                ),
            )
        )

    if resolved_mode is Mode.DEEP:
        operations.append(Operation(OperationType.REMOVE_ORPHAN_FILES, _orphan_params(params)))

    if resolved_mode in {Mode.STANDARD, Mode.DEEP}:
        optimize_ops = _optimize_operations(stats, partitions, params, notes)
        if _delete_compaction_policy(stats, params) == "minor":
            operations.append(_rewrite_position_deletes_operation(params))
            notes.append(
                f"Planned rewrite_position_deletes (minor): "
                f"{int(stats.get('position_delete_files') or 0)} position-delete files, "
                f"delete_file_pct {float(stats.get('delete_file_pct') or 0):.2f}% below the "
                f"major-rewrite threshold."
            )
        else:
            convert_op = _convert_deletes_operation(stats, partitions, params, optimize_ops, notes)
            if convert_op is not None:
                operations.append(convert_op)
        operations.extend(optimize_ops)

    if resolved_mode is Mode.DEEP:
        operations.append(Operation(OperationType.OPTIMIZE_MANIFESTS, dict(params)))

    # Puffin statistics: always last, after manifests, so new file stats are current.
    # Only runs when at least one OPTIMIZE was planned (stats would be stale otherwise).
    if (
        resolved_mode in {Mode.STANDARD, Mode.DEEP}
        and params.get("compute_statistics", False)
        and any(op.type is OperationType.OPTIMIZE for op in operations)
    ):
        operations.append(Operation(OperationType.COMPUTE_STATISTICS, _stats_params(params)))

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
    delete_ratio_trigger = float(params.get("delete_ratio_trigger", 0))
    delete_file_pct = float(stats.get("delete_file_pct") or 0)
    table_small_files = int(stats.get("small_files_count") or stats.get("table_small_files") or 0)
    total_size_gb = float(stats.get("total_size_gb") or 0.0)
    chunk_size_gb = float(params.get("chunk_size_gb", 100))
    fields = partition_fields_from_stats(stats, params)

    # Streaming-aware compaction cadence: high-velocity tables fragment faster, so we
    # tighten the chunk size + min_small_files threshold to keep compaction windows small
    # enough that we never run on top of an actively-being-written partition. Caller can
    # opt out by setting `streaming_aware_cadence=false` in params. Detected upstream by
    # `detect_streaming_from_history` (cryo/scanner/signals.py) from snapshot-count
    # deltas across consecutive `table_stats_history` rows.
    is_streaming = bool(stats.get("is_streaming"))
    commits_per_day = float(stats.get("commits_per_day") or 0.0)
    streaming_aware = params.get("streaming_aware_cadence", True)
    if is_streaming and streaming_aware:
        original_chunk = chunk_size_gb
        original_min_small = min_small_files
        chunk_size_gb = max(1.0, chunk_size_gb / 2.0)
        min_small_files = max(1, int(min_small_files * 0.75))
        notes.append(
            f"Streaming-aware cadence: table flagged as streaming "
            f"({commits_per_day:.1f} commits/day) — tightened chunk_size_gb "
            f"{original_chunk:g} → {chunk_size_gb:g} and min_small_files "
            f"{original_min_small} → {min_small_files}."
        )

    delete_triggered = delete_ratio_trigger > 0 and delete_file_pct >= delete_ratio_trigger
    if table_small_files < min_small_files and not delete_triggered:
        notes.append(
            "Skipped optimize: table has "
            f"{table_small_files} small files below threshold {min_small_files}."
        )
        return []
    if delete_triggered and table_small_files < min_small_files:
        notes.append(
            f"Planned optimize due to delete_file_pct {delete_file_pct}% "
            f">= delete_ratio_trigger {delete_ratio_trigger}%."
        )

    if not fields or all(field.transform is PartitionTransform.UNPARTITIONED for field in fields):
        notes.append("Planned full-table optimize: table is unpartitioned.")
        return [_optimize_operation(params)]

    if total_size_gb <= chunk_size_gb:
        notes.append("Planned full-table optimize: table is within chunk size budget.")
        return [_optimize_operation(params)]

    capabilities = _predicate_capabilities(params)
    candidates = _candidate_partitions(
        fields, partitions, min_small_files, capabilities, notes, params
    )
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
    params: Mapping[str, Any] | None = None,
) -> list[OptimizeCandidate]:
    params = params or {}
    idle_hours = int(params.get("partition_write_idle_hours") or 0)
    candidates: list[OptimizeCandidate] = []
    for row in partitions:
        if idle_hours > 0 and not _partition_is_cold(row, idle_hours):
            partition_value = row.get("partition_value", row.get("partition_values", "<unknown>"))
            notes.append(
                f"Skipped hot partition {partition_value}: writes within last {idle_hours}h."
            )
            continue
        small_files = _small_files(row)
        if small_files < min_small_files:
            continue

        predicate = build_predicate(fields, row)
        unsupported_reason = predicate.unsupported_for(capabilities)
        if unsupported_reason:
            partition_value = row.get("partition_value", row.get("partition_values", "<unknown>"))
            notes.append(f"Skipped partition {partition_value}: {unsupported_reason}.")
            continue

        benefit_score = float(row.get("compaction_benefit_score") or 0.0)
        candidates.append(
            OptimizeCandidate(
                value=_sort_value(row),
                size_gb=_partition_size_gb(row),
                small_files=small_files,
                predicate=predicate,
                benefit_score=benefit_score,
                moop_score=_moop_score(row),
            )
        )
    return candidates


def _optimize_operation(
    params: Mapping[str, Any],
    predicates: tuple[PartitionPredicate, ...] | None = None,
) -> Operation:
    return Operation(OperationType.OPTIMIZE, dict(params), predicates)


def _delete_compaction_policy(stats: Mapping[str, Any], params: Mapping[str, Any]) -> str | None:
    """Okolnychyi et al. ("Petabyte-Scale Row-Level Operations", VLDB'24): minor position-delete
    compaction is much cheaper than a full major rewrite and restores most of the read-side
    cost of accumulated deletes -- prefer it while the table hasn't crossed into "needs a real
    rewrite" territory.

    Returns ``"minor"`` when a cheap rewrite_position_deletes pass should substitute for
    convert_deletes this cycle, or ``None`` to defer to the existing convert_deletes gate
    (its own delete_file_pct threshold decides "major" from there -- this function only ever
    claims the "minor" branch, so it stays purely additive to prior behavior).
    """
    format_version = stats.get("format_version")
    if format_version is not None and int(format_version) >= 3:
        # V3 deletion-vector tables don't accumulate position-delete *files* the same way --
        # DV-aware maintenance is a different lever this policy doesn't implement; do not
        # misapply V2-shaped file-count heuristics to it.
        return None
    delete_pct = float(stats.get("delete_file_pct") or 0.0)
    major_pct = float(params.get("delete_major_pct_threshold", 10.0))
    if delete_pct >= major_pct:
        return None  # already past "cheap fix" territory -- let convert_deletes handle it
    position_delete_files = int(stats.get("position_delete_files") or 0)
    minor_threshold = int(params.get("delete_minor_file_threshold", 50))
    if position_delete_files >= minor_threshold:
        return "minor"
    return None


def _rewrite_position_deletes_operation(params: Mapping[str, Any]) -> Operation:
    return Operation(
        OperationType.REWRITE_POSITION_DELETES,
        {
            "delete_file_threshold": int(params.get("convert_deletes_file_threshold", 1)),
            "partial_progress_enabled": bool(params.get("convert_deletes_partial_progress", True)),
        },
    )


def _convert_deletes_operation(
    stats: Mapping[str, Any],
    partitions: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    optimize_ops: Sequence[Operation],
    notes: list[str],
) -> Operation | None:
    enabled = bool(params.get("enable_convert_deletes", True))
    if not enabled:
        return None
    delete_files = int(stats.get("total_delete_files") or 0)
    if delete_files <= 0:
        return None
    delete_pct = float(stats.get("delete_file_pct") or 0.0)
    threshold = float(params.get("convert_deletes_threshold_pct", 5.0))
    if delete_pct < threshold:
        return None
    if optimize_ops and not bool(params.get("force_convert_deletes", False)):
        notes.append(
            "Skipped convert_deletes: planned optimize already rewrites data files for this table."
        )
        return None
    convert_params = {
        "delete_file_threshold": int(params.get("convert_deletes_file_threshold", 1)),
        "partial_progress_enabled": bool(params.get("convert_deletes_partial_progress", True)),
    }
    predicates = _skewed_partition_predicates(stats, partitions, params, notes)
    notes.append(
        f"Planned convert_deletes: delete_file_pct {delete_pct:.2f}% >= threshold {threshold:.2f}%."
    )
    return Operation(OperationType.CONVERT_DELETES, convert_params, predicates=predicates or None)


def _skewed_partition_predicates(
    stats: Mapping[str, Any],
    partitions: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    notes: list[str],
) -> tuple[PartitionPredicate, ...]:
    """Scope convert_deletes to disproportionately fragmented partitions when present.

    rewrite_data_files self-selects file groups across the WHOLE table using its own
    binpack/size heuristics when given no `where` clause. On a heavily skewed table that
    silently starves the worst partitions run after run: confirmed live, 2026-07-22, a
    200-partition table averaging ~19 files/partition but with 10+ partitions holding
    700-791 files each had convert_deletes touch the same 46 files and remove zero deletes
    across three separate attempts, including one with 4x the Spark executors -- the skewed
    partitions were never reached. Only kicks in when a real skew is detected; otherwise
    returns empty and the call stays table-wide (unchanged prior behavior).
    """
    if len(partitions) < 2:
        return ()
    fields = partition_fields_from_stats(stats, params)
    if not fields or all(field.transform is PartitionTransform.UNPARTITIONED for field in fields):
        return ()
    counts = [(_small_files(row), row) for row in partitions]
    counts = [(count, row) for count, row in counts if count > 0]
    if not counts:
        return ()
    total = sum(count for count, _ in counts)
    average = total / len(counts)
    skew_multiplier = float(params.get("convert_deletes_skew_multiplier", 3.0))
    min_files = int(params.get("convert_deletes_skew_min_files", 50))
    skew_threshold = max(min_files, skew_multiplier * average)
    skewed = sorted(
        (row for count, row in counts if count >= skew_threshold),
        key=lambda row: _small_files(row),
        reverse=True,
    )
    if not skewed:
        return ()
    max_partitions = int(params.get("convert_deletes_skew_max_partitions", 20))
    skewed = skewed[:max_partitions]
    predicates = tuple(build_predicate(fields, row) for row in skewed)
    predicates = tuple(p for p in predicates if not any(t.unsupported_reason for t in p.terms))
    if not predicates:
        return ()
    notes.append(
        f"Scoped convert_deletes to {len(predicates)} skewed partition(s) "
        f"(>= {skew_threshold:.0f} small files, {skew_multiplier:g}x the {average:.0f}-file "
        "table average) instead of a table-wide rewrite."
    )
    return predicates


def _expire_params(
    params: Mapping[str, Any],
    *,
    protected_snapshot_ids: list[int] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "retention_days": int(params.get("retention_days", 7)),
        "retain_last": int(params.get("retain_last", 1)),
    }
    if protected_snapshot_ids:
        out["protected_snapshot_ids"] = sorted(set(int(s) for s in protected_snapshot_ids))
    return out


def _orphan_params(params: Mapping[str, Any]) -> dict[str, Any]:
    days = int(params.get("orphan_retention_days", params.get("retention_days", 7)))
    return {
        "retention_days": days,
        "orphan_min_age_hours": days * 24,
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


def _moop_score(row: Mapping[str, Any]) -> float | None:
    traits = row.get("autocomp_traits")
    if not isinstance(traits, Mapping):
        return None
    s_c = traits.get("s_c")
    return float(s_c) if s_c is not None else None


def _compare_candidates(left: OptimizeCandidate, right: OptimizeCandidate) -> int:
    # Prefer MOOP (AutoComp §A2) when both sides have it -- it's the paper's actual ranking
    # signal (file-count reduction + entropy + query affinity, minus compute cost), not just
    # small_files x frequency.
    if left.moop_score is not None and right.moop_score is not None:
        if left.moop_score != right.moop_score:
            return -1 if left.moop_score > right.moop_score else 1
    elif left.benefit_score > 0 and right.benefit_score > 0:
        if left.benefit_score != right.benefit_score:
            return -1 if left.benefit_score > right.benefit_score else 1
    else:
        # Fall back to small_files DESC (original behavior)
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


def _stats_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Build parameters for the COMPUTE_STATISTICS operation."""
    return {
        # Columns to compute stats for; empty = all columns
        "columns": params.get("stats_columns", []),
        # Whether to build Bloom filter indexes (high-cardinality columns only)
        "bloom_filter_columns": params.get("bloom_filter_columns", []),
        # NDV sketch via DataSketches Theta (always when supported)
        "compute_ndv": params.get("stats_compute_ndv", True),
    }


def _partition_is_cold(row: Mapping[str, Any], idle_hours: int) -> bool:
    from datetime import UTC, datetime, timedelta

    last_write = row.get("last_write_at") or row.get("updated_at")
    if last_write is None:
        return True
    if isinstance(last_write, datetime):
        observed = last_write if last_write.tzinfo else last_write.replace(tzinfo=UTC)
    else:
        try:
            observed = datetime.fromisoformat(str(last_write).replace("Z", "+00:00"))
        except ValueError:
            return True
    return observed <= datetime.now(UTC) - timedelta(hours=idle_hours)
