"""Split a ranked table list into Spark lanes and size each lane's pod.

A lane is one pod running Spark in local mode that works through its tables sequentially in a
single session. That replaces the per-statement model (one spark-submit wrapper + driver +
executors for every SQL statement), which started ~35k driver pods in 8 days and billed
mostly for nodes held up by pod churn.

Lane count follows the amount of work, not a fixed number: a quiet day gets one small pod.
Tables are dealt in rank order to the least-loaded lane, so the highest-ranked tables start
first on every lane and lanes finish at roughly the same time. Each lane is then sized from
the largest single table it holds, since that table sets the lane's peak memory need.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LaneLimits:
    max_lanes: int = 4
    max_cpu_milli: int = 4000
    max_mem_mib: int = 16384
    # Work one lane is expected to get through before a second lane is worth its startup cost.
    target_gb_per_lane: float = 200.0
    # Fixed per-table cost in GB-equivalents: every table pays metadata commits, snapshot
    # expiry and an orphan listing regardless of how much data it rewrites.
    per_table_overhead_gb: float = 0.5


@dataclass(frozen=True)
class LaneTable:
    table_id: uuid.UUID
    rewrite_gb: float


@dataclass
class Lane:
    index: int
    tables: list[LaneTable] = field(default_factory=list)
    cpu_milli: int = 0
    mem_mib: int = 0

    @property
    def work_gb(self) -> float:
        return sum(t.rewrite_gb for t in self.tables)

    @property
    def largest_table_gb(self) -> float:
        return max((t.rewrite_gb for t in self.tables), default=0.0)


# (largest single table rewrite GB strictly under, cpu_milli, mem_mib)
_SIZE_TIERS: tuple[tuple[float, int, int], ...] = (
    (5.0, 1000, 4096),
    (50.0, 2000, 8192),
)
_LARGEST_TIER = (4000, 16384)


def _lane_size(largest_gb: float, limits: LaneLimits) -> tuple[int, int]:
    cpu, mem = _LARGEST_TIER
    for upper, tier_cpu, tier_mem in _SIZE_TIERS:
        if largest_gb < upper:
            cpu, mem = tier_cpu, tier_mem
            break
    return min(cpu, limits.max_cpu_milli), min(mem, limits.max_mem_mib)


def lane_count(tables: Sequence[LaneTable], limits: LaneLimits) -> int:
    if not tables:
        return 0
    total = sum(t.rewrite_gb + limits.per_table_overhead_gb for t in tables)
    wanted = math.ceil(total / limits.target_gb_per_lane)
    return max(1, min(wanted, limits.max_lanes, len(tables)))


def plan_lanes(tables: Sequence[LaneTable], limits: LaneLimits | None = None) -> list[Lane]:
    """``tables`` must already be in rank order; each lane keeps that relative order."""
    limits = limits or LaneLimits()
    count = lane_count(tables, limits)
    lanes = [Lane(index=i) for i in range(count)]
    if not lanes:
        return []
    loads = [0.0] * count
    for table in tables:
        target = min(range(count), key=lambda i: (loads[i], i))
        lanes[target].tables.append(table)
        loads[target] += table.rewrite_gb + limits.per_table_overhead_gb
    for lane in lanes:
        lane.cpu_milli, lane.mem_mib = _lane_size(lane.largest_table_gb, limits)
    return lanes
