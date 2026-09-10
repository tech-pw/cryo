from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cmp_to_key
from typing import Any

from cryo.domain.enums import Mode, OperationType, PartitionTransform, PredicateCapability
from cryo.domain.models import MaintenancePlan, Operation, PartitionPredicate
from cryo.planner.chunking import greedy_pack
from cryo.planner.guardrails import sort_operations_canonically
from cryo.planner.partitions import (
    DEFAULT_RENDERABLE_CAPABILITIES,
    PartitionFieldSpec,
    build_predicate,
    partition_fields_from_stats,
)

# Tables just under detect_streaming_from_history's 24 commits/day cutoff still
# take concurrent writes during rewrite_data_files (PDAE-8353). Treat that band
# as extra-care: smaller OPTIMIZE chunks and a shorter expire window.
_EXTRA_CARE_COMMITS_PER_DAY = 5.0


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
            operations=_apply_skip_operations(operations, params, notes),
            notes=tuple(notes),
        )

    if resolved_mode in {Mode.LIGHT, Mode.STANDARD, Mode.DEEP}:
        operations.append(
            Operation(
                OperationType.EXPIRE_SNAPSHOTS,
                _expire_params(
                    params,
                    stats,
                    protected_snapshot_ids=list(protected_snapshot_ids or []),
                    notes=notes,
                ),
            )
        )

    if resolved_mode is Mode.DEEP:
        operations.append(Operation(OperationType.REMOVE_ORPHAN_FILES, _orphan_params(params)))

    if resolved_mode in {Mode.STANDARD, Mode.DEEP}:
        optimize_ops = list(
            _apply_skip_operations(
                _optimize_operations(stats, partitions, params, notes), params, notes
            )
        )
        pathological_skew = _has_pathological_delete_skew(partitions, params)
        unreachable_skew = not pathological_skew and _has_unreachable_delete_skew(
            partitions, params
        )
        delete_policy = _delete_compaction_policy(stats, partitions, params)
        if delete_policy == "minor":
            operations.append(_rewrite_position_deletes_operation(params))
            if pathological_skew:
                notes.append(
                    f"Planned rewrite_position_deletes (minor): "
                    f"{int(stats.get('position_delete_files') or 0)} position-delete files "
                    f"concentrated on already-compacted partitions with an extreme delete-per-file "
                    f"ratio -- convert_deletes cannot reach these regardless of delete_file_pct "
                    f"({float(stats.get('delete_file_pct') or 0):.2f}%)."
                )
            elif unreachable_skew:
                notes.append(
                    f"Planned rewrite_position_deletes (minor): "
                    f"{int(stats.get('position_delete_files') or 0)} position-delete files "
                    f"concentrated on partitions whose data files are already reasonably sized -- "
                    f"convert_deletes/optimize select rewrite candidates by file size and would "
                    f"very likely form zero rewrite groups here despite delete_file_pct "
                    f"({float(stats.get('delete_file_pct') or 0):.2f}%) being past the "
                    f"major-rewrite threshold."
                )
            else:
                notes.append(
                    f"Planned rewrite_position_deletes (minor): "
                    f"{int(stats.get('position_delete_files') or 0)} position-delete files, "
                    f"delete_file_pct {float(stats.get('delete_file_pct') or 0):.2f}% below the "
                    f"major-rewrite threshold."
                )
        else:
            convert_params = params
            if delete_policy == "major":
                convert_params = {
                    **params,
                    "force_convert_deletes": True,
                    "convert_deletes_threshold_pct": 0.0,
                }
                notes.append(
                    "Escalated delete maintenance to a major data rewrite after "
                    f"{int(stats.get('delete_minor_runs_since_major') or 0)} successful minor "
                    "delete compactions since the last major rewrite."
                )
            convert_op = _convert_deletes_operation(
                stats,
                partitions,
                convert_params,
                optimize_ops,
                notes,
            )
            if convert_op is not None:
                operations.append(convert_op)
        operations.extend(optimize_ops)

    if resolved_mode is Mode.DEEP:
        operations.append(Operation(OperationType.OPTIMIZE_MANIFESTS, dict(params)))
    elif resolved_mode in {Mode.LIGHT, Mode.STANDARD}:
        # Regression, 2026-07-27: a standard-mode run on
        # reports.gold_dbt_video_stats_reports_unified only ran expire_snapshots + optimize,
        # silently dropping remove_orphan_files and
        # optimize_manifests with zero explanation in plan.notes -- an operator (or an agent)
        # reading the run afterward had no way to tell "skipped by design for this mode" apart
        # from "the planner failed to build them." Both are correctly mode-gated to deep (orphan
        # scanning needs a full S3 LIST; manifest rewrite is a secondary pass) -- just say so.
        notes.append(
            f"remove_orphan_files and optimize_manifests are deep-only and were not planned for "
            f"mode={resolved_mode.value}."
        )

    # Puffin statistics: computed on the freshly-compacted files, before the cleanup steps.
    # Only runs when at least one OPTIMIZE was planned (stats would be stale otherwise).
    if (
        resolved_mode in {Mode.STANDARD, Mode.DEEP}
        and params.get("compute_statistics", False)
        and any(op.type is OperationType.OPTIMIZE for op in operations)
    ):
        operations.append(Operation(OperationType.COMPUTE_STATISTICS, _stats_params(params)))

    # The blocks above append in mode/gating order, not execution order. Sorting here makes
    # guardrails.CORRECT_ORDER the single source of sequencing truth. The sort is stable, so
    # the per-chunk OPTIMIZE operations keep the benefit-score order _optimize_operations gave
    # them (they all share one rank).
    operations = sort_operations_canonically(operations, key=lambda op: op.type)
    return MaintenancePlan(
        table_ref=_table_ref(stats, params),
        mode=resolved_mode,
        operations=_apply_skip_operations(operations, params, notes),
        notes=tuple(notes),
    )


def _apply_skip_operations(
    operations: list[Operation],
    params: Mapping[str, Any],
    notes: list[str],
) -> tuple[Operation, ...]:
    """Drop operations named in params["skip_operations"] (a list of OperationType values).

    Regression, discovered 2026-07-31 and fixed 2026-08-03: Preset.operations has always
    been editable and looked like it controlled what actually runs, but build_plan() never
    consulted it -- only ABAC/PII validation read it. An operator removing expire_snapshots
    from a preset's operations list had no effect on the built plan at all, with no error or
    warning. cryo.worker.worker derives skip_operations from job.operations (itself sourced
    from Preset.operations) before calling build_plan, so this is the one place that
    actually has to honor it.
    """
    skip = {str(s) for s in (params.get("skip_operations") or [])}
    if not skip:
        return tuple(operations)
    kept = [op for op in operations if op.type.value not in skip]
    dropped = [op.type.value for op in operations if op.type.value in skip]
    if dropped:
        notes.append(
            "Skipped operation(s) not in the preset's configured operations list: "
            f"{', '.join(sorted(set(dropped)))}."
        )
    return tuple(kept)


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
    total_size_gb = _total_size_gb(stats)
    configured_chunk_size_gb = float(params.get("chunk_size_gb", 100))
    fields = partition_fields_from_stats(stats, params)

    # Streaming-aware compaction cadence: high-velocity tables fragment faster, so we
    # tighten the chunk size + min_small_files threshold to keep compaction windows small
    # enough that we never run on top of an actively-being-written partition. Caller can
    # opt out by setting `streaming_aware_cadence=false` in params. Detected upstream by
    # `detect_streaming_from_history` (cryo/scanner/signals.py) from snapshot-count
    # deltas across consecutive `table_stats_history` rows.
    #
    # Extra-care band (PDAE-8353): tables just under the 24 commits/day is_streaming
    # cutoff still lose rewrite_data_files to concurrent writers (live:
    # lsq_pw.gold_prospect_activity_extension_base, 14 commits/day, is_streaming False).
    # Apply the same smaller chunks / fewer partitions per chunk without flipping the
    # streaming flag used elsewhere.
    is_streaming = bool(stats.get("is_streaming"))
    commits_per_day = float(stats.get("commits_per_day") or 0.0)
    write_aware = _is_write_aware(stats, params)
    max_parts = int(params.get("max_partitions_per_chunk", 100))
    chunk_size_gb = _effective_chunk_size_gb(configured_chunk_size_gb, write_aware)
    if write_aware:
        original_chunk = configured_chunk_size_gb
        original_min_small = min_small_files
        original_max_parts = max_parts
        min_small_files = max(1, int(min_small_files * 0.75))
        max_parts = max(1, max_parts // 2)
        band = "streaming" if is_streaming else "moderate-write extra-care"
        notes.append(
            f"Streaming-aware cadence: table flagged as {band} "
            f"({commits_per_day:.1f} commits/day) — tightened chunk_size_gb "
            f"{original_chunk:g} → {chunk_size_gb:g}, min_small_files "
            f"{original_min_small} → {min_small_files}, and max_partitions_per_chunk "
            f"{original_max_parts} → {max_parts}."
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
        if total_size_gb is not None and total_size_gb <= chunk_size_gb:
            notes.append(
                "Planned full-table optimize: table is unpartitioned within chunk size budget."
            )
            return [_optimize_operation(params)]
        if total_size_gb is None:
            notes.append("Skipped optimize: table is unpartitioned and total size is unknown.")
        else:
            notes.append(
                "Skipped optimize: unpartitioned table size "
                f"{total_size_gb:g} GB exceeds chunk size budget {chunk_size_gb:g} GB."
            )
        return []

    if total_size_gb is not None and total_size_gb <= chunk_size_gb:
        notes.append("Planned full-table optimize: table is within chunk size budget.")
        return [_optimize_operation(params)]

    # min_partition_small_files: opt-in override for _candidate_partitions' per-partition
    # gate, separate from the table-level min_small_files check above (line ~249).
    # Live-verified, 2026-09-10: gold_dbt_pw_bq has ~5,954 small files total (comfortably
    # over the table-level gate) spread across ~63k partitions at a median of 4/partition
    # -- with the same min_small_files=128 applied per-partition here, only ~63 partitions
    # (the rare ones that happen to concentrate 128+ small files) ever qualify per sweep,
    # so optimize "succeeds" repeatedly (30 successful chunked runs seen in one job's
    # history) while the fleet-wide small_files_count never meaningfully drops -- the
    # other ~62,900+ partitions each sit just under the bar forever. Defaults to
    # min_small_files (today's exact behavior, unchanged for every table that doesn't set
    # this) -- set explicitly low (e.g. 1) on a preset to opt a specific thin-spread table
    # into packing many small partitions into one chunk via greedy_pack below, instead of
    # requiring each partition to individually clear the threshold.
    min_partition_small_files = int(params.get("min_partition_small_files", min_small_files))
    capabilities = _predicate_capabilities(params)
    candidates = _candidate_partitions(
        fields, partitions, min_partition_small_files, capabilities, notes, params
    )
    if not candidates:
        notes.append("Skipped optimize: no renderable partition candidates met the threshold.")
        return []

    sorted_candidates = sorted(candidates, key=cmp_to_key(_compare_candidates))
    chunks = greedy_pack(
        sorted_candidates,
        gb_budget=chunk_size_gb,
        max_parts=max_parts,
        max_chunks=_optional_int(params.get("max_chunks")),
        oversize_alone=True,
    )

    return [
        _optimize_operation(params, tuple(candidate.predicate for candidate in chunk))
        for chunk in chunks
    ]


def full_table_optimize_allowed(
    stats: Mapping[str, Any],
    params: Mapping[str, Any],
) -> bool:
    total_size_gb = _total_size_gb(stats)
    return total_size_gb is not None and total_size_gb <= effective_optimize_chunk_size_gb(
        stats, params
    )


def effective_optimize_chunk_size_gb(
    stats: Mapping[str, Any],
    params: Mapping[str, Any],
) -> float:
    configured_chunk_size_gb = float(params.get("chunk_size_gb", 100))
    return _effective_chunk_size_gb(
        configured_chunk_size_gb,
        _is_write_aware(stats, params),
    )


def _effective_chunk_size_gb(chunk_size_gb: float, write_aware: bool) -> float:
    return max(1.0, chunk_size_gb / 2.0) if write_aware else chunk_size_gb


def _is_write_aware(stats: Mapping[str, Any], params: Mapping[str, Any]) -> bool:
    is_streaming = bool(stats.get("is_streaming"))
    commits_per_day = float(stats.get("commits_per_day") or 0.0)
    streaming_aware = params.get("streaming_aware_cadence", True)
    extra_care = (
        streaming_aware and not is_streaming and commits_per_day >= _EXTRA_CARE_COMMITS_PER_DAY
    )
    return bool(streaming_aware and (is_streaming or extra_care))


def _total_size_gb(stats: Mapping[str, Any]) -> float | None:
    value = stats.get("total_size_gb")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _delete_compaction_policy(
    stats: Mapping[str, Any],
    partitions: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
) -> str | None:
    """Okolnychyi et al. ("Petabyte-Scale Row-Level Operations", VLDB'24): minor position-delete
    compaction is much cheaper than a full major rewrite and restores most of the read-side
    cost of accumulated deletes -- prefer it while the table hasn't crossed into "needs a real
    rewrite" territory.

    Returns ``"minor"`` when a cheap rewrite_position_deletes pass should substitute for
    convert_deletes, ``"major"`` when repeated minor passes require a bounded escalation, or
    ``None`` to defer to the existing convert_deletes gate.
    """
    equality_delete_files = int(stats.get("equality_delete_files") or 0)
    equality_major_threshold = int(params.get("delete_equality_file_threshold", 50))
    if equality_major_threshold > 0 and equality_delete_files >= equality_major_threshold:
        return "major"
    format_version = stats.get("format_version")
    if format_version is not None and int(format_version) >= 3:
        # V3 deletion-vector tables don't accumulate position-delete *files* the same way --
        # DV-aware maintenance is a different lever this policy doesn't implement; do not
        # misapply V2-shaped file-count heuristics to it.
        return None
    if _has_pathological_delete_skew(partitions, params) or _has_unreachable_delete_skew(
        partitions, params
    ):
        # convert_deletes (rewrite_data_files/BIN-PACK) selects candidates by *file size* --
        # even with delete-file-threshold set, it does not reliably form a rewrite group for a
        # partition whose data files are already well-sized but carry an extreme delete-to-data
        # ratio, because the underlying Iceberg action's file-group selection is size-driven.
        # Confirmed live, 2026-07-29: a partition with 7 data files and 11,845 position-delete
        # files produced zero rewrite groups via convert_deletes (removed_delete_files_count=0,
        # three separate attempts across this table's 4 worst partitions), while
        # rewrite_position_delete_files -- run directly against the same table -- cleared all
        # 34,176 dangling deletes in one pass (rewritten_delete_files_count=34176,
        # added_delete_files_count=0: every one of them was fully stale). Once any partition
        # looks like this, prefer minor regardless of overall delete_file_pct -- convert_deletes
        # provably cannot reach it, no matter how high delete_file_pct climbs.
        return "minor"
    minor_runs = int(stats.get("delete_minor_runs_since_major") or 0)
    max_minor_runs = int(params.get("delete_max_minor_runs_before_major", 5))
    hot_query_threshold = params.get("delete_hot_query_count_30d")
    if hot_query_threshold is not None and int(stats.get("query_count_30d") or 0) >= int(
        hot_query_threshold
    ):
        max_minor_runs = int(
            params.get(
                "delete_hot_max_minor_runs_before_major",
                max(1, max_minor_runs // 2),
            )
        )
    if max_minor_runs > 0 and minor_runs >= max_minor_runs:
        return "major"
    delete_pct = float(stats.get("delete_file_pct") or 0.0)
    major_pct = float(params.get("delete_major_pct_threshold", 10.0))
    if delete_pct >= major_pct:
        return None  # already past "cheap fix" territory -- let convert_deletes handle it
    position_delete_files = int(stats.get("position_delete_files") or 0)
    minor_threshold = int(params.get("delete_minor_file_threshold", 50))
    if position_delete_files >= minor_threshold:
        return "minor"
    return None


def _has_pathological_delete_skew(
    partitions: Sequence[Mapping[str, Any]], params: Mapping[str, Any]
) -> bool:
    """True if any partition has so few (already well-sized) data files relative to its delete
    count that rewrite_data_files' size-driven selection cannot reach it -- see
    _delete_compaction_policy for the live incident this codifies."""
    min_ratio = float(params.get("delete_per_file_pathological_ratio", 20.0))
    min_deletes = int(params.get("delete_pathological_min_files", 200))
    for row in partitions:
        deletes = _delete_files(row)
        if deletes < min_deletes:
            continue
        small_files = int(row.get("small_files_in_partition_count") or 0)
        if small_files > 0:
            continue  # data itself is still fragmented -- convert_deletes can still help here
        data_files = int(row.get("files_per_partition") or 0)
        if data_files <= 0:
            continue
        if deletes / data_files >= min_ratio:
            return True
    return False


def _has_unreachable_delete_skew(
    partitions: Sequence[Mapping[str, Any]], params: Mapping[str, Any]
) -> bool:
    """True if any partition's data files are already well-sized (no small-file fragmentation)
    but still carry enough deletes per data file that rewrite_data_files' size-driven candidate
    selection is unlikely to form a rewrite group -- a milder version of
    _has_pathological_delete_skew's "provably zero rewrite groups" case.

    Regression, confirmed live 2026-08-11: mview.gold_dbt_test_results (delete_file_pct=100%,
    a genuinely hot table at 483 queries/30d) has 8 partitions each carrying 150-315
    position-delete files against 20-44 well-sized data files -- ratios of ~2.6 to ~8.0,
    well under _has_pathological_delete_skew's default 20.0 "provably unreachable" bar, but
    still far
    past the point where convert_deletes/optimize's size-driven selection reliably picks up
    these files. That table's own "succeeded" job plan explicitly deferred to optimize
    ("planned optimize already rewrites data files for this table") and never touched the
    deletes -- delete_file_pct was still 100% in the live snapshot afterward. Distinct,
    separately-tunable threshold from the pathological case so a fleet-wide default change
    here can't silently also change the (already-proven, already-tested) pathological
    threshold's behavior.
    """
    # 7.0 sits with real margin on both sides: the live incident this codifies
    # (mview.gold_dbt_test_results) has its highest per-partition ratio at ~8.0 (295/37), and
    # test_convert_deletes_scopes_to_delete_skew_when_data_files_already_compacted (a separate,
    # deliberately-milder fixture for _has_pathological_delete_skew, ratio 20.0) sits at 18.0 --
    # this threshold must never drift close enough to either to make those tests ambiguous.
    min_ratio = float(params.get("delete_per_file_unreachable_ratio", 7.0))
    min_deletes = int(params.get("delete_unreachable_min_files", 100))
    for row in partitions:
        deletes = _delete_files(row)
        if deletes < min_deletes:
            continue
        small_files = int(row.get("small_files_in_partition_count") or 0)
        if small_files > 0:
            continue  # data itself is still fragmented -- convert_deletes can still help here
        data_files = int(row.get("files_per_partition") or 0)
        if data_files <= 0:
            continue
        if deletes / data_files >= min_ratio:
            return True
    return False


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


def _delete_files(row: Mapping[str, Any]) -> int:
    """Read a partition row's delete-file count.

    Bug found live 2026-08-18 while verifying PDAE-8355's deployed fix against real data:
    this read `delete_files_in_partition_count`, a key that has never existed in production.
    Both scanner producers write the count as `delete_files` (`cryo/scanner/analyze.py`'s
    `raw={"partition_values": ..., "delete_files": accum.delete_files}` and
    `cryo/scanner/go_scan.py`'s identical shape) -- so this always read 0/None and silently
    made both `_has_pathological_delete_skew` and `_has_unreachable_delete_skew` dead code in
    prod despite passing every test, because the test fixtures used the same wrong key this
    function read rather than the real one the scanners actually produce. Confirmed live on
    the exact PDAE-8355 incident table (`mview.gold_dbt_test_results`): before this fix,
    `_has_unreachable_delete_skew` returned False against its own real partition data (every
    row's `delete_files` present, non-zero, up to 431 in a single partition) because this
    function couldn't see it under the wrong key.
    """
    return int(row.get("delete_files") or row.get("delete_files_in_partition_count") or 0)


def _skewed_rows_by(
    partitions: Sequence[Mapping[str, Any]],
    *,
    metric: Any,
    multiplier: float,
    min_count: int,
    max_partitions: int,
) -> tuple[list[Mapping[str, Any]], float]:
    # Average over ALL partitions, not just the ones with count > 0: when only a handful of
    # partitions carry any of this metric at all (e.g. delete files concentrated in a few
    # already-data-compacted partitions, everything else genuinely at 0), averaging over just
    # those handful makes them average against themselves and never look "skewed" relative to
    # their own value, no matter how disproportionate they are versus the rest of the table.
    # Confirmed in testing, 2026-07-28: 4 partitions uniformly at 8,000 delete files each,
    # amid 50 partitions at 0, failed the multiplier check when averaged over just the 4.
    if not partitions:
        return [], 0.0
    counts = [(metric(row), row) for row in partitions]
    average = sum(count for count, _ in counts) / len(counts)
    threshold = max(min_count, multiplier * average)
    skewed = sorted(
        (row for count, row in counts if count > 0 and count >= threshold),
        key=metric,
        reverse=True,
    )
    return skewed[:max_partitions], threshold


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

    Scopes by BOTH small-data-file skew and delete-file skew, unioned. These are independent
    signals: a partition can be well-compacted on data files (few, large files -- not skewed
    by small_files_in_partition_count) while still carrying a pile of stale position-delete
    files that no data-file-count heuristic would ever surface. Confirmed live, 2026-07-28: a
    table's 34,176 position-delete files sat in 4 partitions that an earlier optimize pass had
    already reduced to 5-7 data files each -- small-file-only scoping silently starved exactly
    those partitions across every convert_deletes attempt, identical in shape to the 2026-07-22
    incident this function was originally written for, just on the opposite axis.
    """
    if len(partitions) < 2:
        return ()
    fields = partition_fields_from_stats(stats, params)
    if not fields or all(field.transform is PartitionTransform.UNPARTITIONED for field in fields):
        return ()
    skew_multiplier = float(params.get("convert_deletes_skew_multiplier", 3.0))
    min_files = int(params.get("convert_deletes_skew_min_files", 50))
    max_partitions = int(params.get("convert_deletes_skew_max_partitions", 20))
    by_small_files, small_threshold = _skewed_rows_by(
        partitions,
        metric=_small_files,
        multiplier=skew_multiplier,
        min_count=min_files,
        max_partitions=max_partitions,
    )
    min_deletes = int(params.get("convert_deletes_skew_min_delete_files", 50))
    by_delete_files, delete_threshold = _skewed_rows_by(
        partitions,
        metric=_delete_files,
        multiplier=skew_multiplier,
        min_count=min_deletes,
        max_partitions=max_partitions,
    )
    seen: set[str] = set()
    skewed: list[Mapping[str, Any]] = []
    for row in [*by_small_files, *by_delete_files]:
        key = str(row.get("partition_value") or row.get("partition_key") or id(row))
        if key in seen:
            continue
        seen.add(key)
        skewed.append(row)
    if not skewed:
        return ()
    skewed = skewed[:max_partitions]
    predicates = tuple(build_predicate(fields, row) for row in skewed)
    predicates = tuple(p for p in predicates if not any(t.unsupported_reason for t in p.terms))
    if not predicates:
        return ()
    parts_desc = []
    if by_small_files:
        parts_desc.append(f"{len(by_small_files)} by small-file count (>= {small_threshold:.0f})")
    if by_delete_files:
        parts_desc.append(
            f"{len(by_delete_files)} by delete-file count (>= {delete_threshold:.0f})"
        )
    notes.append(
        f"Scoped convert_deletes to {len(predicates)} skewed partition(s) "
        f"({', '.join(parts_desc)}, {skew_multiplier:g}x table average) instead of a "
        "table-wide rewrite."
    )
    return predicates


def _expire_params(
    params: Mapping[str, Any],
    stats: Mapping[str, Any],
    *,
    protected_snapshot_ids: list[int] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    retention_days = int(params.get("retention_days", 7))

    # Streaming-aware retention: a table this actively written accumulates new snapshots
    # faster than a long retention window ever clears them, no matter how compaction-heavy
    # the rest of the run is -- confirmed live, 2026-08-03: unified_bq.gold_dbt_pw_bq went
    # from 666 to 2,137 snapshots *during* a single 2.5-day maintenance run, since
    # expire_snapshots can only remove snapshots older than the window, not ones created
    # while the job itself was still running. Tightening the window at least bounds how far
    # back irrelevant history persists between runs. Same opt-out and detection signal as
    # _optimize_operations' streaming-aware chunk sizing (detect_streaming_from_history).
    # This mitigates but does not fully solve unbounded growth for extremely high-velocity
    # tables -- that needs a recurring schedule (Preset.schedule_cron), not a shorter window
    # on a one-shot run, which is why the note below says so explicitly.
    is_streaming = bool(stats.get("is_streaming"))
    commits_per_day = float(stats.get("commits_per_day") or 0.0)
    streaming_aware = params.get("streaming_aware_cadence", True)
    extra_care = (
        streaming_aware and not is_streaming and commits_per_day >= _EXTRA_CARE_COMMITS_PER_DAY
    )
    if streaming_aware and commits_per_day > 0 and (is_streaming or extra_care):
        original_retention = retention_days
        if is_streaming and commits_per_day >= 100:
            retention_days = 1
        elif commits_per_day >= 20 or extra_care:
            retention_days = max(1, min(retention_days, 3))
        if retention_days != original_retention and notes is not None:
            band = "streaming" if is_streaming else "moderate-write extra-care"
            notes.append(
                f"Streaming-aware retention: table flagged as {band} "
                f"({commits_per_day:.1f} commits/day) — tightened retention_days "
                f"{original_retention} → {retention_days} so expire_snapshots can keep pace. "
                "A table this active will keep re-accumulating snapshots between runs; a "
                "recurring schedule (Preset.schedule_cron) gives more control than a "
                "shorter window on a one-shot run."
            )

    # Backlog-aware retention: is_streaming/commits_per_day only reacts to *current* write
    # velocity, so a table that was bursty in the past (or commits just under the streaming
    # detection cutoff -- confirmed live, 2026-08-10: admin.iceberg_maintenance_log sits at
    # 22.15 commits/day against a 24.0 threshold, never flagged, sitting on 499 snapshots) or
    # that has simply never had expire_snapshots reach it before never gets tightened, no
    # matter how large the accumulated backlog already is. This reacts to the backlog itself
    # (total_snapshots, an absolute count, not a rate) so it catches both cases the streaming
    # check misses. Purely additive to the streaming check above via min() -- never loosens
    # whatever the streaming-aware pass already decided.
    total_snapshots = int(stats.get("total_snapshots") or 0)
    backlog_aware = params.get("backlog_aware_cadence", True)
    backlog_threshold = int(params.get("snapshot_backlog_threshold", 100))
    if backlog_aware and total_snapshots >= backlog_threshold:
        original_retention = retention_days
        if total_snapshots >= backlog_threshold * 4:
            retention_days = min(retention_days, 1)
        else:
            retention_days = max(1, min(retention_days, 3))
        if retention_days != original_retention and notes is not None:
            notes.append(
                f"Backlog-aware retention: table already carries {total_snapshots} snapshots "
                f"(>= {backlog_threshold}) regardless of current write velocity — tightened "
                f"retention_days {original_retention} → {retention_days} to clear the backlog "
                "over the next few scheduled runs rather than waiting on the streaming-velocity "
                "signal alone."
            )

    out: dict[str, Any] = {
        "retention_days": retention_days,
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
    canonical = row.get("small_files_in_partition_count")
    if canonical is not None:
        return int(canonical)
    legacy = row.get("small_files")
    return int(legacy) if legacy is not None else 0


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
