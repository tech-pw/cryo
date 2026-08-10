"""Day-over-day / week-over-week fleet cost signal detection + maintenance correlation.

Compares Cryo's own aggregate fleet cost (``TableCostHistory.current_monthly_cost_usd``,
summed daily) using same-day-range comparisons only -- never a partial period against a
full one. Confirmed live in production that the loose version silently produces a wrong
answer, not just a less precise one: a 5-day partial-month average compared against a
full 31-day prior-month average said cost was down ~21%; the correct same-day-range
comparison said up ~6%, not the same conclusion at all. Day-over-day and week-over-week
here are each single-day-vs-single-day comparisons for exactly that reason.

When a spike or decline crosses its threshold, correlates against Cryo's own recent
maintenance activity (``AuditLog``) so the report can say *why*, deterministically -- no
LLM guessing at causation, same "numbers come from code, not the model" rule the rest of
``daily_report.py`` follows. The correlation distinguishes rewrite-type actions (which can
temporarily *increase* storage until their own cleanup window elapses) from cleanup-type
actions (which reduce it) -- a spike explained by "we just compacted a lot of tables and
the old files haven't aged out of retention yet" is a very different finding than an
unexplained spike, and conflating the two into one "maintenance ran" bucket would blur
that distinction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import AuditLog, TableCostHistory

CostDirection = Literal["spike", "decline", "stable"]
CostSignalSource = Literal["dod", "wow"]

# Actions that write new data and can transiently inflate storage before their own
# expire/orphan cleanup (run in the same job, but only reaches files already past
# their retention window) catches up to what THIS run just made obsolete.
_REWRITE_ACTIONS = ("optimize", "optimize_manifests")
# Actions whose entire job is reclaiming space -- the natural explanation for a decline.
_CLEANUP_ACTIONS = ("expire_snapshots", "remove_orphan_files")
_SUCCESS_STATUSES = ("OK", "succeeded")


@dataclass(frozen=True)
class CostSignal:
    direction: CostDirection
    dod_pct: float | None
    wow_pct: float | None
    primary_signal: CostSignalSource | None
    current_cost_usd: float | None
    baseline_dod_cost_usd: float | None
    baseline_wow_cost_usd: float | None


@dataclass(frozen=True)
class MaintenanceCorrelation:
    tables_involved: int
    actions_succeeded: int
    bytes_freed: int
    explains_change: bool
    summary: str


def _daily_fleet_cost(session: Session, as_of: datetime) -> float | None:
    """Sum of each table's most recent cost snapshot at/before ``as_of``.

    The fleet isn't scanned atomically, so "cost as of day D" means each table's latest
    snapshot on or before D, not a single fleet-wide row -- mirrors the same
    nearest-prior-scan-per-table logic ``daily_report.py`` already uses for the size delta.
    Returns None (never a fabricated 0) when there's no data yet for that day.
    """
    subq = (
        select(
            TableCostHistory.table_id,
            func.max(TableCostHistory.scan_at).label("scan_at"),
        )
        .where(TableCostHistory.scan_at <= as_of)
        .group_by(TableCostHistory.table_id)
        .subquery()
    )
    rows = session.execute(
        select(TableCostHistory.current_monthly_cost_usd).join(
            subq,
            (TableCostHistory.table_id == subq.c.table_id)
            & (TableCostHistory.scan_at == subq.c.scan_at),
        )
    ).fetchall()
    if not rows:
        return None
    return sum(float(r[0] or 0) for r in rows)


def _pct_change(current: float | None, baseline: float | None) -> float | None:
    if current is None or not baseline:
        return None
    return (current - baseline) / baseline * 100


def compute_cost_signal(session: Session, *, now: datetime | None = None) -> CostSignal:
    """Compute the DoD and WoW fleet-cost change and classify it against configured thresholds.

    Uses yesterday as the "current" point (not today) so a report generated mid-day never
    compares a partially-scanned day against a fully-scanned baseline -- an asymmetry that
    would look like a real spike/decline but is really just an artifact of when the report ran.
    """
    now = now or datetime.now(UTC)
    settings = get_settings()

    current_day = now - timedelta(days=1)
    dod_baseline_day = now - timedelta(days=2)
    wow_baseline_day = now - timedelta(days=8)

    current_cost = _daily_fleet_cost(session, current_day)
    dod_baseline = _daily_fleet_cost(session, dod_baseline_day)
    wow_baseline = _daily_fleet_cost(session, wow_baseline_day)

    dod_pct = _pct_change(current_cost, dod_baseline)
    wow_pct = _pct_change(current_cost, wow_baseline)

    direction: CostDirection = "stable"
    primary: CostSignalSource | None = None

    if dod_pct is not None and dod_pct >= settings.report_cost_dod_spike_pct:
        direction, primary = "spike", "dod"
    elif dod_pct is not None and dod_pct <= -settings.report_cost_dod_decline_pct:
        direction, primary = "decline", "dod"

    # WoW only overrides a "stable" DoD read, or upgrades a decline that DoD alone missed --
    # DoD stays the primary alarm when both fire, per the same reasoning the day-over-day /
    # week-over-week threshold split exists for at all: WoW catches slower creep that DoD's
    # tighter window can hide, but shouldn't out-shout a same-day-range hit.
    if direction == "stable":
        if wow_pct is not None and wow_pct >= settings.report_cost_wow_spike_pct:
            direction, primary = "spike", "wow"
        elif wow_pct is not None and wow_pct <= -settings.report_cost_wow_decline_pct:
            direction, primary = "decline", "wow"

    return CostSignal(
        direction=direction,
        dod_pct=dod_pct,
        wow_pct=wow_pct,
        primary_signal=primary,
        current_cost_usd=current_cost,
        baseline_dod_cost_usd=dod_baseline,
        baseline_wow_cost_usd=wow_baseline,
    )


def correlate_with_maintenance(
    session: Session, signal: CostSignal, *, now: datetime | None = None
) -> MaintenanceCorrelation | None:
    """When a cost signal fires, check Cryo's own recent maintenance for a deterministic
    explanation -- Cryo has first-party access to its own audit trail, so this needs no
    external system and no LLM guess at causation. Returns None for a "stable" signal --
    there's nothing to explain.
    """
    if signal.direction == "stable":
        return None
    now = now or datetime.now(UTC)
    lookback = timedelta(days=1) if signal.primary_signal == "dod" else timedelta(days=7)
    since = now - lookback

    # A spike is explained by rewrite activity (which can transiently grow storage ahead of
    # its own cleanup window); a decline is explained by cleanup activity (which shrinks it).
    relevant_actions = _REWRITE_ACTIONS if signal.direction == "spike" else _CLEANUP_ACTIONS

    rows = session.execute(
        select(AuditLog.table_id, AuditLog.actual_bytes_freed)
        .where(
            AuditLog.ts >= since,
            AuditLog.action.in_(relevant_actions),
            AuditLog.status.in_(_SUCCESS_STATUSES),
        )
    ).fetchall()

    tables = {r[0] for r in rows if r[0] is not None}
    bytes_freed = sum(int(r[1] or 0) for r in rows if r[1] is not None)
    succeeded = len(rows)
    days_str = f"{lookback.days} day{'s' if lookback.days != 1 else ''}"
    action_kind = "rewrite" if signal.direction == "spike" else "cleanup"

    if not tables:
        return MaintenanceCorrelation(
            tables_involved=0,
            actions_succeeded=0,
            bytes_freed=0,
            explains_change=False,
            summary=(
                f"No successful {action_kind} activity in the last {days_str} -- this "
                f"{signal.direction} is not explained by Cryo's own maintenance."
            ),
        )

    gb_freed = bytes_freed / (1024**3)
    freed_str = f", {gb_freed:.1f} GB freed" if bytes_freed else ""
    return MaintenanceCorrelation(
        tables_involved=len(tables),
        actions_succeeded=succeeded,
        bytes_freed=bytes_freed,
        explains_change=True,
        summary=(
            f"{len(tables)} table{'s' if len(tables) != 1 else ''} had successful "
            f"{action_kind} activity in the last {days_str} ({succeeded} action"
            f"{'s' if succeeded != 1 else ''}{freed_str}) -- plausibly explains this "
            f"{signal.direction}."
        ),
    )
