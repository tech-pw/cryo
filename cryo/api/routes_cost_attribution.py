"""API routes for query-driven cost attribution (cryo.telemetry.cost_attribution).

Single endpoint: ``GET /api/v1/cost-attribution`` -- a "who/what is driving scan cost" report
built from real Trino query-log data (bytes scanned per client/BI-tool and schema), rolled up
daily by the scheduler's ``_maybe_aggregate_cost_attribution`` tick. Read-only; there is no
action to take here directly (compare routes_dark_tables.py's dismiss endpoint or
routes_dedup.py's verify endpoint) -- this is a report, not a recommendation queue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from cryo.api.deps import SessionDep, require_viewer
from cryo.api.schemas import (
    CostAttributionByClient,
    CostAttributionResponse,
    CostAttributionRow,
)
from cryo.db.models import QueryCostAttributionDaily

router = APIRouter(prefix="/api/v1", tags=["cost-attribution"])

_BYTES_PER_GB = 1024**3


@router.get(
    "/cost-attribution",
    response_model=CostAttributionResponse,
    dependencies=[Depends(require_viewer)],
)
def get_cost_attribution(
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> CostAttributionResponse:
    since = (datetime.now(UTC) - timedelta(days=days)).date()

    raw_rows = session.execute(
        select(
            QueryCostAttributionDaily.client,
            QueryCostAttributionDaily.schema_name,
            QueryCostAttributionDaily.query_count,
            QueryCostAttributionDaily.failed_query_count,
            QueryCostAttributionDaily.physical_bytes_scanned,
        ).where(QueryCostAttributionDaily.day >= since)
    ).all()

    # Aggregate in Python across days per (client, schema) -- the underlying table is already
    # a coarse daily rollup (not per-query), so this second aggregation is small and cheap even
    # at the 90-day max window.
    by_client_schema: dict[tuple[str, str], list[int]] = {}
    by_client: dict[str, list[int]] = {}
    for client, schema_name, query_count, failed_count, bytes_scanned in raw_rows:
        key = (client, schema_name)
        bucket = by_client_schema.setdefault(key, [0, 0, 0])
        bucket[0] += query_count
        bucket[1] += failed_count
        bucket[2] += bytes_scanned

        client_bucket = by_client.setdefault(client, [0, 0])
        client_bucket[0] += query_count
        client_bucket[1] += bytes_scanned

    rows = [
        CostAttributionRow(
            client=client,
            schema_name=schema_name,
            query_count=qc,
            failed_query_count=fc,
            physical_bytes_scanned=bs,
            physical_gb_scanned=bs / _BYTES_PER_GB,
        )
        for (client, schema_name), (qc, fc, bs) in sorted(
            by_client_schema.items(), key=lambda item: -item[1][2]
        )
    ]

    by_client_rows = [
        CostAttributionByClient(
            client=client,
            query_count=qc,
            physical_bytes_scanned=bs,
            physical_gb_scanned=bs / _BYTES_PER_GB,
        )
        for client, (qc, bs) in sorted(by_client.items(), key=lambda item: -item[1][1])
    ]

    total_query_count = sum(qc for qc, _ in by_client.values())
    total_bytes = sum(bs for _, bs in by_client.values())

    return CostAttributionResponse(
        since=since,
        rows=rows,
        by_client=by_client_rows,
        total_query_count=total_query_count,
        total_physical_gb_scanned=total_bytes / _BYTES_PER_GB,
    )
