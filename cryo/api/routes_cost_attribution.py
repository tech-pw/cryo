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
            QueryCostAttributionDaily.day,
            QueryCostAttributionDaily.client,
            QueryCostAttributionDaily.schema_name,
            QueryCostAttributionDaily.query_count,
            QueryCostAttributionDaily.failed_query_count,
            QueryCostAttributionDaily.physical_bytes_scanned,
            QueryCostAttributionDaily.estimated_scan_cost_usd,
        ).where(QueryCostAttributionDaily.day >= since)
    ).all()

    # Aggregate in Python across days per (client, schema) -- the underlying table is already
    # a coarse daily rollup (not per-query), so this second aggregation is small and cheap even
    # at the 90-day max window. A missing estimated_scan_cost_usd stays missing: summing it as
    # 0 would turn an unpriced day into a priced zero.
    by_client_schema: dict[tuple[str, str], list[float]] = {}
    by_client: dict[str, list[float]] = {}
    priced_days: set[object] = set()
    for day, client, schema_name, query_count, failed_count, bytes_scanned, scan_cost in raw_rows:
        key = (client, schema_name)
        bucket = by_client_schema.setdefault(key, [0, 0, 0, 0, 0])
        bucket[0] += query_count
        bucket[1] += failed_count
        bucket[2] += bytes_scanned
        if scan_cost is not None:
            bucket[3] += scan_cost
            if bucket[4] == 0:
                bucket[4] = 1
            priced_days.add(day)
        else:
            # A later priced day must not hide this unpriced one. Mixing them and
            # reporting the priced sum would price bytes that were never priced.
            bucket[4] = -1

        client_bucket = by_client.setdefault(client, [0, 0, 0, 0])
        client_bucket[0] += query_count
        client_bucket[1] += bytes_scanned
        if scan_cost is not None:
            client_bucket[2] += scan_cost
            if client_bucket[3] == 0:
                client_bucket[3] = 1
        else:
            client_bucket[3] = -1

    def _priced(total: float, seen: float) -> float | None:
        # seen > 0: every contributing day was priced. seen < 0: at least one day was not,
        # so the sum is not a price for the bytes beside it. seen == 0: nothing was priced.
        return round(total, 4) if seen > 0 else None

    rows = [
        CostAttributionRow(
            client=client,
            schema_name=schema_name,
            query_count=int(qc),
            failed_query_count=int(fc),
            physical_bytes_scanned=int(bs),
            physical_gb_scanned=bs / _BYTES_PER_GB,
            estimated_scan_cost_usd=_priced(cost, seen),
        )
        for (client, schema_name), (qc, fc, bs, cost, seen) in sorted(
            by_client_schema.items(), key=lambda item: -item[1][2]
        )
    ]

    by_client_rows = [
        CostAttributionByClient(
            client=client,
            query_count=int(qc),
            physical_bytes_scanned=int(bs),
            physical_gb_scanned=bs / _BYTES_PER_GB,
            estimated_scan_cost_usd=_priced(cost, seen),
        )
        for client, (qc, bs, cost, seen) in sorted(by_client.items(), key=lambda item: -item[1][1])
    ]

    total_query_count = sum(int(qc) for qc, _, _, _ in by_client.values())
    total_bytes = sum(bs for _, bs, _, _ in by_client.values())
    priced_total = sum(cost for _, _, cost, seen in by_client.values() if seen)

    return CostAttributionResponse(
        since=since,
        rows=rows,
        by_client=by_client_rows,
        total_query_count=total_query_count,
        total_physical_gb_scanned=total_bytes / _BYTES_PER_GB,
        total_estimated_scan_cost_usd=round(priced_total, 4) if priced_days else None,
        scan_cost_priced_days=len(priced_days),
    )
