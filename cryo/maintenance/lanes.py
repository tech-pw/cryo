"""Split a ranked table list into Spark lanes and size each lane's pod.

A lane is one pod running Spark in local mode that works through its tables sequentially in a
single session. That replaces the per-statement model (one spark-submit wrapper + driver +
executors for every SQL statement), which started ~35k driver pods in 8 days and billed
mostly for nodes held up by pod churn.

Lane count follows the amount of work, not a fixed number: a quiet day gets one small pod.
Tables are dealt in rank order to the least-loaded lane, so the highest-ranked tables start
first on every lane and lanes finish at roughly the same time. Each lane is then sized from
the largest single table it holds, since that table sets the lane's peak memory need.

Tables are first split by size tier (see _SIZE_TIERS) and dealt separately within each tier,
so a lane's size reflects the tables it actually carries rather than one outlier dragging
everything sharing its lane up to its own tier. Confirmed live 2026-09-28: a 494-table run
put 3 of 4 lanes at the largest tier (4 vCPU/16Gi) purely because each lane happened to
contain at least one table over the top threshold, even though those lanes averaged ~120
tables each and most were nowhere near that size -- the K8s namespace's own "unused capacity"
cost check flagged those lanes' pod-lifetime cost at 2-4x their measured usage as a result.
Tier-first dealing means only the tables that actually need a large pod pay for one.
That split yields when it would exceed the lane budget: ``max_lanes`` is a hard upper
bound, and the lowest tiers then share a lane sized to the largest table among them.
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
# 16Gi/4 vCPU starts at 200GB, not 50GB. A 50GB rewrite was reserving a 16Gi lane for the
# whole nightly window; the 16Gi tier stays for tables that actually need it.
_SIZE_TIERS: tuple[tuple[float, int, int], ...] = (
    (5.0, 1000, 4096),
    (200.0, 2000, 8192),
)
_LARGEST_TIER = (4000, 16384)


def lane_size(largest_gb: float, limits: LaneLimits) -> tuple[int, int]:
    cpu, mem = _LARGEST_TIER
    for upper, tier_cpu, tier_mem in _SIZE_TIERS:
        if largest_gb < upper:
            cpu, mem = tier_cpu, tier_mem
            break
    return min(cpu, limits.max_cpu_milli), min(mem, limits.max_mem_mib)


def _tier_index(rewrite_gb: float) -> int:
    """Which _SIZE_TIERS bucket a table's rewrite size falls into -- index into _SIZE_TIERS,
    or len(_SIZE_TIERS) for the largest (uncapped) tier."""
    for i, (upper, _cpu, _mem) in enumerate(_SIZE_TIERS):
        if rewrite_gb < upper:
            return i
    return len(_SIZE_TIERS)


def lane_count(tables: Sequence[LaneTable], limits: LaneLimits) -> int:
    if not tables:
        return 0
    total = sum(t.rewrite_gb + limits.per_table_overhead_gb for t in tables)
    wanted = math.ceil(total / limits.target_gb_per_lane)
    return max(1, min(wanted, limits.max_lanes, len(tables)))


def _groups_within_budget(tables: Sequence[LaneTable], budget: int) -> dict[int, list[LaneTable]]:
    """Fold the lowest size tiers until at most ``budget`` groups remain.

    A folded group keeps rank order and is later sized from its largest table.
    """
    by_tier: dict[int, list[LaneTable]] = {}
    for table in tables:
        by_tier.setdefault(_tier_index(table.rewrite_gb), []).append(table)
    if budget < 1 or len(by_tier) <= budget:
        return by_tier
    shared = sorted(by_tier)[len(by_tier) - budget]
    packed: dict[int, list[LaneTable]] = {}
    for table in tables:
        tier = _tier_index(table.rewrite_gb)
        packed.setdefault(shared if tier <= shared else tier, []).append(table)
    return packed


def plan_lanes(tables: Sequence[LaneTable], limits: LaneLimits | None = None) -> list[Lane]:
    """``tables`` must already be in rank order; each lane keeps that relative order.

    Tables are dealt tier-first so a lane's pod matches the tables it carries. The budget
    is ``lane_count``, itself capped by ``max_lanes``. Occupied tiers stay separate when
    that budget has room; otherwise the lowest tiers share a lane sized to the largest
    table among them. The returned plan never has more lanes than ``max_lanes``.
    """
    limits = limits or LaneLimits()
    if not tables or limits.max_lanes < 1:
        return []
    budget = min(lane_count(tables, limits), limits.max_lanes)
    if budget < 1:
        return []

    groups = _groups_within_budget(tables, budget)
    group_work = {
        tier: sum(t.rewrite_gb + limits.per_table_overhead_gb for t in group)
        for tier, group in groups.items()
    }
    group_max_lanes = {tier: len(group) for tier, group in groups.items()}
    total_work = sum(group_work.values()) or 1.0
    # Proportional share of the overall lane budget, at least 1 lane per group (groups
    # already fit in the budget, so that floor cannot exceed it), never more than that
    # group's own table count -- a lane needs >=1 table.
    raw_shares = {
        tier: min(group_max_lanes[tier], max(1, round(budget * (work / total_work))))
        for tier, work in group_work.items()
    }
    while sum(raw_shares.values()) > budget:
        # Trim from the tier with the most lanes first -- the smallest, cheapest tier is the
        # one that can most afford to lose a lane and still fit its tables via more work per
        # pod, whereas the largest tier's lane count is closest to a hard per-table minimum
        # (one table over the ceiling genuinely needs its own lane).
        biggest = max(raw_shares, key=lambda t: (raw_shares[t], t))
        if raw_shares[biggest] <= 1:
            break
        raw_shares[biggest] -= 1
    while sum(raw_shares.values()) < budget:
        # Spare lanes (rounding gave fewer than the budget allows, or a tier's own table count
        # capped it below its proportional share) go to the tier with the most work per
        # already-assigned lane that still has table headroom to use another lane -- so the
        # most work-starved group gets relief first, and capacity never sits unused because
        # one small tier (e.g. a single outlier table) can't absorb more lanes than it has
        # tables.
        eligible = [t for t in raw_shares if raw_shares[t] < group_max_lanes[t]]
        if not eligible:
            break
        neediest = max(eligible, key=lambda t: group_work[t] / raw_shares[t])
        raw_shares[neediest] += 1

    lanes: list[Lane] = []
    for tier in sorted(groups):
        group = groups[tier]
        count = min(raw_shares[tier], len(group))
        cpu, mem = lane_size(max(t.rewrite_gb for t in group), limits)
        tier_lanes = [Lane(index=len(lanes) + i, cpu_milli=cpu, mem_mib=mem) for i in range(count)]
        loads = [0.0] * count
        for table in group:
            target = min(range(count), key=lambda i: (loads[i], i))
            tier_lanes[target].tables.append(table)
            loads[target] += table.rewrite_gb + limits.per_table_overhead_gb
        lanes.extend(tier_lanes)
    for i, lane in enumerate(lanes):
        lane.index = i
    assert len(lanes) <= limits.max_lanes
    assert sum(len(lane.tables) for lane in lanes) == len(tables)
    return lanes
