"""WAIR's workload-aware reclustering policy (paper §4.2 Algorithm 2, §4.3 Eq. 4).

Files are sorted by their summed savings over the window, and the prefix (``cut``) with the
lowest ``α·cost_r(prefix) - Σ savings(prefix)`` is chosen; it is reclustered only if that value
is negative (savings beat α times the cost) and its cost fits ``cost_limit``.

Recluster cost is CPU seconds for Cryo's Spark lane to sort-rewrite those bytes, from the
measured rewrite rate (GiB/hour per lane) times the lane's vCPUs. The paper models cost as a
function of aggregate size (in-memory vs external sort); a linear rate is Cryo's simplification.
The paper's cost limit bounds accumulated debt from realized savings; Cryo does not yet measure
realized savings, so the limit caps each recommendation instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cryo.optimizer.wair.boundary import BoundaryFile

GIB = 1024**3


@dataclass(frozen=True, slots=True)
class WairCostParams:
    rewrite_gb_per_hour: float = 25.0
    lane_vcpus: float = 4.0
    aggressiveness: float = 1.0
    cost_limit_cpu_seconds: float | None = None

    def recluster_cpu_seconds(self, size_bytes: int) -> float:
        return size_bytes / GIB / self.rewrite_gb_per_hour * 3600 * self.lane_vcpus


@dataclass(frozen=True, slots=True)
class BoundarySelection:
    selected: tuple[BoundaryFile, ...]
    selected_bytes: int
    savings_cpu_seconds: float
    recluster_cpu_seconds: float
    net_cpu_seconds: float
    clustering_column: str | None


def select_boundary_files(
    boundaries: Sequence[BoundaryFile], params: WairCostParams
) -> BoundarySelection:
    ranked = sorted(boundaries, key=lambda b: (-b.savings_cpu_seconds, b.file_path))
    best_cut, best_delta = 0, 0.0
    size = 0
    savings = 0.0
    for cut, boundary in enumerate(ranked, start=1):
        size += boundary.size_bytes
        savings += boundary.savings_cpu_seconds
        cost = params.recluster_cpu_seconds(size)
        if params.cost_limit_cpu_seconds is not None and cost > params.cost_limit_cpu_seconds:
            break
        delta = params.aggressiveness * cost - savings
        if delta < best_delta:
            best_cut, best_delta = cut, delta
    chosen = tuple(ranked[:best_cut])
    chosen_bytes = sum(b.size_bytes for b in chosen)
    chosen_savings = sum(b.savings_cpu_seconds for b in chosen)
    chosen_cost = params.recluster_cpu_seconds(chosen_bytes)
    return BoundarySelection(
        selected=chosen,
        selected_bytes=chosen_bytes,
        savings_cpu_seconds=chosen_savings,
        recluster_cpu_seconds=chosen_cost,
        net_cpu_seconds=chosen_savings - chosen_cost,
        clustering_column=_dominant_column(chosen),
    )


def _dominant_column(files: Sequence[BoundaryFile]) -> str | None:
    """The single global key with the highest aggregate savings (paper §4.4's baseline).

    The paper's hybrid layout (per-group keys via DBSCAN over savings signatures, Hilbert or
    Qd-tree for blends) is not implemented.
    """
    totals: dict[str, float] = {}
    for file in files:
        for column, saving in file.savings_by_column.items():
            totals[column] = totals.get(column, 0.0) + saving
    if not totals:
        return None
    return max(sorted(totals), key=lambda c: totals[c])
