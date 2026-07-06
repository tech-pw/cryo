"""AutoComp-style workload-aware compaction candidate ranking.

Based on: "AutoComp: Automated Data Compaction for Log-Structured Tables
in Data Lakes" — SIGMOD 2025, LinkedIn (arXiv:2504.04186).

Ranks partitions by combining small-file pressure with observed query
frequency so frequently-queried, fragmented partitions surface first.
Reuses ``cryo.planner.benefit_score.compute_benefit_score`` for the
AutoComp benefit score and adds an S3-style cost-savings estimate.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import QueryLog, TablePartition, TableStats
from cryo.planner.benefit_score import compute_benefit_score

if TYPE_CHECKING:
    pass

Priority = Literal["HIGH", "MED", "LOW"]


@dataclass(frozen=True)
class CompactionCandidate:
    partition_path: str
    partition_size_gb: float
    small_files: int
    query_freq_per_day: float
    p95_latency_ms: float
    score: float
    priority: Priority
    savings_estimate_usd: float
    reasons: list[str] = field(default_factory=list)


def _build_reasons(
    small_files: int,
    total_files: int,
    query_freq: float,
    partition_size_gb: float,
    target_file_size_bytes: int,
) -> list[str]:
    reasons: list[str] = []
    if query_freq >= 1.0:
        reasons.append(f"high query frequency ({query_freq:.1f} queries/day)")
    elif query_freq > 0:
        reasons.append(f"moderate query frequency ({query_freq:.1f} queries/day)")

    if total_files > 0 and small_files > 0:
        small_ratio = small_files / total_files
        if small_ratio >= 0.5:
            reasons.append(f"{int(round(small_ratio * 100))}% of files are small")
        elif small_ratio >= 0.2:
            reasons.append(f"{int(round(small_ratio * 100))}% of files are small")

    if target_file_size_bytes > 0 and partition_size_gb > 0:
        implied_file_mb = (partition_size_gb * 1024 * 1024 * 1024) / max(small_files, 1)
        implied_mb = implied_file_mb / (1024 * 1024)
        target_mb = target_file_size_bytes / (1024 * 1024)
        if implied_mb < target_mb * 0.5:
            reasons.append(
                f"avg {implied_mb:.0f}MB vs {target_mb:.0f}MB target — well below target size"
            )

    if not reasons:
        reasons.append("candidate based on AutoComp benefit score")
    return reasons


def _latest_partitions(session: Session, table_id: uuid.UUID) -> list[TablePartition]:
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
            )
            .limit(10000)
        )
    )


def _query_freq_for_table(
    session: Session,
    table_id: uuid.UUID,
    horizon_days: int,
) -> float:
    since = datetime.now(UTC) - timedelta(days=horizon_days)
    total = (
        session.scalar(
            select(func.count(QueryLog.id)).where(
                QueryLog.table_id == table_id,
                QueryLog.executed_at >= since,
            )
        )
        or 0
    )
    return float(total) / max(horizon_days, 1)


def _table_p95_latency(
    session: Session,
    table_id: uuid.UUID,
    horizon_days: int,
) -> float:
    since = datetime.now(UTC) - timedelta(days=horizon_days)
    p95 = session.scalar(
        select(func.percentile_cont(0.95).within_group(QueryLog.duration_ms)).where(
            QueryLog.table_id == table_id,
            QueryLog.executed_at >= since,
            QueryLog.duration_ms.is_not(None),
        )
    )
    return float(p95) if p95 is not None else 0.0


def _stats_lookup(session: Session, table_id: uuid.UUID) -> TableStats | None:
    return session.get(TableStats, table_id)


def find_candidates(
    session: Session,
    table_id: uuid.UUID,
    horizon_days: int = 30,
    k: int = 10,
    s3_storage_cost_per_gb_month: float | None = None,
) -> list[CompactionCandidate]:
    """Return top-``k`` AutoComp-style compaction candidates for a table.

    Args:
        session: SQLAlchemy session.
        table_id: Table to rank partitions for.
        horizon_days: Window for query-frequency and p95-latency.
        k: Maximum number of candidates to return.
        s3_storage_cost_per_gb_month: Override unit price (defaults to settings).
    """
    settings = get_settings()
    price_per_gb_month = s3_storage_cost_per_gb_month or settings.cost_price_per_gb_month

    partitions = _latest_partitions(session, table_id)
    if not partitions:
        return []

    query_freq = _query_freq_for_table(session, table_id, horizon_days)
    p95_latency = _table_p95_latency(session, table_id, horizon_days)
    stats = _stats_lookup(session, table_id)
    target_file_bytes = (
        stats.target_file_size_bytes if stats and stats.target_file_size_bytes else 0
    )

    candidates: list[CompactionCandidate] = []
    for partition in partitions:
        small_files = int(partition.small_files_in_partition_count or 0)
        total_files = int(partition.files_per_partition or 0)
        partition_size_gb = float(partition.partition_size_gb or 0.0)
        if small_files <= 0 or total_files <= 0:
            continue

        denom = max(partition_size_gb, 0.1)
        score = (small_files * query_freq * 1.0) / denom

        benefit_score = compute_benefit_score(
            small_files=small_files,
            query_frequency=query_freq,
            partition_size_gb=partition_size_gb,
        )
        if benefit_score >= 5.0:
            priority: Priority = "HIGH"
        elif benefit_score >= 1.0:
            priority = "MED"
        else:
            priority = "LOW"

        savings = price_per_gb_month * partition_size_gb * 12 * (small_files / total_files)

        reasons = _build_reasons(
            small_files=small_files,
            total_files=total_files,
            query_freq=query_freq,
            partition_size_gb=partition_size_gb,
            target_file_size_bytes=target_file_bytes,
        )

        candidates.append(
            CompactionCandidate(
                partition_path=partition.partition_path or partition.partition_key or "",
                partition_size_gb=round(partition_size_gb, 6),
                small_files=small_files,
                query_freq_per_day=round(query_freq, 4),
                p95_latency_ms=round(p95_latency, 2),
                score=round(score, 4),
                priority=priority,
                savings_estimate_usd=round(savings, 4),
                reasons=reasons,
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[: max(k, 0)]


__all__ = ["CompactionCandidate", "Priority", "find_candidates"]