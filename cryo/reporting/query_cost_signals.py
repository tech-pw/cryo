"""Day-over-day / week-over-week Trino query-cost signal, from ``QueryCostAttributionDaily``.

Same same-day-range-only comparison discipline as ``cryo.reporting.cost_signals`` (see that
module's docstring for why a partial-vs-full-period comparison silently produces a wrong
answer, not just a less precise one) -- yesterday vs the day before (DoD) and yesterday vs the
same weekday last week (WoW), never a rolling average against a single point.

Deliberately a SEPARATE signal from ``cost_signals.compute_cost_signal``: that one tracks
Cryo's own fleet STORAGE cost (``TableCostHistory`` -- what tables Cryo scanned cost to hold in
S3); this one tracks Trino QUERY/COMPUTE cost (``QueryCostAttributionDaily`` -- what running
queries against those tables cost to execute). They move for different reasons and on
different noise profiles, so they get their own thresholds (``report_query_cost_*`` in
``cryo.config``, not reused from ``report_cost_*``) rather than being conflated into one signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import QueryCostAttributionDaily

QueryCostDirection = Literal["spike", "decline", "stable"]
QueryCostSignalSource = Literal["dod", "wow"]


@dataclass(frozen=True)
class DailyQueryCostTotals:
    day: date
    query_count: int
    failed_query_count: int
    physical_bytes_scanned: int
    estimated_scan_cost_usd: float | None


@dataclass(frozen=True)
class ClientQueryCost:
    client: str
    query_count: int
    physical_bytes_scanned: int
    estimated_scan_cost_usd: float | None


@dataclass(frozen=True)
class QueryCostSignal:
    direction: QueryCostDirection
    dod_pct: float | None
    wow_pct: float | None
    primary_signal: QueryCostSignalSource | None
    current: DailyQueryCostTotals | None
    dod_baseline: DailyQueryCostTotals | None
    wow_baseline: DailyQueryCostTotals | None
    top_clients_current: list[ClientQueryCost]


def _totals_for_day(session: Session, day: date) -> DailyQueryCostTotals | None:
    """Sum QueryCostAttributionDaily across every source/client/schema for one day.

    Returns None (never a fabricated zero row) when nothing was ingested for that day yet --
    same "no honest number, so no number" rule ``derive_trino_scan_cost_prices`` follows for
    its own per-day price, and the same reason ``cost_signals._daily_fleet_cost`` returns None
    rather than 0 for a day with no snapshot.
    """
    row = session.execute(
        select(
            func.coalesce(func.sum(QueryCostAttributionDaily.query_count), 0),
            func.coalesce(func.sum(QueryCostAttributionDaily.failed_query_count), 0),
            func.coalesce(func.sum(QueryCostAttributionDaily.physical_bytes_scanned), 0),
            func.sum(QueryCostAttributionDaily.estimated_scan_cost_usd),
        ).where(QueryCostAttributionDaily.day == day)
    ).one()
    query_count, failed_count, bytes_scanned, cost_usd = row
    if query_count == 0 and bytes_scanned == 0:
        return None
    return DailyQueryCostTotals(
        day=day,
        query_count=int(query_count),
        failed_query_count=int(failed_count),
        physical_bytes_scanned=int(bytes_scanned),
        estimated_scan_cost_usd=float(cost_usd) if cost_usd is not None else None,
    )


def _top_clients_for_day(
    session: Session, day: date, *, limit: int = 8
) -> list[ClientQueryCost]:
    """Per-client rollup for one day, summed across schemas/sources, ranked by scan cost when
    priced (real $ is the more actionable ranking) and falling back to bytes scanned for days
    the $/GiB price hasn't been derived for yet (see ``derive_trino_scan_cost_prices`` --
    NULL cost is expected, not an error, for a day cost_ceiling hasn't priced)."""
    rows = session.execute(
        select(
            QueryCostAttributionDaily.client,
            func.sum(QueryCostAttributionDaily.query_count),
            func.sum(QueryCostAttributionDaily.physical_bytes_scanned),
            func.sum(QueryCostAttributionDaily.estimated_scan_cost_usd),
        )
        .where(QueryCostAttributionDaily.day == day)
        .group_by(QueryCostAttributionDaily.client)
    ).fetchall()
    clients = [
        ClientQueryCost(
            client=r[0],
            query_count=int(r[1] or 0),
            physical_bytes_scanned=int(r[2] or 0),
            estimated_scan_cost_usd=float(r[3]) if r[3] is not None else None,
        )
        for r in rows
    ]
    # Composite key: priced clients first (ranked by real $), then unpriced ones by bytes --
    # a client with no price yet still needs to be findable in the list, just ranked below
    # anyone with an honest $ figure rather than competing on an invented one.
    clients.sort(
        key=lambda c: (
            c.estimated_scan_cost_usd is not None,
            c.estimated_scan_cost_usd or 0.0,
            c.physical_bytes_scanned,
        ),
        reverse=True,
    )
    return clients[:limit]


def _pct_change(current: float | None, baseline: float | None) -> float | None:
    if current is None or not baseline:
        return None
    return (current - baseline) / baseline * 100


def compute_query_cost_signal(
    session: Session, *, now: datetime | None = None
) -> QueryCostSignal:
    """Compute the DoD and WoW Trino query-cost change and classify it against thresholds.

    Ranks by estimated_scan_cost_usd when available (real $, the actionable number); falls
    back to physical_bytes_scanned pct-change when no day in the comparison has been priced yet
    (cost_ceiling's namespace-cost ingestion can lag query-log ingestion) rather than reporting
    "stable" on a day that may well have moved.
    """
    now = now or datetime.now(UTC)
    settings = get_settings()

    current_day = (now - timedelta(days=1)).date()
    dod_baseline_day = (now - timedelta(days=2)).date()
    wow_baseline_day = (now - timedelta(days=8)).date()

    current = _totals_for_day(session, current_day)
    dod_baseline = _totals_for_day(session, dod_baseline_day)
    wow_baseline = _totals_for_day(session, wow_baseline_day)

    def _metric(totals: DailyQueryCostTotals | None) -> float | None:
        if totals is None:
            return None
        if totals.estimated_scan_cost_usd is not None:
            return totals.estimated_scan_cost_usd
        # Unpriced day -- fall back to bytes scanned (GiB) so a real move still shows up.
        return totals.physical_bytes_scanned / (1024**3)

    current_metric = _metric(current)
    dod_baseline_metric = _metric(dod_baseline)
    wow_baseline_metric = _metric(wow_baseline)

    dod_pct = _pct_change(current_metric, dod_baseline_metric)
    wow_pct = _pct_change(current_metric, wow_baseline_metric)

    direction: QueryCostDirection = "stable"
    primary: QueryCostSignalSource | None = None

    if dod_pct is not None and dod_pct >= settings.report_query_cost_dod_spike_pct:
        direction, primary = "spike", "dod"
    elif dod_pct is not None and dod_pct <= -settings.report_query_cost_dod_decline_pct:
        direction, primary = "decline", "dod"

    if direction == "stable":
        if wow_pct is not None and wow_pct >= settings.report_query_cost_wow_spike_pct:
            direction, primary = "spike", "wow"
        elif wow_pct is not None and wow_pct <= -settings.report_query_cost_wow_decline_pct:
            direction, primary = "decline", "wow"

    top_clients = _top_clients_for_day(session, current_day) if current is not None else []

    return QueryCostSignal(
        direction=direction,
        dod_pct=dod_pct,
        wow_pct=wow_pct,
        primary_signal=primary,
        current=current,
        dod_baseline=dod_baseline,
        wow_baseline=wow_baseline,
        top_clients_current=top_clients,
    )
