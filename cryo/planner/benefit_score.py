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
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, QueryLog, Table, TablePartition

logger = logging.getLogger(__name__)

# How many hours of query history to use for frequency calculation
_QUERY_WINDOW_HOURS = 168  # 7 days

# Minimum benefit score to be eligible for compaction (below this = skip)
DEFAULT_MIN_BENEFIT_SCORE = 1.0

# Cost factor: relative cost estimate of rewriting N GB
# (pure heuristic: 1 GB ≈ cost_factor=1 unit)
_COST_PER_GB = 1.0


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


def update_partition_benefit_scores(
    session: Session,
    table_id: uuid.UUID,
    query_window_hours: int = _QUERY_WINDOW_HOURS,
) -> int:
    """Update compaction_benefit_score for all partitions of a table.

    This should be called:
    - After the scanner updates partition stats
    - After new query logs are ingested

    Args:
        session: Database session
        table_id: Table to update scores for
        query_window_hours: Hours of query history to use

    Returns:
        Number of partitions updated
    """
    since = datetime.now(UTC) - timedelta(hours=query_window_hours)

    # Count total queries for this table in the window
    total_queries = (
        session.scalar(
            select(func.count(QueryLog.id)).where(
                QueryLog.table_id == table_id,
                QueryLog.executed_at >= since,
            )
        )
        or 0
    )

    # Queries per day
    days_in_window = query_window_hours / 24.0
    queries_per_day = total_queries / max(days_in_window, 1.0)

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

    # For now, all partitions share the table-level query frequency.
    # Future: join with per-partition filter predicates to get partition-level frequency.
    updated = 0
    for partition in partitions:
        small_files = partition.small_files_in_partition_count or 0
        size_gb = partition.partition_size_gb or 0.1

        score = compute_benefit_score(
            small_files=small_files,
            query_frequency=queries_per_day,
            partition_size_gb=size_gb,
        )

        partition.query_frequency = queries_per_day
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

    total = 0
    for table in tables:
        total += update_partition_benefit_scores(session, table.id)
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
    return list(
        session.scalars(
            select(TablePartition)
            .where(
                TablePartition.table_id == table_id,
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
        key=lambda p: (p.small_files_in_partition_count or 0),
        reverse=True,
    )[:limit]
    autocomp_sorted = sorted(
        partitions,
        key=lambda p: (p.compaction_benefit_score or 0.0),
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
