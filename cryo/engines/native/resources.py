"""Smart CPU/memory sizing for native maintenance K8s Jobs.

Estimates per-table resource requests from scanner stats and planned ops.
Hard ceiling: memory requests never exceed 16Gi (16384 MiB).

Chunk size is derived *from* pod memory (not a preset knob): size the pod
from stats first, then ``chunk_size_gb_for_pod_mem`` fills the planner budget
and ``max_bytes_per_group_for_pod_mem`` coalesces rewrite file groups up to a
cap floored at the output target file size (never shreds below one output file).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cryo.domain.enums import OperationType

MAX_POD_MEM_MIB = 16384  # 16Gi — absolute max any pod may request
MAX_POD_CPU_MILLI = 4000

_MIN_MEM_MIB = 512
_MIN_CPU_MILLI = 250

_MEM_BASE_MIB = 512
_CPU_BASE_MILLI = 250

# Rewrite: ~128 MiB per 10 GB of planned chunk / table size, plus file/delete pressure.
_REWRITE_MEM_PER_10GB = 128
_REWRITE_CPU_PER_10GB = 100
_REWRITE_MEM_PER_1K_FILES = 64
_REWRITE_MEM_PER_MAX_PART_FILES = 0.25  # MiB per file in hottest partition
_REWRITE_MEM_PER_DELETE_PCT = 8  # MiB per percentage point of delete files

# Expire / orphan are lighter metadata / listing workloads.
_EXPIRE_MEM_BASE = 256
_EXPIRE_MEM_PER_1K_SNAPSHOTS = 32
_EXPIRE_MEM_PER_1K_MANIFESTS = 16
_ORPHAN_MEM_BASE = 384
_ORPHAN_MEM_PER_10GB = 48
_ORPHAN_MEM_PER_1K_FILES = 24

_HOT_MEM_FRACTION = 0.5
_HOT_SIZE_GB = 500.0
_HOT_MAX_FILES_PER_PARTITION = 50_000

MIN_CHUNK_GB = 1.0
MAX_CHUNK_GB = 500.0

# Rewrite file-group sizing (compressed input bytes per ExecuteCompactionGroup).
# Hard floor = target_file_size_bytes (never shred bins below one output file).
# Packing floor = N× target so the rolling writer amortizes remainder tails
# across multiple full outputs (avoids a small file per tiny group).
# Cap from budget ÷ planning expansion (not a raw budget multiplier):
# run 3483a157 coalesced to 2 GiB groups under slack=4× → ~20 GiB RSS.
_DEFAULT_TARGET_FILE_SIZE_BYTES = 256 * 1024 * 1024
_MAX_BYTES_PER_GROUP_HARD = 8 * 1024 * 1024 * 1024  # 8 GiB
# Peak RSS ≈ compressed_group_bytes × factor (resultsa-class tables ~10×).
_PLANNING_MEMORY_EXPANSION = 8
# Prefer groups that can fill this many target-sized outputs before a remainder.
_PACKING_TARGETS_PER_GROUP = 2

_REWRITE_OPS = frozenset(
    {
        OperationType.OPTIMIZE,
        OperationType.OPTIMIZE.value,
        "rewrite_data_files",
        "optimize",
    }
)
_EXPIRE_OPS = frozenset(
    {
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.EXPIRE_SNAPSHOTS.value,
        "expire_snapshots",
    }
)
_ORPHAN_OPS = frozenset(
    {
        OperationType.REMOVE_ORPHAN_FILES,
        OperationType.REMOVE_ORPHAN_FILES.value,
        "remove_orphan_files",
    }
)


@dataclass(frozen=True)
class ResourceCeilings:
    """Effective ceilings after clamping config to hard maxima."""

    max_cpu_milli: int = MAX_POD_CPU_MILLI
    max_mem_mib: int = MAX_POD_MEM_MIB

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None = None) -> ResourceCeilings:
        cfg = config or {}
        max_cpu = _parse_cpu_milli(
            cfg.get("max_cpu_milli")
            or cfg.get("maxCpu")
            or cfg.get("k8s_max_cpu")
            or MAX_POD_CPU_MILLI
        )
        max_mem = _parse_memory_mib(
            cfg.get("max_memory_mib")
            or cfg.get("maxMemory")
            or cfg.get("k8s_max_memory")
            or MAX_POD_MEM_MIB
        )
        return cls(
            max_cpu_milli=max(_MIN_CPU_MILLI, min(max_cpu, MAX_POD_CPU_MILLI)),
            max_mem_mib=max(_MIN_MEM_MIB, min(max_mem, MAX_POD_MEM_MIB)),
        )


@dataclass(frozen=True)
class TableResourceEstimate:
    cpu_milli: int
    mem_mib: int
    hot: bool
    reasons: tuple[str, ...] = ()
    has_rewrite: bool = False


def rewrite_file_pressure_mib(stats: Mapping[str, Any] | None) -> int:
    """Fixed file/delete overhead subtracted when inverting pod mem → chunk GB."""
    stats = stats or {}
    total_files = _as_int(stats.get("total_files"), 0)
    max_files_per_partition = _as_int(stats.get("max_files_per_partition"), 0)
    delete_file_pct = _as_float(stats.get("delete_file_pct"), 0.0)
    total_delete_files = _as_int(stats.get("total_delete_files"), 0)
    return (
        int(total_files / 1000 * _REWRITE_MEM_PER_1K_FILES)
        + int(max_files_per_partition * _REWRITE_MEM_PER_MAX_PART_FILES)
        + int(delete_file_pct * _REWRITE_MEM_PER_DELETE_PCT)
        + int(total_delete_files / 1000 * 32)
    )


def chunk_size_gb_for_pod_mem(
    mem_mib: int,
    stats: Mapping[str, Any] | None = None,
) -> float:
    """Derive planner chunk_size_gb from a pod memory request (invert rewrite formula).

    Pod requests already include file/delete pressure in ``estimate_table_resources``.
    Invert only the base + rewrite_gb term so chunk fills the data budget:

    ``mem ≈ MEM_BASE + (rewrite_gb/10)*128``  (+ file pressure already in mem)
    → ``rewrite_gb ≈ (mem - MEM_BASE) / 12.8``
    """
    _ = stats  # reserved if we later soft-adjust for extreme file pressure
    capped = min(max(int(mem_mib), _MIN_MEM_MIB), MAX_POD_MEM_MIB)
    usable = max(0, capped - _MEM_BASE_MIB)
    chunk_gb = usable / (_REWRITE_MEM_PER_10GB / 10.0)
    return float(max(MIN_CHUNK_GB, min(MAX_CHUNK_GB, chunk_gb)))


def estimate_table_resources(
    stats: Mapping[str, Any] | None,
    *,
    operations: Sequence[Any] | None = None,
    plan: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
    ceilings: ResourceCeilings | None = None,
) -> TableResourceEstimate:
    """Estimate CPU/mem for one table's planned native maintenance.

    Sizes from table stats (fill toward the pod ceiling); does **not** require
    preset ``chunk_size_gb``. Peak memory is the max across op working sets.
    Result is always clamped to ≤16Gi.
    """
    caps = ceilings or ResourceCeilings()
    stats = stats or {}
    params = params or {}
    op_types = _normalize_operations(operations, plan)

    total_size_gb = _as_float(stats.get("total_size_gb"), 0.0)
    # Provisional rewrite working set: grow with table size up to what fits
    # under the memory ceiling (inverse of the rewrite mem formula, ignoring
    # file pressure for the provisional cap so large tables request near-max).
    max_rewrite_gb_for_ceiling = max(
        MIN_CHUNK_GB,
        (caps.max_mem_mib - _MEM_BASE_MIB) / (_REWRITE_MEM_PER_10GB / 10.0),
    )
    if total_size_gb > 0:
        rewrite_gb = min(total_size_gb, max_rewrite_gb_for_ceiling)
    else:
        rewrite_gb = min(100.0, max_rewrite_gb_for_ceiling)

    total_files = _as_int(stats.get("total_files"), 0)
    max_files_per_partition = _as_int(stats.get("max_files_per_partition"), 0)
    total_snapshots = _as_int(stats.get("total_snapshots"), 0)
    manifest_count = _as_int(stats.get("manifest_count"), 0)
    group_concurrency = max(1, _as_int(params.get("group_concurrency"), 2))

    has_rewrite = bool(op_types & _REWRITE_OPS)
    has_expire = bool(op_types & _EXPIRE_OPS)
    has_orphan = bool(op_types & _ORPHAN_OPS)
    if not op_types:
        has_rewrite = True

    peaks: list[int] = []
    reasons: list[str] = []

    if has_rewrite:
        rewrite_mem = (
            _MEM_BASE_MIB
            + int(rewrite_gb / 10 * _REWRITE_MEM_PER_10GB)
            + rewrite_file_pressure_mib(stats)
        )
        peaks.append(rewrite_mem)
        reasons.append(f"rewrite_gb={rewrite_gb:.1f}")

    if has_expire:
        expire_mem = (
            _EXPIRE_MEM_BASE
            + int(total_snapshots / 1000 * _EXPIRE_MEM_PER_1K_SNAPSHOTS)
            + int(manifest_count / 1000 * _EXPIRE_MEM_PER_1K_MANIFESTS)
        )
        peaks.append(expire_mem)
        reasons.append(f"expire_snapshots={total_snapshots}")

    if has_orphan:
        orphan_mem = (
            _ORPHAN_MEM_BASE
            + int(total_size_gb / 10 * _ORPHAN_MEM_PER_10GB)
            + int(total_files / 1000 * _ORPHAN_MEM_PER_1K_FILES)
        )
        peaks.append(orphan_mem)
        reasons.append(f"orphan_files={total_files}")

    if not peaks:
        peaks.append(_MIN_MEM_MIB)

    raw_mem = max(peaks)
    mem_mib = _clamp(raw_mem, _MIN_MEM_MIB, caps.max_mem_mib)
    if has_rewrite:
        # Ensure group coalesce can keep ≥ packing-floor output file size.
        mem_mib = _clamp(
            mem_mib_for_group_packing(mem_mib, params),
            _MIN_MEM_MIB,
            caps.max_mem_mib,
        )

    raw_cpu = _CPU_BASE_MILLI + int(rewrite_gb / 10 * _REWRITE_CPU_PER_10GB)
    if has_rewrite:
        raw_cpu += int(total_files / 5000 * 50) + (group_concurrency - 1) * 100
    if has_orphan:
        raw_cpu += 50
    cpu_milli = _clamp(raw_cpu, _MIN_CPU_MILLI, caps.max_cpu_milli)

    hot = False
    hot_reasons: list[str] = []
    if has_rewrite:
        if mem_mib >= int(_HOT_MEM_FRACTION * caps.max_mem_mib):
            hot = True
            hot_reasons.append("mem_near_ceiling")
        if total_size_gb >= _HOT_SIZE_GB:
            hot = True
            hot_reasons.append(f"size_gb>={_HOT_SIZE_GB:g}")
        if max_files_per_partition >= _HOT_MAX_FILES_PER_PARTITION:
            hot = True
            hot_reasons.append("hot_partition_files")

    all_reasons = tuple(reasons + hot_reasons)
    assert mem_mib <= MAX_POD_MEM_MIB
    assert cpu_milli <= MAX_POD_CPU_MILLI
    return TableResourceEstimate(
        cpu_milli=cpu_milli,
        mem_mib=mem_mib,
        hot=hot,
        reasons=all_reasons,
        has_rewrite=has_rewrite,
    )


def compute_job_resources(
    size_gb: float,
    *,
    ceilings: ResourceCeilings | None = None,
) -> tuple[int, int]:
    """Backward-compatible wrapper: size from a GB working-set hint."""
    estimate = estimate_table_resources(
        {"total_size_gb": size_gb},
        operations=(OperationType.OPTIMIZE,),
        ceilings=ceilings,
    )
    return estimate.cpu_milli, estimate.mem_mib


def compaction_memory_budget_bytes(mem_mib: int) -> int:
    """In-process Go budget: leave ~25% headroom under the pod request, ≤16Gi."""
    capped = min(mem_mib, MAX_POD_MEM_MIB)
    budget_mib = max(_MIN_MEM_MIB, int(capped * 0.75))
    return budget_mib * 1024 * 1024


def packing_floor_bytes(params: Mapping[str, Any] | None = None) -> int:
    """Minimum coalesce size that keeps output files at/above target size.

    One target is the shred floor; ``_PACKING_TARGETS_PER_GROUP`` targets lets
    the rolling writer finish multiple full files before a remainder tail.
    """
    target = target_file_size_bytes_from_params(params)
    return target * _PACKING_TARGETS_PER_GROUP


def mem_mib_for_group_packing(
    mem_mib: int,
    params: Mapping[str, Any] | None = None,
) -> int:
    """Raise pod mem so ``budget / planning_expansion`` can hold the packing floor.

    Without this bump, small rewrite estimates (e.g. ~682 MiB for a 4.5 GiB
    table) force ``max_bytes_per_group`` down to a single target and produce
    one undersized remainder file per group.
    """
    packing = packing_floor_bytes(params)
    needed_budget = packing * _PLANNING_MEMORY_EXPANSION
    # Invert compaction_memory_budget_bytes: budget_mib = int(mem * 0.75).
    needed_budget_mib = max(_MIN_MEM_MIB, (needed_budget + (1024 * 1024) - 1) // (1024 * 1024))
    needed_mem_mib = int((needed_budget_mib + 0.749999) / 0.75)
    return min(MAX_POD_MEM_MIB, max(int(mem_mib), needed_mem_mib, _MIN_MEM_MIB))


def target_file_size_bytes_from_params(params: Mapping[str, Any] | None = None) -> int:
    """Output target file size used as the floor for rewrite file-group bytes."""
    params = params or {}
    raw = params.get("target_file_size_bytes")
    if raw is not None:
        try:
            value = int(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    file_size_mb = params.get("file_size_mb")
    if file_size_mb is not None:
        try:
            mb = int(file_size_mb)
            if mb > 0:
                return mb * 1024 * 1024
        except (TypeError, ValueError):
            pass
    return _DEFAULT_TARGET_FILE_SIZE_BYTES


def max_bytes_per_group_for_pod_mem(
    mem_mib: int,
    params: Mapping[str, Any] | None = None,
    *,
    chunk_size_gb: float | None = None,
) -> int:
    """Derive rewrite ``max_bytes_per_group`` from pod memory / chunk budget.

    iceberg-go packs candidates toward ``target_file_size_bytes``; Cryo then
    coalesces same-partition bins up to this cap. Rules:

    - Never below ``target_file_size_bytes`` (avoids shredding → small outputs).
    - Prefer at least ``_PACKING_TARGETS_PER_GROUP`` × target when the budget
      allows, so rolling-writer remainders stay rare.
    - Upper-bounded by ``compaction_memory_budget_bytes / planning_expansion``
      so decode expansion cannot turn one group into multi-× pod RSS.
    - Grows with derived ``chunk_size_gb`` and pod compaction budget.
    - Hard-capped at 8 GiB per group for blast-radius / RSS safety.
    """
    params = params or {}
    target = target_file_size_bytes_from_params(params)
    packing = packing_floor_bytes(params)
    chunk_gb = (
        float(chunk_size_gb)
        if chunk_size_gb is not None
        else chunk_size_gb_for_pod_mem(mem_mib)
    )
    chunk_bytes = max(target, int(chunk_gb * (1024**3)))
    budget = compaction_memory_budget_bytes(mem_mib)
    expansion = max(1, _as_int(params.get("memory_expansion_factor"), _PLANNING_MEMORY_EXPANSION))
    # Cap at the planning factor so the admit-gate default (96×) does not force
    # sub-target groups; a lower explicit factor may enlarge the safe byte cap.
    planning_expansion = min(expansion, _PLANNING_MEMORY_EXPANSION)
    safe_bytes = budget // planning_expansion
    # Shred floor = one target; packing floor when memory admits it.
    floor = packing if safe_bytes >= packing else target
    safe_bytes = max(floor, safe_bytes)
    scaled = min(chunk_bytes, safe_bytes, _MAX_BYTES_PER_GROUP_HARD)
    return max(floor, int(scaled))


def derive_chunk_params(
    mem_mib: int,
    stats: Mapping[str, Any] | None,
    base_params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge pod-derived rewrite knobs into planner/execute params.

    Always sets ``chunk_size_gb`` and ``compaction_memory_budget_bytes``.
    Sets ``max_bytes_per_group`` unless the caller already provided an explicit
    override (e.g. dense-table memory tuning).
    """
    merged = dict(base_params or {})
    sized_mem = mem_mib_for_group_packing(mem_mib, merged)
    chunk = chunk_size_gb_for_pod_mem(sized_mem, stats)
    merged["chunk_size_gb"] = chunk
    merged["compaction_memory_budget_bytes"] = compaction_memory_budget_bytes(sized_mem)
    if "max_bytes_per_group" not in merged:
        merged["max_bytes_per_group"] = max_bytes_per_group_for_pod_mem(
            sized_mem, merged, chunk_size_gb=chunk
        )
    return merged


def _normalize_operations(
    operations: Sequence[Any] | None,
    plan: Mapping[str, Any] | None,
) -> set[Any]:
    result: set[Any] = set()
    if operations:
        for op in operations:
            if isinstance(op, OperationType):
                result.add(op)
                result.add(op.value)
            elif isinstance(op, str):
                result.add(op)
            elif isinstance(op, Mapping):
                op_type = op.get("type") or op.get("operation")
                if op_type is not None:
                    result.add(op_type)
            else:
                op_type = getattr(op, "type", None)
                if op_type is not None:
                    result.add(op_type)
                    if isinstance(op_type, OperationType):
                        result.add(op_type.value)
    if plan and not result:
        for op in plan.get("operations") or ():
            if isinstance(op, Mapping):
                op_type = op.get("type")
                if op_type is not None:
                    result.add(op_type)
            elif isinstance(op, str):
                result.add(op)
    return result


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_cpu_milli(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text.endswith("m"):
            return int(float(text[:-1]))
        return int(float(text) * 1000)
    return MAX_POD_CPU_MILLI


def _parse_memory_mib(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text.endswith("gi"):
            return int(float(text[:-2]) * 1024)
        if text.endswith("g"):
            return int(float(text[:-1]) * 1024)
        if text.endswith("mi"):
            return int(float(text[:-2]))
        if text.endswith("m"):
            return int(float(text[:-1]))
        return int(float(text))
    return MAX_POD_MEM_MIB
