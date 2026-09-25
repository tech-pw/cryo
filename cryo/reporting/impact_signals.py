"""North-star maintenance-impact metric: is Cryo actually shrinking the fleet, day to day?

Built 2026-08-19 after a real gap was found live: "N jobs succeeded" was the only maintenance
signal in the daily report, and `job.status = succeeded` only means the operation ran without
erroring -- it says nothing about whether it moved a real number. Spot-checking 181 tables
with before/after scans found file count *up* on 134 of them and snapshot count up on 121 --
not because maintenance failed, but because those tables kept receiving writes between scans
faster than a single run could offset. That's a cohort-selection question, not a "did this
work" question, and conflating the two hid the real answer.

The real answer already exists, per-job, in ``AuditLog.actual_bytes_freed`` /
``input_files_count`` / ``output_files_count`` -- written by every successful action, not
reconstructed from before/after scans that may be days apart or missing entirely (most tables
aren't re-scanned soon enough after their job for a scan-diff to mean anything). This module
sums that ground-truth field the same way ``cost_signals.py`` already proves out for cost:
same-day-range DoD/WoW comparisons only, never a partial period against a full one (that
distinction produced a genuinely wrong answer once already, not just an imprecise one -- see
that module's docstring).

North-star metric: **net bytes reclaimed per day** (remove_orphan_files + optimize +
convert_deletes' actual_bytes_freed, successful actions only) and, alongside it, **optimize's
file-count reduction ratio** (input_files_count -> output_files_count) since bytes freed alone
doesn't distinguish "we shrank fragmented tables" from "we deleted orphan garbage" -- both are
real wins, but they're different kinds of win worth seeing separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import AuditLog

ImpactDirection = Literal["improving", "worsening", "flat", "unknown"]
ImpactSignalSource = Literal["dod", "wow"]
# Review finding F10, 2026-09-20: bytes_freed_is_known's original boolean ("any evidence at
# all" vs "zero evidence") meant one measured action out of a thousand unmeasured ones counted
# as fully "known" -- gb_freed would then report only that one action's bytes as if it were the
# day's real total, silently undercounting by orders of magnitude while claiming to be a real
# measurement. MeasurementState makes the actual coverage explicit instead of collapsing it to
# a single bit: "complete" (every succeeded action reported bytes), "partial" (some did, some
# didn't -- gb_freed is a measured SUBTOTAL, not the true total), "unmeasured" (actions
# succeeded but none reported bytes), "no_actions" (nothing succeeded -- a real, legitimate
# zero, not a gap).
MeasurementState = Literal["complete", "partial", "unmeasured", "no_actions"]

# Bytes-freed actions: the whole point of the job is reclaiming space. Excludes
# expire_snapshots (frees storage indirectly, once remove_orphan_files later collects the
# now-unreferenced files -- double-counting it here would credit the same bytes twice) and
# optimize_manifests/apply_table_properties/compute_statistics (no bytes-freed semantics).
_RECLAIM_ACTIONS = ("remove_orphan_files", "optimize", "convert_deletes", "rewrite_position_deletes")
_SUCCESS_STATUSES = ("OK", "succeeded")


@dataclass(frozen=True)
class DailyImpact:
    day_start: datetime
    day_end: datetime
    bytes_freed: int
    actions_succeeded: int
    tables_touched: int
    optimize_files_in: int
    optimize_files_out: int
    # Recovery plan §6, 2026-09-20: live-confirmed 2026-09-20 that "0 GB freed" for three
    # straight days was NOT a real reclaim regression -- every successful remove_orphan_files/
    # optimize action that day had actual_bytes_freed IS NULL (0 of 2013 rows non-null on
    # 2026-09-19), not a genuine 0. Root cause: only the native engine
    # (cryo.engines.native.engine) ever populates ExecResult.actual_bytes_freed; the Trino SQL
    # engine (cryo.engines.trino.engine.TrinoEngine.execute) never sets it at all, and the
    # native engine has been disabled fleet-wide (CRYO_NATIVE_ENGINE_ENABLED=false) throughout
    # this window -- so bytes_freed's coalesce-to-0 was silently reporting "no measurement
    # exists" as "measured zero," which read as a maintenance-impact collapse when the fleet
    # was actually running normally, just on an engine that doesn't emit this evidence yet.
    # These two counts let the caller (compute_impact_signal, and any dashboard/report reading
    # DailyImpact) tell the two apart instead of trusting bytes_freed blindly.
    actions_with_bytes_evidence: int
    actions_without_bytes_evidence: int

    @property
    def gb_freed(self) -> float:
        return self.bytes_freed / (1024**3)

    @property
    def measurement_state(self) -> MeasurementState:
        """The real coverage of bytes_freed, not just a "some evidence exists" bit -- see
        MeasurementState's own docstring for why the boolean this replaced was insufficient
        (one measured action out of a thousand unmeasured ones used to read as fully "known").
        """
        if self.actions_succeeded == 0:
            return "no_actions"
        if self.actions_with_bytes_evidence == 0:
            return "unmeasured"
        if self.actions_without_bytes_evidence == 0:
            return "complete"
        return "partial"

    @property
    def bytes_freed_is_known(self) -> bool:
        """Kept for callers that only need a coarse known-vs-not check (e.g. whether gb_freed
        is safe to use in a percentage comparison at all) -- prefer measurement_state for
        anything that needs to distinguish "complete" from "partial" coverage. True for
        "complete", "partial", and "no_actions" (all three have a real, if not necessarily
        total, number behind bytes_freed); False only for "unmeasured".
        """
        return self.measurement_state != "unmeasured"

    @property
    def is_comparable_total(self) -> bool:
        """True only for "complete" and "no_actions" -- the two states where gb_freed is the
        REAL total, not a subtotal of unknown completeness. Review finding F10 (follow-up
        review, 2026-09-20): the original bytes_freed_is_known gate let a "partial" day (e.g.
        1 GB measured out of 100 real actions, 99 unmeasured) compute a percentage against a
        fully "complete" baseline -- a 90% "drop" that was actually just a drop in *measurement
        coverage*, not real reclaim volume, still reads as a confident worsening/improving
        signal. Percentage comparisons must gate on this property, not bytes_freed_is_known,
        wherever the comparison's honesty depends on both sides being a true total rather than
        an unknown-sized fragment of one.
        """
        return self.measurement_state in ("complete", "no_actions")

    @property
    def optimize_file_reduction_pct(self) -> float | None:
        if self.optimize_files_in <= 0:
            return None
        return (self.optimize_files_in - self.optimize_files_out) / self.optimize_files_in * 100


@dataclass(frozen=True)
class ImpactSignal:
    direction: ImpactDirection
    dod_pct: float | None
    wow_pct: float | None
    primary_signal: ImpactSignalSource | None
    today: DailyImpact
    dod_baseline_gb: float | None
    wow_baseline_gb: float | None
    # Review finding F10: surfaces today's actual measurement coverage on the signal itself, not
    # just buried in `today.measurement_state` -- a consumer rendering only `direction` and
    # `dod_pct`/`wow_pct` (e.g. a Slack summary line) can still tell a reader "partial" coverage
    # apart from "complete" without reaching into the nested DailyImpact.
    measurement_state: MeasurementState


def measured_reclaim_gb(signal: ImpactSignal) -> float | None:
    """None when no action reported bytes. A missing total is not zero."""
    if signal.measurement_state == "unmeasured":
        return None
    return signal.today.gb_freed


def reclaim_display(signal: ImpactSignal) -> str:
    state = signal.measurement_state
    if state == "unmeasured":
        return "unmeasured"
    if state == "no_actions":
        return "no reclaim actions"
    label = f"{signal.today.gb_freed:.1f} GB"
    if state == "partial":
        return f"{label} measured (partial)"
    return label


def _daily_impact(session: Session, day_start: datetime, day_end: datetime) -> DailyImpact:
    """Sum AuditLog's real per-action outcomes for one UTC day, [day_start, day_end)."""
    row = session.execute(
        select(
            func.coalesce(func.sum(AuditLog.actual_bytes_freed), 0),
            func.count(AuditLog.id),
            func.count(func.distinct(AuditLog.table_id)),
            # See DailyImpact.bytes_freed_is_known's docstring: count(actual_bytes_freed) skips
            # NULLs (standard SQL COUNT-of-a-column semantics), so this is "how many succeeded
            # reclaim actions actually reported a byte figure" -- distinct from actions_succeeded,
            # which counts every succeeded action regardless of whether it reported bytes.
            func.count(AuditLog.actual_bytes_freed),
        ).where(
            AuditLog.ts >= day_start,
            AuditLog.ts < day_end,
            AuditLog.action.in_(_RECLAIM_ACTIONS),
            AuditLog.status.in_(_SUCCESS_STATUSES),
        )
    ).one()
    bytes_freed, actions_succeeded, tables_touched, actions_with_bytes_evidence = row
    actions_with_bytes_evidence = int(actions_with_bytes_evidence or 0)
    actions_without_bytes_evidence = int(actions_succeeded or 0) - actions_with_bytes_evidence

    opt_row = session.execute(
        select(
            func.coalesce(func.sum(AuditLog.input_files_count), 0),
            func.coalesce(func.sum(AuditLog.output_files_count), 0),
        ).where(
            AuditLog.ts >= day_start,
            AuditLog.ts < day_end,
            AuditLog.action == "optimize",
            AuditLog.status.in_(_SUCCESS_STATUSES),
            AuditLog.input_files_count.isnot(None),
            AuditLog.output_files_count.isnot(None),
        )
    ).one()
    files_in, files_out = opt_row

    return DailyImpact(
        day_start=day_start,
        day_end=day_end,
        bytes_freed=int(bytes_freed or 0),
        actions_succeeded=int(actions_succeeded or 0),
        tables_touched=int(tables_touched or 0),
        optimize_files_in=int(files_in or 0),
        optimize_files_out=int(files_out or 0),
        actions_with_bytes_evidence=actions_with_bytes_evidence,
        actions_without_bytes_evidence=actions_without_bytes_evidence,
    )


def day_bounds(now: datetime, days_ago: int) -> tuple[datetime, datetime]:
    """UTC calendar-day bounds for `days_ago` days before `now`'s date -- a full day, never a
    partial one, matching cost_signals.py's same-day-range rule."""
    anchor = (now - timedelta(days=days_ago)).date()
    start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _pct_change(current: float, baseline: float | None) -> float | None:
    if not baseline:
        return None
    return (current - baseline) / baseline * 100


def compute_impact_signal(session: Session, *, now: datetime | None = None) -> ImpactSignal:
    """Compute yesterday's real reclaim impact and its DoD/WoW trend.

    Uses yesterday (not today) as "current" for the same reason compute_cost_signal does --
    a report generated mid-day would otherwise compare a partial day against full baselines,
    which looks like a real change but is really just report-timing noise.
    """
    now = now or datetime.now(UTC)
    settings = get_settings()

    today_start, today_end = day_bounds(now, 1)
    dod_start, dod_end = day_bounds(now, 2)
    wow_start, wow_end = day_bounds(now, 8)

    today = _daily_impact(session, today_start, today_end)
    dod_baseline = _daily_impact(session, dod_start, dod_end)
    wow_baseline = _daily_impact(session, wow_start, wow_end)

    if not today.is_comparable_total:
        # Review finding F10 (follow-up review, 2026-09-20): gate on is_comparable_total, not
        # just "unmeasured" -- a "partial" day (some actions measured, some not) is just as
        # untrustworthy as a percentage numerator as a fully unmeasured one, since gb_freed is
        # an unknown-sized fragment of the real total either way. "unknown" direction still
        # correctly distinguishes this from a genuine no_actions zero (handled below, since
        # no_actions IS a comparable total -- see is_comparable_total's own docstring).
        return ImpactSignal(
            direction="unknown",
            dod_pct=None,
            wow_pct=None,
            primary_signal=None,
            today=today,
            dod_baseline_gb=dod_baseline.gb_freed if dod_baseline.is_comparable_total else None,
            wow_baseline_gb=wow_baseline.gb_freed if wow_baseline.is_comparable_total else None,
            measurement_state=today.measurement_state,
        )

    # Both baselines must ALSO be comparable totals -- a "partial" or "unmeasured" baseline
    # would make even a genuinely complete `today` produce a manufactured percentage (e.g.
    # comparing today's real total against a baseline day that only measured a fragment of
    # its own actions). _pct_change already treats a None baseline as "no comparison" rather
    # than dividing by an untrustworthy number.
    dod_pct = _pct_change(
        today.gb_freed, dod_baseline.gb_freed if dod_baseline.is_comparable_total else None
    )
    wow_pct = _pct_change(
        today.gb_freed, wow_baseline.gb_freed if wow_baseline.is_comparable_total else None
    )

    # "Improving" means reclaiming MORE than baseline -- the opposite polarity from
    # cost_signals.py's spike/decline (there, less cost is good; here, more bytes freed is
    # good). A day with genuinely zero reclaim activity and a non-zero baseline is a real
    # regression worth flagging, not treated as "stable."
    direction: ImpactDirection = "flat"
    primary: ImpactSignalSource | None = None
    if dod_pct is not None and abs(dod_pct) >= settings.report_impact_dod_move_pct:
        direction = "improving" if dod_pct > 0 else "worsening"
        primary = "dod"
    elif wow_pct is not None and abs(wow_pct) >= settings.report_impact_wow_move_pct:
        direction = "improving" if wow_pct > 0 else "worsening"
        primary = "wow"

    return ImpactSignal(
        direction=direction,
        dod_pct=dod_pct,
        wow_pct=wow_pct,
        primary_signal=primary,
        today=today,
        # Same is_comparable_total gate as the pct computation above -- a baseline shown here
        # must be the real total it claims to be, not a partial/unmeasured subtotal presented
        # without the caveat (this is the "null the number, don't just null the trend" half of
        # F10: a consumer reading only *_baseline_gb, not measurement_state, must still never
        # see a number it can't trust).
        dod_baseline_gb=dod_baseline.gb_freed if dod_baseline.is_comparable_total else None,
        wow_baseline_gb=wow_baseline.gb_freed if wow_baseline.is_comparable_total else None,
        measurement_state=today.measurement_state,
    )
