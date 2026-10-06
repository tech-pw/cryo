"""S3 Intelligent-Tiering recommendations engine.

Analyzes table access patterns and recommends S3 Intelligent-Tiering
for cost optimization. Can save 40-68% on storage costs for cold data.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from cryo.cost.s3_pricing import S3StorageClass, calculate_monthly_storage_cost
from cryo.db.model_utils import qualified_table_name
from cryo.db.models import AWSIntegration, QueryLog, Source, Table, TableQueryActivity, TableStats


@dataclass
class S3TieringRecommendation:
    """Recommendation for enabling S3 Intelligent-Tiering on a table."""

    table_id: str
    table_name: str
    current_size_gb: float
    avg_file_size_kb: float
    days_since_last_access: int | None
    current_monthly_cost_usd: float
    intelligent_tiering_monthly_cost_usd: float
    estimated_annual_savings_usd: float
    recommendation: str  # "enable", "not_suitable", "already_optimal", "unknown"
    reason: str


def analyze_table_for_tiering(
    session: Session,
    table_id: str,
    lookback_days: int = 90,
    min_file_size_kb: int = 128,
) -> S3TieringRecommendation:
    """Analyze a table for S3 Intelligent-Tiering suitability.

    A missing QueryLog is not "not accessed". When QueryLog has no row in the
    lookback, last access comes from TableQueryActivity. If neither source has
    a row in the lookback, the recommendation is unknown and savings are 0.
    Unknown is not cold.
    """
    table = session.get(Table, table_id)
    if not table:
        raise ValueError(f"Table {table_id} not found")

    sizes = session.execute(
        select(TableStats.total_size_gb, TableStats.total_files).where(
            TableStats.table_id == table.id
        )
    ).one_or_none()
    if sizes is None:
        raise ValueError(f"No stats found for table {table_id}")

    now = datetime.now(UTC)
    return _recommend(
        table_id=str(table_id),
        table_name=qualified_table_name(table),
        current_size_gb=sizes.total_size_gb,
        total_files=sizes.total_files,
        region=_pricing_region(session, table.source_id),
        last_access=lambda: _days_since_last_access(
            session, table.id, lookback_days=lookback_days, now=now
        ),
        lookback_days=lookback_days,
        min_file_size_kb=min_file_size_kb,
    )


def _recommend(
    *,
    table_id: str,
    table_name: str,
    current_size_gb: float,
    total_files: int | None,
    region: str,
    last_access: Callable[[], int | None],
    lookback_days: int,
    min_file_size_kb: int,
) -> S3TieringRecommendation:
    avg_file_size_kb = (current_size_gb * 1024 * 1024) / (total_files or 1)

    if avg_file_size_kb < min_file_size_kb:
        current_monthly_cost = calculate_monthly_storage_cost(
            current_size_gb, S3StorageClass.STANDARD, region=region
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

    days_since_last_access = last_access()
    current_monthly_cost = calculate_monthly_storage_cost(
        current_size_gb, S3StorageClass.STANDARD, region=region
    )

    if days_since_last_access is None:
        return _unknown_recommendation(
            table_id=str(table_id),
            table_name=table_name,
            current_size_gb=current_size_gb,
            avg_file_size_kb=avg_file_size_kb,
            current_monthly_cost=current_monthly_cost,
            lookback_days=lookback_days,
        )

    if days_since_last_access < 30:
        intelligent_tiering_cost = current_monthly_cost
        recommendation = "not_suitable"
        reason = f"Table accessed {days_since_last_access} days ago (< 30 days threshold)"
    elif days_since_last_access < 90:
        intelligent_tiering_cost = calculate_monthly_storage_cost(
            current_size_gb, S3StorageClass.STANDARD_IA, region=region
        )
        annual_savings = (current_monthly_cost - intelligent_tiering_cost) * 12
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
        intelligent_tiering_cost = calculate_monthly_storage_cost(
            current_size_gb, S3StorageClass.GLACIER_IR, region=region
        )
        reduction = _reduction_percent(current_monthly_cost, intelligent_tiering_cost)
        recommendation = "enable"
        reason = (
            f"Table not accessed in {days_since_last_access} days. "
            "Would move to Archive Instant Access tier after 90 days "
            f"({reduction:.0f}% cost reduction)."
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
    rows = session.execute(
        select(Table, TableStats.total_size_gb, TableStats.total_files)
        .join(TableStats)
        .where(TableStats.total_size_gb >= min_size_gb)
        .order_by(TableStats.total_size_gb.desc())
        .options(selectinload(Table.source))
    ).all()
    now = datetime.now(UTC)
    last_access = _days_since_last_access_bulk(
        session, [row[0].id for row in rows], lookback_days=lookback_days, now=now
    )
    regions: dict[object, str] = {}

    recommendations = []
    for table, size_gb, total_files in rows:
        if table.source_id not in regions:
            regions[table.source_id] = _pricing_region(session, table.source_id)
        rec = _recommend(
            table_id=str(table.id),
            table_name=qualified_table_name(table),
            current_size_gb=size_gb,
            total_files=total_files,
            region=regions[table.source_id],
            last_access=partial(last_access.get, table.id),
            lookback_days=lookback_days,
            min_file_size_kb=128,
        )
        if rec.recommendation == "enable":
            recommendations.append(rec)

    recommendations.sort(key=lambda r: r.estimated_annual_savings_usd, reverse=True)
    return recommendations


def _days_since_last_access(
    session: Session,
    table_id: object,
    *,
    lookback_days: int,
    now: datetime,
) -> int | None:
    since = now - timedelta(days=lookback_days)
    last_query = session.execute(
        select(func.max(QueryLog.executed_at)).where(
            QueryLog.table_id == table_id,
            QueryLog.executed_at >= since,
        )
    ).scalar()
    if last_query is not None:
        return max((now - _as_utc(last_query)).days, 0)

    last_day = session.execute(
        select(func.max(TableQueryActivity.day)).where(
            TableQueryActivity.table_id == table_id,
            TableQueryActivity.day >= since.date(),
            TableQueryActivity.query_count > 0,
        )
    ).scalar()
    if last_day is None:
        return None
    return max((now.date() - _as_date(last_day)).days, 0)


def _days_since_last_access_bulk(
    session: Session,
    table_ids: list[uuid.UUID],
    *,
    lookback_days: int,
    now: datetime,
) -> dict[uuid.UUID, int]:
    """Same precedence as :func:`_days_since_last_access`, for many tables in two queries."""
    if not table_ids:
        return {}
    since = now - timedelta(days=lookback_days)
    days: dict[uuid.UUID, int] = {}
    for table_id, last_day in session.execute(
        select(TableQueryActivity.table_id, func.max(TableQueryActivity.day))
        .where(
            TableQueryActivity.table_id.in_(table_ids),
            TableQueryActivity.day >= since.date(),
            TableQueryActivity.query_count > 0,
        )
        .group_by(TableQueryActivity.table_id)
    ):
        days[table_id] = max((now.date() - _as_date(last_day)).days, 0)
    for table_id, last_query in session.execute(
        select(QueryLog.table_id, func.max(QueryLog.executed_at))
        .where(QueryLog.table_id.in_(table_ids), QueryLog.executed_at >= since)
        .group_by(QueryLog.table_id)
    ):
        days[table_id] = max((now - _as_utc(last_query)).days, 0)
    return days


def _unknown_recommendation(
    *,
    table_id: str,
    table_name: str,
    current_size_gb: float,
    avg_file_size_kb: float,
    current_monthly_cost: float,
    lookback_days: int,
) -> S3TieringRecommendation:
    return S3TieringRecommendation(
        table_id=table_id,
        table_name=table_name,
        current_size_gb=current_size_gb,
        avg_file_size_kb=avg_file_size_kb,
        days_since_last_access=None,
        current_monthly_cost_usd=current_monthly_cost,
        intelligent_tiering_monthly_cost_usd=current_monthly_cost,
        estimated_annual_savings_usd=0.0,
        recommendation="unknown",
        reason=_missing_evidence_reason(lookback_days),
    )


def _missing_evidence_reason(lookback_days: int) -> str:
    return (
        "Access evidence is missing: no QueryLog or TableQueryActivity row "
        f"in the last {lookback_days} days. "
        "Missing evidence is not proof the table is cold."
    )


def _pricing_region(session: Session, source_id: object) -> str:
    integration_region = session.execute(
        select(AWSIntegration.region).where(AWSIntegration.source_id == source_id)
    ).scalar()
    resolved = _region_text(integration_region)
    if resolved:
        return resolved
    config = session.execute(select(Source.config).where(Source.id == source_id)).scalar()
    if isinstance(config, dict):
        resolved = _region_text(config.get("s3.region") or config.get("region"))
        if resolved:
            return resolved
    return "us-east-1"


def _region_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _as_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return _as_utc(value).date()
    return value


def _reduction_percent(current_monthly_cost: float, tiered_monthly_cost: float) -> float:
    if current_monthly_cost <= 0:
        return 0.0
    return (current_monthly_cost - tiered_monthly_cost) / current_monthly_cost * 100
