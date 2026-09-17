"""AutoComp-style compaction benefit scoring.

Based on: "AutoComp: Automated Data Compaction for Log-Structured Tables
in Data Lakes" — SIGMOD 2025, LinkedIn.

Core idea: compact a partition only when the expected scan savings
× query frequency exceed the estimated compaction cost.

Score = (small_files × query_frequency × scan_cost_multiplier) / estimated_rewrite_cost_factor

scan_cost_multiplier (see _scan_cost_multiplier) weights by real Trino bytes-scanned-per-query
when known (QueryLog.bytes_read), neutral (1.0) otherwise -- two tables with identical
fragmentation and identical query *counts* don't necessarily cost the same to scan, and file
count/frequency alone can't see that gap.
"""

from __future__ import annotations

import gc
import hashlib
import logging
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, QueryLog, Table, TablePartition, TableStats
from cryo.domain.enums import PartitionTransform
from cryo.planner.partition_query_affinity import MIN_COVERAGE_RATIO, compute_partition_affinity
from cryo.planner.partitions import PartitionFieldSpec, normalize_transform

logger = logging.getLogger(__name__)

# How many hours of query history to use for frequency calculation
_QUERY_WINDOW_HOURS = 168  # 7 days

# Minimum benefit score to be eligible for compaction (below this = skip)
DEFAULT_MIN_BENEFIT_SCORE = 1.0

# How often (every N tables) update_all_benefit_scores forces a full gc.collect() pass to fight
# heap fragmentation across a long sweep -- see that function's own comment at the call site for
# the live incident this addresses. 250 is a compromise: frequent enough to keep peak RSS well
# below the scheduler's memory limit across a 13k+ table fleet, infrequent enough that gc.collect()
# itself (a stop-the-world pause proportional to live object count) doesn't meaningfully slow the
# sweep down -- expunge_all() has already dropped this table's objects to typically near-zero live
# references by the time each collection runs, so each pass stays cheap.
_GC_COLLECT_EVERY_N_TABLES = 250


class _SchedulingBaseline(TypedDict):
    name: str
    order: list[str]
    utility: float
    regret: float
    regret_ratio: float


# Cost factor: relative cost estimate of rewriting N GB
# (pure heuristic: 1 GB ≈ cost_factor=1 unit)
_COST_PER_GB = 1.0


@dataclass(frozen=True)
class BenefitCostBreakdown:
    latency_benefit_usd_month: float
    storage_benefit_usd_month: float
    request_benefit_usd_month: float
    opportunity_benefit_usd_month: float
    rewrite_compute_cost_usd_month: float
    write_amplification_cost_usd_month: float
    opportunity_cost_usd_month: float

    @property
    def total_benefit_usd_month(self) -> float:
        return (
            self.latency_benefit_usd_month
            + self.storage_benefit_usd_month
            + self.request_benefit_usd_month
            + self.opportunity_benefit_usd_month
        )

    @property
    def total_cost_usd_month(self) -> float:
        return (
            self.rewrite_compute_cost_usd_month
            + self.write_amplification_cost_usd_month
            + self.opportunity_cost_usd_month
        )

    @property
    def bcr(self) -> float:
        return self.total_benefit_usd_month / max(self.total_cost_usd_month, 0.0001)


def enhanced_bcr_from_table(
    table_stats: Mapping[str, Any],
    *,
    projected_storage_savings_usd_month: float | None = None,
    query_request_savings_usd_month: float | None = None,
    avg_bytes_per_query: float | None = None,
    value_per_query_second_usd: float = 0.0002,
    rewrite_cost_per_gb_usd: float = 0.025,
    write_amp_multiplier: float = 1.15,
) -> BenefitCostBreakdown:
    """Compute monthly ROI terms in USD for smart-maintenance gating.

    ``avg_bytes_per_query``: real per-query scan cost (``QueryLog.bytes_read`` average), when
    known. This is the same admission decision ``ROIGate.decide()`` uses to PROCEED/SKIP/DEFER
    a job outright -- unlike ``compute_benefit_score``'s ranking-only role, a wrong number here
    can block a job from running at all. Reuses ``_scan_cost_multiplier`` so a table whose
    queries genuinely scan a lot of data gets credited proportionally more latency benefit for
    having fewer/larger files to scan through, instead of ``latency_seconds_saved_per_query``
    depending only on file count/delete pressure with no sense of how expensive each scan
    actually is. ``None`` (the default) keeps this identical to before the parameter existed.
    """
    query_count_30d = float(table_stats.get("query_count_30d") or 0.0)
    small_files = float(table_stats.get("small_files_count") or 0.0)
    delete_pct = float(table_stats.get("delete_file_pct") or 0.0)
    table_size_gb = float(table_stats.get("total_size_gb") or 0.0)
    projected = float(
        projected_storage_savings_usd_month
        if projected_storage_savings_usd_month is not None
        else (table_stats.get("projected_savings_usd_month") or 0.0)
    )
    request = float(
        query_request_savings_usd_month
        if query_request_savings_usd_month is not None
        else (table_stats.get("query_request_savings_usd_month") or 0.0)
    )

    # Cheap but monotonic proxy: more small files and more delete pressure generally
    # translate to larger planning/pruning wins. Scaled by real scan cost (neutral 1.0 when
    # unknown) so the benefit estimate reflects how much data these queries actually touch,
    # not just how fragmented the table is.
    latency_seconds_saved_per_query = (small_files / 2000.0) + (delete_pct / 100.0 * 0.35)
    latency_benefit = (
        query_count_30d
        * latency_seconds_saved_per_query
        * _scan_cost_multiplier(avg_bytes_per_query)
        * value_per_query_second_usd
    )

    rewrite_compute_cost = max(0.0, table_size_gb * rewrite_cost_per_gb_usd)
    write_amp_cost = max(0.0, table_size_gb * max(write_amp_multiplier - 1.0, 0.0) * 0.006)
    # Opportunity cost captures foregone slots for other jobs in busy clusters.
    opportunity_cost = rewrite_compute_cost * 0.1
    opportunity_benefit = max(0.0, min(projected * 0.25, latency_benefit * 0.5))

    return BenefitCostBreakdown(
        latency_benefit_usd_month=round(latency_benefit, 6),
        storage_benefit_usd_month=round(max(projected, 0.0), 6),
        request_benefit_usd_month=round(max(request, 0.0), 6),
        opportunity_benefit_usd_month=round(opportunity_benefit, 6),
        rewrite_compute_cost_usd_month=round(rewrite_compute_cost, 6),
        write_amplification_cost_usd_month=round(write_amp_cost, 6),
        opportunity_cost_usd_month=round(opportunity_cost, 6),
    )


# Reference point for the scan-cost multiplier: a query that reads ~1GB on average is treated
# as "typical" (multiplier ~1x, same as a caller that never passes avg_bytes_per_query at all).
# Bigger than this and the multiplier climbs above 1; smaller and it eases below 1 but never to
# 0, so a low-cost query pattern still leaves file-count/frequency doing the ranking rather than
# suppressing the score outright. log2-scaled rather than linear so one 500GB-scanning outlier
# query doesn't blow the ranking out to a degenerate "always compact this table first" state --
# real spread observed in cost_attribution's 20K-query sample (2026-08-14) ranged from
# near-zero to 525GB in a single query, a ~9 orders-of-magnitude range linear scaling can't
# absorb sanely.
_TYPICAL_QUERY_BYTES_GB = 1.0
# Ceiling on the multiplier itself -- an extreme scan-cost table should rank well above a cheap
# one, but shouldn't single-handedly drown out every other trait (small_files, frequency) the
# way an unbounded log multiplier could for a truly pathological outlier.
_MAX_BYTES_MULTIPLIER = 8.0


def _scan_cost_multiplier(avg_bytes_per_query: float | None) -> float:
    """log2-scaled multiplier from average bytes scanned per query hitting this partition/table.

    Returns 1.0 (neutral -- no change to the pre-existing score) when no scan-cost signal is
    available, so every caller that doesn't pass ``avg_bytes_per_query`` keeps today's behavior
    exactly. See module-level constants above for why log2 and why it's capped.
    """
    if avg_bytes_per_query is None or avg_bytes_per_query <= 0:
        return 1.0
    gb = avg_bytes_per_query / 1_073_741_824  # bytes -> GiB
    if gb <= 0:
        return 1.0
    multiplier = 1.0 + math.log2(max(gb, 0.001) / _TYPICAL_QUERY_BYTES_GB)
    return round(min(max(multiplier, 0.25), _MAX_BYTES_MULTIPLIER), 4)


def compute_benefit_score(
    small_files: int,
    query_frequency: float,
    partition_size_gb: float,
    avg_bytes_per_query: float | None = None,
) -> float:
    """Compute a compaction benefit score for a partition.

    Args:
        small_files: Number of small files in the partition
        query_frequency: Average queries per day hitting this partition
        partition_size_gb: Size of the partition in GB (proxy for rewrite cost)
        avg_bytes_per_query: Average bytes actually scanned per query hitting this
            partition/table (``QueryLog.bytes_read``), when known. Two tables with identical
            file fragmentation and identical query *counts* previously scored identically even
            if one's queries scan orders of magnitude more data per hit -- this is the real
            Trino scan-cost signal that distinguishes them. ``None`` (the default) keeps the
            score identical to before this parameter existed, for every existing caller that
            hasn't been wired up to a bytes source yet.

    Returns:
        Benefit score — higher = higher priority for compaction.
        Score < 1.0 means compaction cost likely exceeds benefit.
    """
    if small_files <= 0:
        return 0.0

    # Scan savings increase with small file count (more files = more planning overhead)
    scan_savings = float(small_files)

    # Query frequency amplifies the value of faster scans
    frequency_multiplier = max(query_frequency, 0.1)  # floor at 0.1 to avoid zero

    # Real Trino scan-cost signal: queries that read more bytes per hit get more benefit from
    # having fewer/larger files to scan through. Neutral (1.0) when unknown.
    bytes_multiplier = _scan_cost_multiplier(avg_bytes_per_query)

    # Rewrite cost scales with data volume
    rewrite_cost = max(partition_size_gb * _COST_PER_GB, 0.1)

    score = (scan_savings * frequency_multiplier * bytes_multiplier) / rewrite_cost
    return round(score, 4)


# AutoComp §A2 default scalarization weights (paper-aligned): S_c = 0.55 T1' + 0.15 T3' +
# 0.15 T4' - 0.15 T2'. When T3 (file entropy) has no data yet -- the scanner doesn't populate
# TablePartition.file_entropy today, see docs/implementation/workload-aware-optimization.md
# §A.4 -- its weight is redistributed proportionally across T1/T4 rather than letting a missing
# trait silently score as "worst possible entropy".
@dataclass(frozen=True)
class MoopWeights:
    t1_file_count: float = 0.55
    t2_compute_cost: float = 0.15
    t3_file_entropy: float = 0.15
    t4_query_affinity: float = 0.15
    version: str = "v1"


DEFAULT_MOOP_WEIGHTS = MoopWeights()


@dataclass(frozen=True)
class MoopTraits:
    """Normalized [0,1] trait values plus the scalarized score, for one candidate."""

    t1: float
    t2: float
    t3: float
    t4: float
    s_c: float

    def as_dict(self, *, weights_version: str) -> dict[str, float | str]:
        return {
            "t1": self.t1,
            "t2": self.t2,
            "t3": self.t3,
            "t4": self.t4,
            "s_c": self.s_c,
            "weights_version": weights_version,
        }


def compute_moop_scores(
    candidates: Sequence[Mapping[str, Any]],
    weights: MoopWeights = DEFAULT_MOOP_WEIGHTS,
) -> list[MoopTraits]:
    """MOOP-scalarized score for each candidate, normalized against the others in this batch.

    Each candidate is a mapping with ``small_files``, ``size_gb``, ``query_frequency``, and
    optionally ``file_entropy`` and ``avg_bytes_per_query``. Normalization is min-max *within
    this batch* (a table's partitions from one scan, or a fleet-wide batch the caller assembles)
    -- the paper allows either; per-table keeps this a pure function with no cross-table state
    to manage.

    T4 (query_affinity) folds in real Trino scan cost (``avg_bytes_per_query``, when given) via
    the same log2-scaled multiplier ``compute_benefit_score`` uses, rather than adding a 5th
    trait: the paper's four-trait scalarization (T1 file count, T2 compute cost, T3 entropy, T4
    query affinity) is a fixed, weighted formula, and query affinity is already "how much does
    being queried matter" -- a query that scans 50GB mattering more than one scanning 10MB is
    the same concept sharpened with real cost, not a new dimension. Two candidates with
    identical query *counts* previously tied on T4 even when one's queries scan orders of
    magnitude more data per hit; this closes that gap without touching T1-T3 or the paper's
    weight semantics.
    """
    if not candidates:
        return []

    small_files = [float(c.get("small_files") or 0) for c in candidates]
    size_gb = [float(c.get("size_gb") or 0.1) for c in candidates]
    frequency = [
        float(c.get("query_frequency") or 0.0)
        * _scan_cost_multiplier(
            float(v) if (v := c.get("avg_bytes_per_query")) is not None else None
        )
        for c in candidates
    ]
    entropy_raw = [c.get("file_entropy") for c in candidates]
    has_entropy = any(v is not None for v in entropy_raw)
    entropy = [float(v) if v is not None else 0.0 for v in entropy_raw]

    t1_norm = _min_max_normalize(small_files)
    t2_norm = _min_max_normalize(size_gb)
    t3_norm = _min_max_normalize(entropy) if has_entropy else [0.0] * len(candidates)
    t4_norm = _min_max_normalize(frequency)

    if has_entropy:
        t1_w, t2_w, t3_w, t4_w = (
            weights.t1_file_count,
            weights.t2_compute_cost,
            weights.t3_file_entropy,
            weights.t4_query_affinity,
        )
    else:
        base = weights.t1_file_count + weights.t4_query_affinity
        share = weights.t3_file_entropy / base if base > 0 else 0.5
        t1_w = weights.t1_file_count + weights.t1_file_count * share
        t4_w = weights.t4_query_affinity + weights.t4_query_affinity * share
        t2_w = weights.t2_compute_cost
        t3_w = 0.0

    results = []
    for i in range(len(candidates)):
        s_c = t1_w * t1_norm[i] + t3_w * t3_norm[i] + t4_w * t4_norm[i] - t2_w * t2_norm[i]
        results.append(
            MoopTraits(
                t1=round(t1_norm[i], 4),
                t2=round(t2_norm[i], 4),
                t3=round(t3_norm[i], 4),
                t4=round(t4_norm[i], 4),
                s_c=round(s_c, 4),
            )
        )
    return results


def _min_max_normalize(values: Sequence[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    span = hi - lo
    if span <= 0:
        # Every candidate ties on this trait -- neutral midpoint, not 0 (which would read as
        # "worst on this trait" for an entire batch that's actually uniform) or 1 (which would
        # read as "best for everyone", equally misleading).
        return [0.5] * len(values)
    return [(v - lo) / span for v in values]


def _partition_field_spec(partitions: Sequence[TablePartition]) -> PartitionFieldSpec | None:
    """Primary partition-field spec shared by this scan's rows, or None if unpartitioned.

    Cryo's per-partition scoring (like the rest of Part A) only reasons about the primary
    partition field, matching ``analyze.py::partition_row``'s own ``accum.fields[0]``
    convention for compound specs -- multi-field affinity is out of scope for this phase.
    """
    primary = next((p for p in partitions if p.base_column), None)
    if primary is None or primary.base_column is None:
        return None
    base_column = primary.base_column
    fields_raw = primary.partition_fields or []
    field_detail = fields_raw[0] if fields_raw else {}
    partition_field_name = field_detail.get("partition_field") or base_column
    return PartitionFieldSpec(
        base_column=base_column,
        transform=normalize_transform(primary.transform),
        col_type=primary.base_column_type or "",
        partition_field=str(partition_field_name) if partition_field_name else base_column,
    )


def _partition_query_frequency(
    session: Session,
    *,
    table_id: uuid.UUID,
    since: datetime,
    days_in_window: float,
    partitions: Sequence[TablePartition],
) -> dict[str, float]:
    """Per-``partition_value`` queries/day, or ``{}`` to signal "use the table-level rate"."""
    field = _partition_field_spec(partitions)
    if field is None or field.transform is PartitionTransform.UNPARTITIONED:
        return {}

    query_texts = session.scalars(
        select(QueryLog.query_text).where(
            QueryLog.table_id == table_id,
            QueryLog.executed_at >= since,
            QueryLog.query_text.isnot(None),
        )
    ).all()
    if not query_texts:
        return {}

    partition_field_key = field.partition_field or field.base_column
    known_values: dict[str, Any] = {}
    for partition in partitions:
        key = partition.partition_value
        if key is None:
            continue
        values = partition.partition_values or {}
        known_values[key] = values.get(partition_field_key, key)

    affinity = compute_partition_affinity(list(query_texts), field, known_values)
    if not affinity.is_trustworthy:
        logger.info(
            "partition_query_affinity: table=%s coverage=%.0f%% below %.0f%% threshold, "
            "falling back to table-level frequency (%d/%d queries resolved)",
            table_id,
            affinity.coverage_ratio * 100,
            MIN_COVERAGE_RATIO * 100,
            affinity.resolved_queries,
            affinity.total_queries,
        )
        return {}

    # Every current partition needs an explicit entry here, defaulting to 0.0 -- a partition
    # this window's resolved queries never touched is cold, not "unknown". Falling through to
    # the caller's table-level default for it (via dict.get on a partial mapping) would silently
    # broadcast the table-wide rate right back onto it, defeating the point of this function.
    per_day = max(days_in_window, 1.0)
    return {
        p.partition_value: affinity.hits.get(p.partition_value, 0.0) / per_day
        for p in partitions
        if p.partition_value is not None
    }


def update_partition_benefit_scores(
    session: Session,
    table_id: uuid.UUID,
    query_window_hours: int = _QUERY_WINDOW_HOURS,
) -> int:
    """Update compaction_benefit_score for all partitions of a table.

    This should be called:
    - After the scanner updates partition stats
    - After new query logs are ingested

    Partitions get per-partition query frequency (AutoComp §A3.1) when enough in-window
    queries have a predicate Cryo can resolve to a specific partition value
    (``PartitionAffinity.is_trustworthy``, see ``partition_query_affinity``); otherwise every
    partition falls back to the table-level rate, same as before this existed.

    Args:
        session: Database session
        table_id: Table to update scores for
        query_window_hours: Hours of query history to use

    Returns:
        Number of partitions updated
    """
    since = datetime.now(UTC) - timedelta(hours=query_window_hours)
    days_in_window = query_window_hours / 24.0

    # Load current partitions for this table (from most recent scan only)
    # First get the latest scan_at timestamp
    latest_scan = session.scalar(
        select(func.max(TablePartition.scan_at)).where(
            TablePartition.table_id == table_id,
        )
    )

    if not latest_scan:
        return 0

    # Then get partitions from that scan
    partitions = session.scalars(
        select(TablePartition)
        .where(
            TablePartition.table_id == table_id,
            TablePartition.scan_at == latest_scan,
        )
        .limit(10000)  # safety cap
    ).all()

    if not partitions:
        return 0

    from cryo.intelligence.autocomp_feedback import (
        realized_cost_multiplier,
        realized_latency_multiplier,
        realized_value_multiplier,
    )

    table_stats = session.get(TableStats, table_id)
    stats_raw = table_stats.raw if table_stats is not None else None
    # Three independently-gated calibration multipliers, each defaulting to neutral (1.0)
    # until its own evidence-sufficiency/drift-stability check qualifies -- see
    # autocomp_feedback._reduction_ratio_calibration's docstring for why cost/latency stay
    # separate from the byte-yield multiplier rather than one blended number. Composed
    # multiplicatively, same as feedback_multiplier alone composed with the base score before
    # this existed: a table that's qualified on multiple fronts gets rewarded on each, not
    # capped at whichever signal happens to qualify first.
    feedback_multiplier = (
        realized_value_multiplier(stats_raw)
        * realized_cost_multiplier(stats_raw)
        * realized_latency_multiplier(stats_raw)
    )

    total_queries = (
        session.scalar(
            select(func.count(QueryLog.id)).where(
                QueryLog.table_id == table_id,
                QueryLog.executed_at >= since,
            )
        )
        or 0
    )
    queries_per_day = total_queries / max(days_in_window, 1.0)

    # Real Trino scan-cost signal (bytes actually read per query), table-level broadcast --
    # same granularity as queries_per_day's own fallback above, since QueryLog doesn't resolve
    # bytes to a specific partition any more reliably than it resolves frequency without the
    # same affinity mechanism _partition_query_frequency already gates on. AVG rather than SUM:
    # QueryLog is a curated/sampled table (see cryo.telemetry.curator's _is_summary_worthy --
    # only failed/slow/large queries plus a 1% sample of "normal" traffic land here), so a raw
    # sum would just track how much got sampled, not the actual per-query cost.
    avg_bytes_per_query = session.scalar(
        select(func.avg(QueryLog.bytes_read)).where(
            QueryLog.table_id == table_id,
            QueryLog.executed_at >= since,
            QueryLog.bytes_read.isnot(None),
        )
    )

    partition_frequency = _partition_query_frequency(
        session,
        table_id=table_id,
        since=since,
        days_in_window=days_in_window,
        partitions=partitions,
    )

    frequencies: list[float] = []
    for partition in partitions:
        frequency = (
            partition_frequency.get(partition.partition_value, queries_per_day)
            if partition.partition_value is not None
            else queries_per_day
        )
        frequencies.append(frequency)

    moop_traits = compute_moop_scores(
        [
            {
                "small_files": partition.small_files_in_partition_count or 0,
                "size_gb": partition.partition_size_gb or 0.1,
                "query_frequency": frequencies[i],
                "file_entropy": partition.file_entropy,
                "avg_bytes_per_query": avg_bytes_per_query,
            }
            for i, partition in enumerate(partitions)
        ]
    )

    updated = 0
    for i, partition in enumerate(partitions):
        small_files = partition.small_files_in_partition_count or 0
        size_gb = partition.partition_size_gb or 0.1
        frequency = frequencies[i]

        score = round(
            compute_benefit_score(
                small_files=small_files,
                query_frequency=frequency,
                partition_size_gb=size_gb,
                avg_bytes_per_query=avg_bytes_per_query,
            )
            * feedback_multiplier,
            4,
        )

        partition.query_frequency = frequency
        partition.compaction_benefit_score = score
        partition.autocomp_traits = moop_traits[i].as_dict(
            weights_version=DEFAULT_MOOP_WEIGHTS.version
        )
        updated += 1

    session.commit()
    logger.info(f"Updated benefit scores for {updated} partitions of table {table_id}")
    return updated


def update_all_benefit_scores(
    session: Session,
    *,
    refresh_realized_value: bool = True,
    max_realized_value_refreshes: int | None = None,
) -> int:
    """Update benefit scores for all active tables.

    Called by the governor/scheduler on a regular cadence
    (e.g., every 4 hours) after query log ingestion.

    CR-04 (docs/CRYO_COST_REDUCTION_PLAN.md): the realized-value refresh (an expensive 90-day
    audit/stats/cost/query-history join, via build_outcome_dataset) was globally disabled with
    refresh_realized_value=False as an emergency OOM stopgap on 2026-09-15/16 -- but that stops
    per-table learning entirely, forever, not just during the incident window: the optimizer can
    only ever see successful/easy jobs it already scored well, and permanently underprices
    failures it never re-learns from. refresh_realized_value=True (the caller-visible default)
    restores learning without reintroducing the OOM by capping how many tables get the expensive
    refresh *per sweep* (max_realized_value_refreshes, default _DEFAULT_MAX_REALIZED_VALUE_
    REFRESHES_PER_SWEEP) and always spending that budget on the stalest tables first (oldest
    raw.autocomp_realized_value.computed_at, tables with no prior value ranked stalest of all) --
    every table's cheap compaction_effectiveness update still runs every sweep as before. This is
    the bounded-batch, cursor-like behavior CR-04 calls for, without the full event-driven
    reconciliation-queue architecture CR-04 also describes as the further-out ideal.
    """
    tables = session.scalars(select(Table).where(Table.is_active.is_(True))).all()
    # Extract just the ids and expunge immediately -- looping over Table ORM objects held in
    # `session`'s identity map for the rest of this function would keep every one of them (and,
    # via update_partition_benefit_scores below, every TablePartition object it loads too) alive
    # for the entire run. commit() doesn't clear the identity map, only expires attributes, so
    # across a large fleet (13k+ tables, up to 10k partitions each -- 1.27M partitions observed
    # in one production run) that accumulation is what was OOM-killing the scheduler process
    # every ~20-40 minutes, not any single table's own work.
    table_ids = [table.id for table in tables]
    session.expunge_all()

    refresh_budget = (
        max_realized_value_refreshes
        if max_realized_value_refreshes is not None
        else _DEFAULT_MAX_REALIZED_VALUE_REFRESHES_PER_SWEEP
    )
    stale_table_ids = (
        _stalest_realized_value_table_ids(session, table_ids, limit=refresh_budget)
        if refresh_realized_value
        else frozenset()
    )

    total = 0
    for index, table_id in enumerate(table_ids):
        total += update_partition_benefit_scores(session, table_id)
        update_table_effectiveness(
            session, table_id, refresh_realized_value=table_id in stale_table_ids
        )
        # update_partition_benefit_scores already commits its own partitions before returning;
        # expunge right after so this table's (and its partitions') objects don't linger either.
        session.expunge_all()
        if index % _GC_COLLECT_EVERY_N_TABLES == 0:
            # expunge_all() releases every object's *references*, but CPython doesn't
            # necessarily reclaim their memory back to the OS immediately or ever (freed
            # arenas can sit unused rather than being returned via malloc_trim) -- especially
            # for cyclic garbage (e.g. SQLAlchemy ORM instances' back-references to their
            # InstanceState/session), which needs a real GC pass, not just refcounting, to be
            # collected at all. Confirmed live 2026-08-25: even with expunge_all() already
            # firing correctly every table (verified via code review, not a regression), the
            # scheduler pod's RSS still climbed to 8.9GB+ over a ~99min, 1.56M-partition sweep
            # and was OOMKilled/evicted -- consistent with heap fragmentation across ~13k
            # short-lived per-table object graphs rather than a true reference leak. Forcing a
            # full collection periodically (not every table -- gc.collect() itself isn't free)
            # trades a small amount of CPU for materially lower peak RSS across the sweep.
            gc.collect()
    return total


# Bounds how many tables per sweep pay the expensive 90-day realized-value join (see
# update_all_benefit_scores' CR-04 docstring) -- deliberately small relative to a 13k-table
# fleet: at this rate every table gets refreshed roughly every ~26 sweeps (with the default
# 6h cadence, about a week), which is a large improvement over "never" while still keeping the
# per-sweep IO/memory cost bounded and predictable regardless of fleet size.
_DEFAULT_MAX_REALIZED_VALUE_REFRESHES_PER_SWEEP = 500


def _stalest_realized_value_table_ids(
    session: Session, table_ids: Sequence[uuid.UUID], *, limit: int
) -> frozenset[uuid.UUID]:
    """Pick the `limit` tables whose autocomp_realized_value is oldest (or missing entirely --
    ranked stalest of all, ahead of any table that has ever been refreshed) to spend this
    sweep's refresh budget on.

    Deliberately queries only TableStats.raw (one column) for every table_id rather than full
    Table/TableStats ORM objects, and returns a plain frozenset of ids -- nothing here holds a
    ORM-mapped object alive past this function, so it can't reintroduce the identity-map growth
    update_all_benefit_scores' own docstring describes. Querying inside the JSON raw column
    itself (e.g. an ORDER BY on raw->>'autocomp_realized_value'->>'computed_at') was avoided
    deliberately -- this repo has no existing precedent for that and the JSON operator syntax
    differs between the Postgres production backend and the SQLite test backend, whereas a
    single bounded raw-column-only query plus a Python sort is dialect-portable and just as
    bounded in memory (one small string per table, never an ORM object).
    """
    if limit <= 0 or not table_ids:
        return frozenset()
    rows = session.execute(
        select(TableStats.table_id, TableStats.raw).where(TableStats.table_id.in_(table_ids))
    ).all()
    session.expunge_all()

    def _computed_at(raw: Any) -> str:
        if not isinstance(raw, dict):
            return ""
        realized = raw.get("autocomp_realized_value")
        if not isinstance(realized, dict):
            return ""
        return str(realized.get("computed_at") or "")

    # ISO-8601 timestamps sort correctly as plain strings; "" (missing/never-refreshed) sorts
    # before any real timestamp, so tables with no prior value are stalest by construction.
    ranked = sorted(rows, key=lambda row: _computed_at(row.raw))
    return frozenset(row.table_id for row in ranked[:limit])


def update_table_effectiveness(
    session: Session, table_id: uuid.UUID, *, refresh_realized_value: bool = True
) -> bool:
    """Persist the current compaction-effectiveness ratio onto table_stats.raw (AutoComp §A3).

    Cheap by construction: get_compaction_effectiveness_stats is a single aggregate query (no
    per-row AuditLog objects loaded), and this only touches the one TableStats row already
    identified by primary key -- safe to run inside update_all_benefit_scores' per-table loop
    without reintroducing the identity-map growth that OOM-killed the scheduler before.

    Returns False (no-op) when the table has no TableStats row yet.
    """
    stats = session.get(TableStats, table_id)
    if stats is None:
        return False
    effectiveness = get_compaction_effectiveness_stats(session, table_id)
    from cryo.intelligence.autocomp_feedback import build_autocomp_realized_value
    from cryo.intelligence.outcomes import build_outcome_dataset

    raw = dict(stats.raw or {})
    raw["compaction_effectiveness"] = {
        **effectiveness,
        "computed_at": datetime.now(UTC).isoformat(),
    }
    if refresh_realized_value:
        realized_value = build_autocomp_realized_value(
            build_outcome_dataset(session, table_id=table_id, limit=200)
        )
        raw["autocomp_realized_value"] = {
            **realized_value.to_dict(),
            "computed_at": datetime.now(UTC).isoformat(),
        }
    stats.raw = raw
    session.commit()
    return True


def get_top_compaction_candidates(
    session: Session,
    table_id: uuid.UUID,
    min_score: float = DEFAULT_MIN_BENEFIT_SCORE,
    limit: int = 50,
) -> list[TablePartition]:
    """Get highest-priority partitions for compaction using benefit scores.

    Args:
        session: Database session
        table_id: Table to get candidates for
        min_score: Minimum benefit score threshold
        limit: Maximum partitions to return

    Returns:
        Partitions sorted by benefit score descending
    """
    # table_partitions is append-only (one row per partition per scan) -- without pinning to the
    # latest scan, a partition that scored high in more than one past scan shows up as multiple
    # "different" candidates with identical stats, since only update_partition_benefit_scores'
    # write side is scoped to the latest scan; older rows' scores are never cleared.
    latest_scan = session.scalar(
        select(func.max(TablePartition.scan_at)).where(TablePartition.table_id == table_id)
    )
    if latest_scan is None:
        return []
    return list(
        session.scalars(
            select(TablePartition)
            .where(
                TablePartition.table_id == table_id,
                TablePartition.scan_at == latest_scan,
                TablePartition.compaction_benefit_score >= min_score,
            )
            .order_by(TablePartition.compaction_benefit_score.desc())
            .limit(limit)
        )
    )


def compare_scheduling_strategies(
    session: Session,
    table_id: uuid.UUID,
    *,
    limit: int = 50,
) -> dict[str, Any]:
    """Compare AutoComp against deterministic, interpretable scheduling baselines."""
    from cryo.db.models import TablePartition

    latest_scan = session.scalar(
        select(func.max(TablePartition.scan_at)).where(TablePartition.table_id == table_id)
    )
    if not latest_scan:
        return {
            "static_order": [],
            "autocomp_order": [],
            "improvement_ratio": 0.0,
            "autocomp_wins": False,
            "confidence": 0.0,
            "evidence_sufficient": False,
            "baselines": [],
        }

    partitions = list(
        session.scalars(
            select(TablePartition).where(
                TablePartition.table_id == table_id,
                TablePartition.scan_at == latest_scan,
            )
        )
    )

    candidate_limit = min(10, limit)
    autocomp_sorted = sorted(
        partitions,
        key=lambda p: p.compaction_benefit_score or 0.0,
        reverse=True,
    )[:candidate_limit]
    baseline_orders = {
        "size_only": sorted(
            partitions,
            key=lambda partition: partition.partition_size_gb or 0.0,
            reverse=True,
        )[:candidate_limit],
        "file_count": sorted(
            partitions,
            key=lambda partition: partition.small_files_in_partition_count or 0,
            reverse=True,
        )[:candidate_limit],
        "threshold": sorted(
            [
                partition
                for partition in partitions
                if (partition.small_files_in_partition_count or 0) >= 10
            ],
            key=lambda partition: partition.small_files_in_partition_count or 0,
            reverse=True,
        )[:candidate_limit],
        "fifo": sorted(partitions, key=lambda partition: str(partition.id))[:candidate_limit],
        "random": sorted(
            partitions,
            key=lambda partition: hashlib.sha256(
                f"{table_id}:{partition.partition_value}".encode()
            ).digest(),
        )[:candidate_limit],
    }
    autocomp_utility = _selection_utility(autocomp_sorted)
    baseline_reports: list[_SchedulingBaseline] = []
    for name, order in baseline_orders.items():
        utility = _selection_utility(order)
        regret = max(0.0, autocomp_utility - utility)
        baseline_reports.append(
            {
                "name": name,
                "order": [str(partition.partition_value) for partition in order],
                "utility": round(utility, 4),
                "regret": round(regret, 4),
                "regret_ratio": round(regret / max(abs(autocomp_utility), 0.0001), 4),
            }
        )
    file_count_utility = next(
        report["utility"] for report in baseline_reports if report["name"] == "file_count"
    )
    improvement = autocomp_utility / max(float(file_count_utility), 0.0001)
    score_coverage = (
        sum(partition.compaction_benefit_score is not None for partition in partitions)
        / len(partitions)
        if partitions
        else 0.0
    )
    confidence = round(score_coverage * min(1.0, len(partitions) / 10.0), 4)

    return {
        "static_order": baseline_reports[1]["order"],
        "autocomp_order": [str(partition.partition_value) for partition in autocomp_sorted],
        "improvement_ratio": round(improvement, 4),
        "autocomp_wins": autocomp_utility > float(file_count_utility),
        "confidence": confidence,
        "evidence_sufficient": confidence >= 0.8 and len(partitions) >= 5,
        "baselines": baseline_reports,
    }


def _selection_utility(partitions: Sequence[TablePartition]) -> float:
    return sum(float(partition.compaction_benefit_score or 0.0) for partition in partitions)


def get_compaction_effectiveness_stats(
    session: Session,
    table_id: uuid.UUID,
    lookback_days: int = 30,
) -> dict[str, Any]:
    """Calculate compaction effectiveness ratio for a table.

    Industry target: 50:1 (50 input files per 1 output file).
    Below 10:1 suggests compaction is running too frequently.

    Args:
        session: Database session
        table_id: Table to analyze
        lookback_days: How many days to look back

    Returns:
        Dictionary with effectiveness stats
    """
    since = datetime.now(UTC) - timedelta(days=lookback_days)

    runs = session.execute(
        select(
            func.sum(AuditLog.input_files_count).label("total_input"),
            func.sum(AuditLog.output_files_count).label("total_output"),
            func.count(AuditLog.id).label("run_count"),
            func.avg(AuditLog.duration_ms).label("avg_duration_ms"),
        ).where(
            AuditLog.table_id == table_id,
            AuditLog.action == "optimize",
            func.upper(AuditLog.status) == "OK",
            AuditLog.ts >= since,
            AuditLog.input_files_count.isnot(None),
        )
    ).one()

    total_input = runs.total_input or 0
    total_output = runs.total_output or 0
    run_count = runs.run_count or 0

    ratio = total_input / max(total_output, 1)

    # Classify compaction health
    if run_count == 0:
        effectiveness = "no_data"
    elif ratio >= 50:
        effectiveness = "excellent"  # 50:1 — industry ideal
    elif ratio >= 20:
        effectiveness = "good"
    elif ratio >= 10:
        effectiveness = "fair"
    else:
        effectiveness = "poor"  # Running too often, tables don't need it

    return {
        "total_input_files": total_input,
        "total_output_files": total_output,
        "run_count": run_count,
        "effectiveness_ratio": round(ratio, 1),
        "effectiveness": effectiveness,
        "avg_duration_ms": int(runs.avg_duration_ms or 0),
        "recommendation": _effectiveness_recommendation(effectiveness, ratio),
    }


def _effectiveness_recommendation(effectiveness: str, ratio: float) -> str:
    if effectiveness == "no_data":
        return "No compaction runs recorded yet."
    if effectiveness == "poor":
        return (
            f"Ratio {ratio:.0f}:1 is below 10:1. "
            "Increase min_small_files threshold or cooldown period to avoid wasting compute."
        )
    if effectiveness == "fair":
        return (
            f"Ratio {ratio:.0f}:1 is acceptable but could be improved. "
            "Consider raising min_small_files."
        )
    if effectiveness == "good":
        return f"Ratio {ratio:.0f}:1 is good. Compaction is running efficiently."
    return f"Ratio {ratio:.0f}:1 is excellent. Compaction is highly effective."
