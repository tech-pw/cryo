"""API routes for per-table partition access visualization (cryo.scanner.
s3_request_cost_ingest's PartitionAccessDaily rollup).

Single endpoint: ``GET /api/v1/tables/{table_id}/partition-access`` -- the raw
(day, partition_path, request_count) points a scatter chart plots directly (docs/
PARTITION_ACCESS_VISUALIZATION_PLAN_2026-08.md, inspired by Yelp's engineering blog post on
the same idea), plus a per-partition summary with ``days_since_last_access`` -- directly
reusable by a future retention-tightening rule, though wiring that up is explicitly out of
scope for this endpoint (see the plan doc's "Explicitly out of scope" section).

Read-only, same as routes_cost_attribution.py -- there is no action to take here directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, false, func, select

from cryo.api.deps import SessionDep, require_viewer
from cryo.api.schemas import (
    PartitionAccessPoint,
    PartitionAccessResponse,
    PartitionAccessSummary,
)
from cryo.db.models import PartitionAccessDaily, Table

router = APIRouter(prefix="/api/v1", tags=["partition-access"])

# Applied in the SQL itself (GROUP BY + LIMIT), not after loading every (day, partition,
# role) row into Python. Ranked by recency (most-recently-accessed first).
_MAX_PARTITIONS = 500
# Query(le=90) times the partition cap: one point per selected partition per day.
_MAX_POINTS = _MAX_PARTITIONS * 90


def partition_access_summary_statement(
    table_id: UUID,
    since: date,
    *,
    limit: int = _MAX_PARTITIONS,
) -> Select[Any]:
    """Per-partition totals for the window, capped in SQL."""
    total_requests = func.coalesce(func.sum(PartitionAccessDaily.request_count), 0)
    first_seen = func.min(PartitionAccessDaily.day)
    last_seen = func.max(PartitionAccessDaily.day)
    return (
        select(
            PartitionAccessDaily.partition_path,
            total_requests.label("total_requests"),
            first_seen.label("first_seen"),
            last_seen.label("last_seen"),
        )
        .where(
            PartitionAccessDaily.table_id == table_id,
            PartitionAccessDaily.day >= since,
        )
        .group_by(PartitionAccessDaily.partition_path)
        .order_by(last_seen.desc(), PartitionAccessDaily.partition_path.asc())
        .limit(limit)
    )


def partition_access_points_statement(
    table_id: UUID,
    since: date,
    partition_paths: Sequence[str],
    *,
    limit: int = _MAX_POINTS,
) -> Select[Any]:
    """One point per (day, partition) for the already-capped partition set."""
    request_count = func.coalesce(func.sum(PartitionAccessDaily.request_count), 0)
    path_filter = (
        PartitionAccessDaily.partition_path.in_(tuple(partition_paths))
        if partition_paths
        else false()
    )
    return (
        select(
            PartitionAccessDaily.day,
            PartitionAccessDaily.partition_path,
            request_count.label("request_count"),
        )
        .where(
            PartitionAccessDaily.table_id == table_id,
            PartitionAccessDaily.day >= since,
            path_filter,
        )
        .group_by(PartitionAccessDaily.day, PartitionAccessDaily.partition_path)
        .order_by(PartitionAccessDaily.day, PartitionAccessDaily.partition_path)
        .limit(limit)
    )


@router.get(
    "/tables/{table_id}/partition-access",
    response_model=PartitionAccessResponse,
    dependencies=[Depends(require_viewer)],
)
def get_partition_access(
    session: SessionDep,
    table_id: UUID,
    days: Annotated[int, Query(ge=1, le=90)] = 90,
) -> PartitionAccessResponse:
    table = session.get(Table, table_id)
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")

    now = datetime.now(UTC)
    since = (now - timedelta(days=days)).date()
    today = now.date()

    summary_rows = session.execute(
        partition_access_summary_statement(table_id, since, limit=_MAX_PARTITIONS)
    ).all()
    partitions = [
        PartitionAccessSummary(
            partition_path=row.partition_path,
            total_requests=int(row.total_requests or 0),
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            days_since_last_access=(today - row.last_seen).days,
        )
        for row in summary_rows
    ]
    selected = [row.partition_path for row in partitions]
    if selected:
        point_rows = session.execute(
            partition_access_points_statement(table_id, since, selected, limit=_MAX_POINTS)
        ).all()
    else:
        point_rows = []
    points = [
        PartitionAccessPoint(
            day=row.day,
            partition_path=row.partition_path,
            request_count=int(row.request_count or 0),
        )
        for row in point_rows
    ]

    return PartitionAccessResponse(
        table_id=table_id, since=since, points=points, partitions=partitions
    )
