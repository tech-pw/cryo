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

from cryo.config import Settings, get_settings
from cryo.db.models import QueryCostAttributionDaily

QueryCostDirection = Literal["spike", "decline", "stable"]
QueryCostSignalSource = Literal["dod", "wow"]

# A client we can't identify at all -- see _normalize_client in cost_attribution.py, the
# literal string it falls back to when the ingested `source` field is empty/null. Surfaced
# separately in the report rather than left to blend into an ordinary-looking table row: an
# "unknown" client that's a top-3 spender is itself worth a flag (misconfigured client,
# missing source string, or a client nobody's tracking) independent of whether cost moved.
UNKNOWN_CLIENT = "unknown"
# Same threshold philosophy as report_query_cost_* -- a fixed starting point, not tuned to any
# deployment. Below this share of total cost, an unidentified client isn't worth flagging on
# its own (some background noise from unlabeled connections is normal).
UNKNOWN_CLIENT_FLAG_SHARE_PCT = 10.0


@dataclass(frozen=True)
class DailyQueryCostTotals:
    day: date
    query_count: int
    failed_query_count: int
    physical_bytes_scanned: int
    estimated_scan_cost_usd: float | None

    @property
    def failure_rate_pct(self) -> float | None:
        """None (not 0.0) when there were no queries at all -- a 0-query day has no honest
        failure rate to report, same "no data, no fabricated number" rule the rest of this
        module follows."""
        if self.query_count == 0:
            return None
        return self.failed_query_count / self.query_count * 100


@dataclass(frozen=True)
class ClientQueryCost:
    client: str
    query_count: int
    physical_bytes_scanned: int
    estimated_scan_cost_usd: float | None


@dataclass(frozen=True)
class UserQueryCost:
    """Same shape as ClientQueryCost, grouped by the Trino user who ran the query instead of
    the client/BI tool -- see docs/QUERY_COST_USER_ATTRIBUTION_PLAN_2026-08.md. Kept as a
    separate dataclass rather than generalizing ClientQueryCost with a dimension field: the two
    read identically today, but "which dimension is this" being encoded in the type (not a
    runtime flag) means a caller can't accidentally render a client list under a "Top users"
    header or vice versa."""

    user_name: str
    query_count: int
    physical_bytes_scanned: int
    estimated_scan_cost_usd: float | None


@dataclass(frozen=True)
class FailureRateSignal:
    """DoD/WoW change in failure *rate* (failed/total), not raw failed-query count.

    Rate, not count, is the honest comparison: a day with 2x the query volume will also have
    ~2x the failed queries even if nothing got worse -- comparing raw counts would flag that as
    a regression when it isn't one. Reuses the fleet query-cost thresholds
    (report_query_cost_dod/wow_spike_pct) as the failure-rate move threshold too, since both
    are "how much did this pct metric move" questions with the same noise tolerance -- not
    worth a third set of tunables until real deployment experience says otherwise.
    """

    direction: QueryCostDirection  # "spike" = rate got worse, "decline" = rate improved
    # Percentage-POINT change (e.g. 8.3% -> 9.1% = +0.8), not a percent-of-a-percent.
    dod_pct_points: float | None
    wow_pct_points: float | None
    primary_signal: QueryCostSignalSource | None
    current_rate_pct: float | None
    dod_baseline_rate_pct: float | None
    wow_baseline_rate_pct: float | None


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
    top_users_current: list[UserQueryCost]
    failure_signal: FailureRateSignal | None
    unknown_client_share_pct: float | None  # None if no "unknown" rows that day


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


def _top_users_for_day(session: Session, day: date, *, limit: int = 8) -> list[UserQueryCost]:
    """Per-user rollup for one day -- same composite-key ranking as ``_top_clients_for_day``
    (priced first by real $, unpriced falls back to bytes scanned), grouped by ``user_name``
    instead of ``client``. Rows ingested before the user-attribution migration (or from a
    source with ``user_column`` disabled) all roll up under ``user_name='unknown'`` -- a single
    large "unknown" bucket there is expected, not a bug; see cost_attribution.py's
    ``_normalize_user``."""
    rows = session.execute(
        select(
            QueryCostAttributionDaily.user_name,
            func.sum(QueryCostAttributionDaily.query_count),
            func.sum(QueryCostAttributionDaily.physical_bytes_scanned),
            func.sum(QueryCostAttributionDaily.estimated_scan_cost_usd),
        )
        .where(QueryCostAttributionDaily.day == day)
        .group_by(QueryCostAttributionDaily.user_name)
    ).fetchall()
    users = [
        UserQueryCost(
            user_name=r[0],
            query_count=int(r[1] or 0),
            physical_bytes_scanned=int(r[2] or 0),
            estimated_scan_cost_usd=float(r[3]) if r[3] is not None else None,
        )
        for r in rows
    ]
    users.sort(
        key=lambda u: (
            u.estimated_scan_cost_usd is not None,
            u.estimated_scan_cost_usd or 0.0,
            u.physical_bytes_scanned,
        ),
        reverse=True,
    )
    return users[:limit]


def _pct_change(current: float | None, baseline: float | None) -> float | None:
    if current is None or not baseline:
        return None
    return (current - baseline) / baseline * 100


def _points_change(current: float | None, baseline: float | None) -> float | None:
    """Percentage-POINT delta (8.3% -> 9.1% = +0.8), not a percent-change-of-a-percent -- the
    right comparison for a rate metric like failure_rate_pct. Unlike _pct_change, a baseline of
    exactly 0 is a legitimate value here (0% failures yesterday is real, not "no baseline"), so
    this only returns None when either side is actually missing."""
    if current is None or baseline is None:
        return None
    return current - baseline


def compute_failure_rate_signal(
    current: DailyQueryCostTotals | None,
    dod_baseline: DailyQueryCostTotals | None,
    wow_baseline: DailyQueryCostTotals | None,
    *,
    settings: Settings | None = None,
) -> FailureRateSignal | None:
    """DoD/WoW failure-rate signal. Returns None only when there's no current-day data at all
    (nothing to report); a current day with no DoD/WoW baseline still returns a signal (direction
    "stable", pct_points None) so the report can show today's raw rate even without a comparison.
    """
    if current is None:
        return None
    settings = settings or get_settings()

    current_rate = current.failure_rate_pct
    dod_rate = dod_baseline.failure_rate_pct if dod_baseline is not None else None
    wow_rate = wow_baseline.failure_rate_pct if wow_baseline is not None else None

    dod_points = _points_change(current_rate, dod_rate)
    wow_points = _points_change(current_rate, wow_rate)

    direction: QueryCostDirection = "stable"
    primary: QueryCostSignalSource | None = None

    # Reuses the fleet query-cost pct thresholds as point-thresholds -- see FailureRateSignal's
    # docstring for why a third set of tunables isn't justified yet.
    if dod_points is not None and dod_points >= settings.report_query_cost_dod_spike_pct:
        direction, primary = "spike", "dod"
    elif dod_points is not None and dod_points <= -settings.report_query_cost_dod_decline_pct:
        direction, primary = "decline", "dod"

    if direction == "stable":
        if wow_points is not None and wow_points >= settings.report_query_cost_wow_spike_pct:
            direction, primary = "spike", "wow"
        elif wow_points is not None and wow_points <= -settings.report_query_cost_wow_decline_pct:
            direction, primary = "decline", "wow"

    return FailureRateSignal(
        direction=direction,
        dod_pct_points=dod_points,
        wow_pct_points=wow_points,
        primary_signal=primary,
        current_rate_pct=current_rate,
        dod_baseline_rate_pct=dod_rate,
        wow_baseline_rate_pct=wow_rate,
    )


def _unknown_client_totals(session: Session, day: date) -> ClientQueryCost | None:
    """The "unknown" client's own row for one day, summed across schemas -- a dedicated lookup
    rather than reading it off ``_top_clients_for_day``'s result, because that list is
    limit-truncated (default top 8) and computing a share against a truncated list would give a
    wrong denominator whenever there are more than 8 clients that day."""
    row = session.execute(
        select(
            func.sum(QueryCostAttributionDaily.query_count),
            func.sum(QueryCostAttributionDaily.physical_bytes_scanned),
            func.sum(QueryCostAttributionDaily.estimated_scan_cost_usd),
        ).where(
            QueryCostAttributionDaily.day == day,
            QueryCostAttributionDaily.client == UNKNOWN_CLIENT,
        )
    ).one()
    query_count, bytes_scanned, cost_usd = row
    if not query_count:
        return None
    return ClientQueryCost(
        client=UNKNOWN_CLIENT,
        query_count=int(query_count),
        physical_bytes_scanned=int(bytes_scanned or 0),
        estimated_scan_cost_usd=float(cost_usd) if cost_usd is not None else None,
    )


def _unknown_client_share(
    session: Session, day: date, current_totals: DailyQueryCostTotals | None
) -> float | None:
    """% of the day's TRUE total (every client, not just the top-N shown in the report)
    attributable to the "unknown" client -- flagged separately in the report regardless of
    whether overall cost moved, since an unidentified top spender is its own finding. Returns
    None when there's no unknown-client row that day (nothing to flag) rather than 0.0, so the
    report can distinguish "checked, none found" from "not computed"."""
    if current_totals is None:
        return None
    unknown = _unknown_client_totals(session, day)
    if unknown is None:
        return None
    if current_totals.estimated_scan_cost_usd and unknown.estimated_scan_cost_usd is not None:
        return unknown.estimated_scan_cost_usd / current_totals.estimated_scan_cost_usd * 100
    if current_totals.physical_bytes_scanned:
        return unknown.physical_bytes_scanned / current_totals.physical_bytes_scanned * 100
    return None


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
    top_users = _top_users_for_day(session, current_day) if current is not None else []
    failure_signal = compute_failure_rate_signal(
        current, dod_baseline, wow_baseline, settings=settings
    )
    unknown_share = _unknown_client_share(session, current_day, current)

    return QueryCostSignal(
        direction=direction,
        dod_pct=dod_pct,
        wow_pct=wow_pct,
        primary_signal=primary,
        current=current,
        dod_baseline=dod_baseline,
        wow_baseline=wow_baseline,
        top_clients_current=top_clients,
        top_users_current=top_users,
        failure_signal=failure_signal,
        unknown_client_share_pct=unknown_share,
    )
