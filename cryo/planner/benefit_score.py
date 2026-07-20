"""AutoComp-style compaction benefit scoring.

Based on: "AutoComp: Automated Data Compaction for Log-Structured Tables
in Data Lakes" — SIGMOD 2025, LinkedIn.

Core idea: compact a partition only when the expected scan savings
× query frequency exceed the estimated compaction cost.

Score = (small_files × query_frequency) / estimated_rewrite_cost_factor
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, QueryLog, Table, TablePartition
from cryo.domain.enums import PartitionTransform
from cryo.planner.partition_query_affinity import MIN_COVERAGE_RATIO, compute_partition_affinity
from cryo.planner.partitions import PartitionFieldSpec, normalize_transform

logger = logging.getLogger(__name__)

# How many hours of query history to use for frequency calculation
_QUERY_WINDOW_HOURS = 168  # 7 days

# Minimum benefit score to be eligible for compaction (below this = skip)
DEFAULT_MIN_BENEFIT_SCORE = 1.0

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
    value_per_query_second_usd: float = 0.0002,
    rewrite_cost_per_gb_usd: float = 0.025,
    write_amp_multiplier: float = 1.15,
) -> BenefitCostBreakdown:
    """Compute monthly ROI terms in USD for smart-maintenance gating."""
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
    # translate to larger planning/pruning wins.
    latency_seconds_saved_per_query = (small_files / 2000.0) + (delete_pct / 100.0 * 0.35)
    latency_benefit = query_count_30d * latency_seconds_saved_per_query * value_per_query_second_usd

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


def compute_benefit_score(
    small_files: int,
    query_frequency: float,
    partition_size_gb: float,
) -> float:
    """Compute a compaction benefit score for a partition.

    Args:
        small_files: Number of small files in the partition
        query_frequency: Average queries per day hitting this partition
        partition_size_gb: Size of the partition in GB (proxy for rewrite cost)

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

    # Rewrite cost scales with data volume
    rewrite_cost = max(partition_size_gb * _COST_PER_GB, 0.1)

    score = (scan_savings * frequency_multiplier) / rewrite_cost
    return round(score, 4)


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

    partition_frequency = _partition_query_frequency(
        session,
        table_id=table_id,
        since=since,
        days_in_window=days_in_window,
        partitions=partitions,
    )

    updated = 0
    for partition in partitions:
        small_files = partition.small_files_in_partition_count or 0
        size_gb = partition.partition_size_gb or 0.1
        frequency = (
            partition_frequency.get(partition.partition_value, queries_per_day)
            if partition.partition_value is not None
            else queries_per_day
        )

        score = compute_benefit_score(
            small_files=small_files,
            query_frequency=frequency,
            partition_size_gb=size_gb,
        )

        partition.query_frequency = frequency
        partition.compaction_benefit_score = score
        updated += 1

    session.commit()
    logger.info(f"Updated benefit scores for {updated} partitions of table {table_id}")
    return updated


def update_all_benefit_scores(session: Session) -> int:
    """Update benefit scores for all active tables.

    Called by the governor/scheduler on a regular cadence
    (e.g., every 4 hours) after query log ingestion.
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

    total = 0
    for table_id in table_ids:
        total += update_partition_benefit_scores(session, table_id)
        # update_partition_benefit_scores already commits its own partitions before returning;
        # expunge right after so this table's (and its partitions') objects don't linger either.
        session.expunge_all()
    return total


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
    """Compare static small-files ordering vs AutoComp benefit-score ordering."""
    from cryo.db.models import TablePartition

    latest_scan = session.scalar(
        select(func.max(TablePartition.scan_at)).where(TablePartition.table_id == table_id)
    )
    if not latest_scan:
        return {"static_order": [], "autocomp_order": [], "improvement_ratio": 0.0}

    partitions = list(
        session.scalars(
            select(TablePartition).where(
                TablePartition.table_id == table_id,
                TablePartition.scan_at == latest_scan,
            )
        )
    )

    static_sorted = sorted(
        partitions,
        key=lambda p: p.small_files_in_partition_count or 0,
        reverse=True,
    )[:limit]
    autocomp_sorted = sorted(
        partitions,
        key=lambda p: p.compaction_benefit_score or 0.0,
        reverse=True,
    )[:limit]

    static_score = sum(p.small_files_in_partition_count or 0 for p in static_sorted[:10])
    autocomp_score = sum(
        (p.compaction_benefit_score or 0.0) * (p.small_files_in_partition_count or 0)
        for p in autocomp_sorted[:10]
    )
    improvement = autocomp_score / max(static_score, 1.0)

    return {
        "static_order": [str(p.partition_value) for p in static_sorted[:10]],
        "autocomp_order": [str(p.partition_value) for p in autocomp_sorted[:10]],
        "improvement_ratio": round(improvement, 4),
        "autocomp_wins": improvement > 1.0,
    }


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
            AuditLog.status == "ok",
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
