"""S3 Intelligent-Tiering recommendations engine.

Analyzes table access patterns and recommends S3 Intelligent-Tiering
for cost optimization. Can save 40-68% on storage costs for cold data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.cost.s3_pricing import S3StorageClass, calculate_monthly_storage_cost
from cryo.db.model_utils import qualified_table_name
from cryo.db.models import QueryLog, Table, TableStats


@dataclass
class S3TieringRecommendation:
    """Recommendation for enabling S3 Intelligent-Tiering on a table."""

    table_id: str
    table_name: str
    current_size_gb: float
    avg_file_size_kb: float
    days_since_last_access: int
    current_monthly_cost_usd: float
    intelligent_tiering_monthly_cost_usd: float
    estimated_annual_savings_usd: float
    recommendation: str  # "enable", "not_suitable", "already_optimal"
    reason: str


def analyze_table_for_tiering(
    session: Session,
    table_id: str,
    lookback_days: int = 90,
    min_file_size_kb: int = 128,
) -> S3TieringRecommendation:
    """
    Analyze a table for S3 Intelligent-Tiering suitability.

    Args:
        session: Database session
        table_id: Table UUID
        lookback_days: Days to look back for access patterns (default: 90)
        min_file_size_kb: Minimum file size for Intelligent-Tiering (default: 128 KB)

    Returns:
        S3TieringRecommendation with analysis results
    """
    # Get table stats
    table = session.get(Table, table_id)
    if not table:
        raise ValueError(f"Table {table_id} not found")

    stats = session.get(TableStats, table_id)
    if not stats:
        raise ValueError(f"No stats found for table {table_id}")

    table_name = qualified_table_name(table)
    current_size_gb = stats.total_size_gb
    total_files = stats.total_files or 1
    avg_file_size_kb = (current_size_gb * 1024 * 1024) / total_files

    # Check if files are large enough for Intelligent-Tiering
    if avg_file_size_kb < min_file_size_kb:
        current_monthly_cost = calculate_monthly_storage_cost(
            current_size_gb, S3StorageClass.STANDARD
        )
        return S3TieringRecommendation(
            table_id=str(table_id),
            table_name=table_name,
            current_size_gb=current_size_gb,
            avg_file_size_kb=avg_file_size_kb,
            days_since_last_access=0,
            current_monthly_cost_usd=current_monthly_cost,
            intelligent_tiering_monthly_cost_usd=0,
            estimated_annual_savings_usd=0,
            recommendation="not_suitable",
            reason=(
                f"Average file size ({avg_file_size_kb:.0f} KB) is below "
                f"Intelligent-Tiering minimum ({min_file_size_kb} KB)"
            ),
        )

    # Check last access time from query logs
    since = datetime.now(UTC) - timedelta(days=lookback_days)
    last_access_stmt = (
        select(func.max(QueryLog.executed_at))
        .where(QueryLog.table_id == table.id)
        .where(QueryLog.executed_at >= since)
    )
    last_access = session.execute(last_access_stmt).scalar()

    if last_access:
        days_since_last_access = (datetime.now(UTC) - last_access).days
    else:
        # No queries in lookback period - assume maximum
        days_since_last_access = lookback_days

    # Calculate cost savings using centralized S3 pricing
    current_monthly_cost = calculate_monthly_storage_cost(current_size_gb, S3StorageClass.STANDARD)

    if days_since_last_access < 30:
        # Too recently accessed - no savings yet
        intelligent_tiering_cost = current_monthly_cost
        recommendation = "not_suitable"
        reason = f"Table accessed {days_since_last_access} days ago (< 30 days threshold)"
    elif days_since_last_access < 90:
        # Would move to Infrequent Access tier
        intelligent_tiering_cost = calculate_monthly_storage_cost(
            current_size_gb, S3StorageClass.STANDARD_IA
        )
        monthly_savings = current_monthly_cost - intelligent_tiering_cost
        annual_savings = monthly_savings * 12

        if annual_savings > 100:
            recommendation = "enable"
            reason = (
                f"Table not accessed in {days_since_last_access} days. "
                "Would move to Infrequent Access tier after 30 days."
            )
        else:
            recommendation = "not_suitable"
            reason = f"Estimated savings (${annual_savings:.2f}/year) too low to justify"
    else:
        # Would move to Archive Instant Access tier
        intelligent_tiering_cost = calculate_monthly_storage_cost(
            current_size_gb, S3StorageClass.GLACIER_IR
        )
        monthly_savings = current_monthly_cost - intelligent_tiering_cost
        annual_savings = monthly_savings * 12

        recommendation = "enable"
        reason = (
            f"Table not accessed in {days_since_last_access} days. "
            "Would move to Archive Instant Access tier after 90 days "
            "(82% cost reduction)."
        )

    estimated_annual_savings = (current_monthly_cost - intelligent_tiering_cost) * 12

    return S3TieringRecommendation(
        table_id=str(table_id),
        table_name=table_name,
        current_size_gb=current_size_gb,
        avg_file_size_kb=avg_file_size_kb,
        days_since_last_access=days_since_last_access,
        current_monthly_cost_usd=current_monthly_cost,
        intelligent_tiering_monthly_cost_usd=intelligent_tiering_cost,
        estimated_annual_savings_usd=estimated_annual_savings,
        recommendation=recommendation,
        reason=reason,
    )


def get_all_tiering_recommendations(
    session: Session,
    min_size_gb: float = 100.0,
    lookback_days: int = 90,
) -> list[S3TieringRecommendation]:
    """
    Get S3 Intelligent-Tiering recommendations for all large tables.

    Args:
        session: Database session
        min_size_gb: Minimum table size to consider (default: 100 GB)
        lookback_days: Days to look back for access patterns

    Returns:
        List of recommendations sorted by estimated annual savings
    """
    # Get all large tables
    stmt = (
        select(Table.id)
        .join(TableStats)
        .where(TableStats.total_size_gb >= min_size_gb)
        .order_by(TableStats.total_size_gb.desc())
    )

    table_ids = session.execute(stmt).scalars().all()

    recommendations = []
    for table_id in table_ids:
        try:
            rec = analyze_table_for_tiering(session, str(table_id), lookback_days=lookback_days)
            if rec.recommendation == "enable":
                recommendations.append(rec)
        except Exception:
            # Skip tables with errors
            continue

    # Sort by estimated annual savings descending
    recommendations.sort(key=lambda r: r.estimated_annual_savings_usd, reverse=True)

    return recommendations
