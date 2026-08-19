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

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from cryo.api.deps import SessionDep, require_viewer
from cryo.api.schemas import (
    PartitionAccessPoint,
    PartitionAccessResponse,
    PartitionAccessSummary,
)
from cryo.db.models import PartitionAccessDaily, Table

router = APIRouter(prefix="/api/v1", tags=["partition-access"])

# Bounds the response size regardless of how many distinct partitions a table has -- a
# table with thousands of partitions (e.g. hourly-partitioned over years) shouldn't ship its
# entire history to the browser for a chart. Ranked by recency (most-recently-accessed first)
# since that's the axis a retention/storage-class decision actually cares about.
_MAX_PARTITIONS = 500


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

    rows = session.execute(
        select(
            PartitionAccessDaily.day,
            PartitionAccessDaily.partition_path,
            PartitionAccessDaily.request_count,
        ).where(
            PartitionAccessDaily.table_id == table_id,
            PartitionAccessDaily.day >= since,
        )
    ).all()

    # The scatter chart wants one point per (day, partition) -- this table's own grain also
    # includes requester_role, so collapse that dimension here (a per-role breakdown is
    # explicitly out of scope for this pass, see the plan doc).
    points_by_key: dict[tuple, int] = {}
    for day, partition_path, request_count in rows:
        key = (day, partition_path)
        points_by_key[key] = points_by_key.get(key, 0) + request_count

    points = [
        PartitionAccessPoint(day=day, partition_path=partition_path, request_count=count)
        for (day, partition_path), count in sorted(points_by_key.items())
    ]

    # Per-partition summary across the full window, for the sidebar/table view alongside the
    # scatter chart and for days_since_last_access (the number a retention decision would
    # actually key off of).
    by_partition: dict[str, list] = {}
    for day, partition_path, request_count in rows:
        bucket = by_partition.setdefault(partition_path, [0, day, day])
        bucket[0] += request_count
        if day < bucket[1]:
            bucket[1] = day
        if day > bucket[2]:
            bucket[2] = day

    partitions = sorted(
        (
            PartitionAccessSummary(
                partition_path=partition_path,
                total_requests=total,
                first_seen=first_seen,
                last_seen=last_seen,
                days_since_last_access=(today - last_seen).days,
            )
            for partition_path, (total, first_seen, last_seen) in by_partition.items()
        ),
        key=lambda summary: summary.days_since_last_access,
    )[:_MAX_PARTITIONS]

    return PartitionAccessResponse(
        table_id=table_id, since=since, points=points, partitions=partitions
    )
