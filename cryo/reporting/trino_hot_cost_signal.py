"""Is the daily top-N-by-Trino-frequency preset actually reducing Trino scan cost?

Neither impact_signals.py (bytes RECLAIMED by maintenance, fleet-wide, engine-agnostic) nor
vitals.py (is the machine healthy) answers this: the Trino-hot preset (cryo.scheduler.
trino_hot_preset) exists specifically to cut Trino scan cost by keeping the tables Trino
queries most in good shape, and there was no signal anywhere confirming that lever is actually
working -- "we ran jobs on the top 500 tables" and "Trino's own bytes-scanned cost for those
tables went down" are different claims, and only the second one is the actual point.

Same same-day-range DoD/WoW discipline as impact_signals.py/cost_signals.py (a partial period
compared against a full one produced a genuinely wrong answer once already -- see those
modules' docstrings), and the same honest-about-coverage discipline as impact_signals.py's
MeasurementState: bytes scanned is real and always available from QueryLog; a real $ figure is
only available when Settings.trino_cost_pricing_enabled has actually priced that day (see
cryo.cost.model.derive_trino_scan_cost_prices) -- reported as None, never silently omitted or
faked, when it isn't.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.db.models import Preset, QueryCostAttributionDaily, QueryLog, TableAssignment

_TRINO_ENGINE = "trino"
_GIB = 1024**3

CohortTrend = Literal["improving", "worsening", "flat", "unknown"]


@dataclass(frozen=True)
class DailyCohortScan:
    day_start: datetime
    day_end: datetime
    cohort_size: int
    trino_query_count: int
    trino_bytes_scanned: int
    # None when that day's $/GiB price hasn't been derived (pricing off, or not yet run) --
    # never a guessed number standing in for a missing one.
    estimated_scan_cost_usd: float | None

    @property
    def gib_scanned(self) -> float:
        return self.trino_bytes_scanned / _GIB


@dataclass(frozen=True)
class TrinoHotCostSignal:
    preset_name: str
    trend: CohortTrend
    dod_bytes_pct: float | None
    wow_bytes_pct: float | None
    today: DailyCohortScan
    dod_baseline: DailyCohortScan | None
    wow_baseline: DailyCohortScan | None


def _day_bounds(now: datetime, days_ago: int) -> tuple[datetime, datetime]:
    anchor = (now - timedelta(days=days_ago)).date()
    start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _cohort_table_ids(session: Session, preset_id) -> list:
    return list(
        session.scalars(
            select(TableAssignment.table_id).where(TableAssignment.preset_id == preset_id)
        ).all()
    )


def _price_for_day(session: Session, day: date) -> float | None:
    """Same $/GiB reconstruction cryo.intelligence.outcomes._table_scan_cost_usd uses -- see
    that function's docstring for why this is derived from QueryCostAttributionDaily's own
    cost/bytes ratio rather than a per-table cost column (none exists in the schema)."""
    row = session.execute(
        select(
            func.sum(QueryCostAttributionDaily.estimated_scan_cost_usd),
            func.sum(QueryCostAttributionDaily.physical_bytes_scanned),
        ).where(
            QueryCostAttributionDaily.day == day,
            QueryCostAttributionDaily.estimated_scan_cost_usd.isnot(None),
        )
    ).one()
    cost_sum, bytes_sum = row
    if cost_sum is None or not bytes_sum:
        return None
    return float(cost_sum) / (float(bytes_sum) / _GIB)


def _daily_cohort_scan(
    session: Session, table_ids: list, day_start: datetime, day_end: datetime
) -> DailyCohortScan:
    if not table_ids:
        return DailyCohortScan(
            day_start=day_start,
            day_end=day_end,
            cohort_size=0,
            trino_query_count=0,
            trino_bytes_scanned=0,
            estimated_scan_cost_usd=None,
        )

    row = session.execute(
        select(func.count(QueryLog.id), func.coalesce(func.sum(QueryLog.bytes_read), 0)).where(
            QueryLog.engine == _TRINO_ENGINE,
            QueryLog.table_id.in_(table_ids),
            QueryLog.executed_at >= day_start,
            QueryLog.executed_at < day_end,
        )
    ).one()
    query_count, bytes_scanned = row

    price = _price_for_day(session, day_start.date())
    cost = round((int(bytes_scanned) / _GIB) * price, 4) if price is not None else None

    return DailyCohortScan(
        day_start=day_start,
        day_end=day_end,
        cohort_size=len(table_ids),
        trino_query_count=int(query_count or 0),
        trino_bytes_scanned=int(bytes_scanned or 0),
        estimated_scan_cost_usd=cost,
    )


def _pct_change(current: float, baseline: float) -> float | None:
    if not baseline:
        return None
    return (current - baseline) / baseline * 100


def compute_trino_hot_cost_signal(
    session: Session,
    *,
    preset_name: str,
    dod_move_pct: float = 15.0,
    wow_move_pct: float = 20.0,
    now: datetime | None = None,
) -> TrinoHotCostSignal | None:
    """Yesterday's Trino bytes-scanned for the current top-N cohort, vs DoD/WoW baselines.

    Returns None (not a signal with direction="unknown") when the preset doesn't exist at all
    or currently has an empty cohort -- there is nothing to trend yet, which is a different
    state from "we have a cohort but no scan evidence for it" (that case DOES return a signal,
    with trend="unknown" via cohort_size==0 same-day check below).

    Ranking direction is the OPPOSITE of impact_signals.py's: there, more bytes freed is
    "improving"; here, FEWER bytes scanned is "improving" -- less Trino scan volume for the
    same hot-table cohort is the entire point of running this preset. Same polarity as
    cost_signals.py's spike/decline, not impact_signals.py's improving/worsening.
    """
    current = now or datetime.now(UTC)
    preset = session.scalar(select(Preset).where(Preset.name == preset_name))
    if preset is None:
        return None

    table_ids = _cohort_table_ids(session, preset.id)
    if not table_ids:
        return None

    today_start, today_end = _day_bounds(current, 1)
    dod_start, dod_end = _day_bounds(current, 2)
    wow_start, wow_end = _day_bounds(current, 8)

    today = _daily_cohort_scan(session, table_ids, today_start, today_end)
    dod_baseline = _daily_cohort_scan(session, table_ids, dod_start, dod_end)
    wow_baseline = _daily_cohort_scan(session, table_ids, wow_start, wow_end)

    if today.trino_query_count == 0:
        # No Trino traffic at all yesterday for this cohort -- could be a real quiet day, could
        # be query-log ingestion stalled. Either way, 0 bytes here is not a trustworthy
        # "scan cost is zero" claim the way impact_signals.py's no_actions state is a genuine
        # zero -- there's no equivalent "this table had zero eligible actions" guarantee for
        # query traffic, so this stays "unknown" rather than reading as a win.
        return TrinoHotCostSignal(
            preset_name=preset_name,
            trend="unknown",
            dod_bytes_pct=None,
            wow_bytes_pct=None,
            today=today,
            dod_baseline=None,
            wow_baseline=None,
        )

    dod_pct = _pct_change(today.trino_bytes_scanned, dod_baseline.trino_bytes_scanned)
    wow_pct = _pct_change(today.trino_bytes_scanned, wow_baseline.trino_bytes_scanned)

    trend: CohortTrend = "flat"
    if dod_pct is not None and abs(dod_pct) >= dod_move_pct:
        trend = "improving" if dod_pct < 0 else "worsening"
    elif wow_pct is not None and abs(wow_pct) >= wow_move_pct:
        trend = "improving" if wow_pct < 0 else "worsening"

    return TrinoHotCostSignal(
        preset_name=preset_name,
        trend=trend,
        dod_bytes_pct=dod_pct,
        wow_bytes_pct=wow_pct,
        today=today,
        dod_baseline=dod_baseline,
        wow_baseline=wow_baseline,
    )
