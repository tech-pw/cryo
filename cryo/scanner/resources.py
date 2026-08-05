"""Resource sizing for per-chunk ad-hoc scan Kubernetes Jobs.

Mirrors cryo.engines.native.resources's shape (size from real stats, hard
ceilings, floor), but scan cost is dominated by manifest/file count -- see
cryo/scanner/analyze.py's iter_data_files -- not by raw table size, so the
formula scales off those instead of total_size_gb.

Sizing source, in priority order:
1. The target table's last-known TableStats row (O(1) point read by table_id)
   -- exactly the fields estimate_table_resources already relies on for
   rewrite jobs.
2. A cheap manifest-list-only estimate for a never-scanned table: reading
   just the manifest list (snapshot pointer + lengths) is the same "Tier-1
   metadata" primitive analyze.py already uses to gate its expensive
   historical-walk path (see analyze.py's account_metadata_cost), never the
   full data-file iteration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cryo.engines.native.resources import ResourceCeilings

_MIN_MEM_MIB = 512
_MIN_CPU_MILLI = 250

_MEM_BASE_MIB = 256
_CPU_BASE_MILLI = 250

# A scan visits every manifest and every data file entry once (see
# analyze.py::iter_data_files) -- cost scales with both counts, plus a smaller
# term for partition skew (the hottest partition drives peak in-memory
# per-partition accumulator size).
_MEM_PER_1K_FILES = 48
_MEM_PER_1K_MANIFESTS = 96
_MEM_PER_MAX_PART_FILES = 0.15
_CPU_PER_1K_FILES = 40

# Never-scanned table fallback: size off manifest-list length alone. This is
# a rough prior (average entries/manifest observed across the fleet), not a
# measured value -- it only has to avoid under-provisioning badly, since the
# very next scan will have real TableStats to size from instead.
_ASSUMED_ENTRIES_PER_MANIFEST = 500


@dataclass(frozen=True)
class ScanResourceEstimate:
    cpu_milli: int
    mem_mib: int
    reasons: tuple[str, ...] = ()


def estimate_scan_resources(
    prior_stats: Mapping[str, Any] | None,
    *,
    manifest_count_hint: int | None = None,
    ceilings: ResourceCeilings | None = None,
) -> ScanResourceEstimate:
    """Estimate CPU/mem for one scan chunk's Kubernetes Job.

    ``prior_stats`` should carry the same keys TableStats persists
    (total_files, manifest_count, max_files_per_partition); pass None for a
    table with no prior scan, optionally with ``manifest_count_hint`` from a
    cheap manifest-list read.
    """
    caps = ceilings or ResourceCeilings()
    reasons: list[str] = []

    if prior_stats:
        total_files = _as_int(prior_stats.get("total_files"), 0)
        manifest_count = _as_int(prior_stats.get("manifest_count"), 0)
        max_files_per_partition = _as_int(prior_stats.get("max_files_per_partition"), 0)
        reasons.append(f"prior_total_files={total_files}")
        reasons.append(f"prior_manifest_count={manifest_count}")
    else:
        manifest_count = max(0, int(manifest_count_hint or 0))
        total_files = manifest_count * _ASSUMED_ENTRIES_PER_MANIFEST
        max_files_per_partition = 0
        reasons.append(f"cold_manifest_hint={manifest_count}")

    raw_mem = (
        _MEM_BASE_MIB
        + int(total_files / 1000 * _MEM_PER_1K_FILES)
        + int(manifest_count / 1000 * _MEM_PER_1K_MANIFESTS)
        + int(max_files_per_partition * _MEM_PER_MAX_PART_FILES)
    )
    raw_cpu = _CPU_BASE_MILLI + int(total_files / 1000 * _CPU_PER_1K_FILES)

    mem_mib = _clamp(raw_mem, _MIN_MEM_MIB, caps.max_mem_mib)
    cpu_milli = _clamp(raw_cpu, _MIN_CPU_MILLI, caps.max_cpu_milli)

    return ScanResourceEstimate(cpu_milli=cpu_milli, mem_mib=mem_mib, reasons=tuple(reasons))


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
