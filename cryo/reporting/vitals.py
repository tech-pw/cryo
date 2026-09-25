"""Cryo vitals: one number for "is everything in Cryo working as expected right now."

Companion to impact_signals.py's north-star metric, answering a different question. The
north star asks "how much good are we doing" (GB freed/day) -- a real day can score well on
that and still be quietly sick (see health_check.py's docstring: three independent incidents
that never showed up as a job failure or a dip in bytes freed, only as something stuck,
something silently gating to zero, or something drifting the wrong direction). Vitals asks
"is the machine itself healthy," the way a pulse/BP/SpO2 reading is a different question from
"how much work did you get done today."

Built 2026-08-31, immediately after confirming a report earlier flagged as "worth a look
later" (a Spark job apparently stuck 2+ hours on the fleet's largest table) was in fact
healthy -- genuine progress through Iceberg's own file-group commit sequence, not a hang.
That investigation used exactly this kind of live, multi-source evidence gathering by hand;
this module exists so the same class of question ("is Cryo actually fine right now") has a
standing, on-demand answer instead of requiring a fresh investigation each time.

Does not duplicate health_check.py's checks -- reuses run_production_health_checks() as the
structural-integrity input and composes it with two pulses that checklist doesn't carry as a
headline number: whether dispatch has produced a run recently (the scheduler's own heartbeat,
distinct from "is a queue draining") and the fleet's job success rate over a real window (the
same ground truth impact_signals.py sums bytes from, viewed as a rate instead of a total).

2026-08-31, same day: extended per explicit feedback that vitals should cover "governer, cost
ingestion, scanning etc" by name, not just job-execution-shaped incidents. health_check.py now
also carries scan_freshness/scan_failures (is the scanner itself still running, and can it
actually read what it's pointed at) and cost_ingestion_freshness (is table_cost still being
refreshed -- it feeds both the north-star S3-cost number and the cost-ceiling admission gate,
and goes stale silently, with no error, looking exactly like "nothing changed"). Governor
health specifically is covered by the existing dispatch_heartbeat pulse below plus
admission_stall in the checklist -- the governor has no DB row of its own recording "a wave
ran"; its only observable footprint is the jobs it admits, which both of those already watch.

Score design: starts at 100, each health_check.py failure deducts by severity (critical costs
more than warning), the dispatch-heartbeat and success-rate pulses each contribute their own
deduction on their own thresholds. This is deliberately a smooth score, not a re-skinned
pass/fail -- a fleet with one warning-level check and a 97% success rate should read as
"mostly fine, one thing to watch," not collapse to the same CRITICAL bucket as a fleet with
zero admissions in six hours. The status band (HEALTHY/DEGRADED/CRITICAL) is still driven
primarily by health_check.py's own critical/warning counts, since those checks encode real
incident-shaped signals this module has no business re-judging independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cryo.config import get_settings
from cryo.db.models import MaintenanceJob
from cryo.reporting.health_check import ProductionHealthReport, run_production_health_checks
from cryo.reporting.impact_signals import (
    ImpactSignal,
    compute_impact_signal,
    measured_reclaim_gb,
)
from cryo.reporting.trino_hot_cost_signal import TrinoHotCostSignal, compute_trino_hot_cost_signal

VitalsStatus = Literal["HEALTHY", "DEGRADED", "CRITICAL"]

# Deduction weights -- tuned so a single warning barely moves the score (an operator shouldn't
# need to react to every point drop) but a critical failure or a genuinely stalled dispatch
# heartbeat drags the score into DEGRADED/CRITICAL territory on its own, matching how
# health_check.py already treats those two severities as qualitatively different.
_CRITICAL_DEDUCTION = 20.0
_WARNING_DEDUCTION = 6.0

# Dispatch heartbeat: the scheduler's tick() should produce *something* -- a job admitted,
# started, or finished -- far more often than this window on any fleet with a non-empty
# backlog. This is deliberately generous (90min) vs. _STUCK_JOB_HOURS's 6h in health_check.py:
# that check catches jobs stuck *mid-flight*; this one catches dispatch itself going quiet,
# the exact shape of the multi-day stall fixed earlier this same day (2026-08-31) in
# scheduler/service.py and scheduler/cron.py.
_DISPATCH_HEARTBEAT_MINUTES = 90
_DISPATCH_HEARTBEAT_DEDUCTION = 25.0

# Success-rate pulse over a real rolling window -- long enough that one bad hour of transient
# metastore blips (see the 2026-08-31 native/watcher.py retry fix) doesn't swing it, short
# enough that a fresh regression shows up same-day.
_SUCCESS_RATE_WINDOW_HOURS = 24
_SUCCESS_RATE_MIN_TERMINAL_JOBS = 20  # below this, the rate is too noisy to score -- a fleet
# that finished 3 jobs today isn't "83% healthy" or "100% healthy," it's "not enough signal yet."
_SUCCESS_RATE_WARN_PCT = 90.0
_SUCCESS_RATE_CRITICAL_PCT = 75.0
_SUCCESS_RATE_WARN_DEDUCTION = 8.0
_SUCCESS_RATE_CRITICAL_DEDUCTION = 18.0


@dataclass(frozen=True)
class DispatchHeartbeat:
    last_activity_at: datetime | None
    minutes_since: float | None
    is_stalled: bool

    @property
    def summary(self) -> str:
        if self.last_activity_at is None:
            return "No job admitted/started/finished yet -- no dispatch history at all"
        if self.is_stalled:
            return (
                f"No job admitted/started/finished in {self.minutes_since:.0f}min "
                f"(threshold {_DISPATCH_HEARTBEAT_MINUTES}min)"
            )
        return f"Dispatch active -- last job activity {self.minutes_since:.0f}min ago"


@dataclass(frozen=True)
class SuccessRatePulse:
    window_hours: int
    succeeded: int
    failed: int
    total_terminal: int
    has_enough_signal: bool

    @property
    def success_rate_pct(self) -> float | None:
        if not self.has_enough_signal:
            return None
        return 100.0 * self.succeeded / self.total_terminal

    @property
    def summary(self) -> str:
        if not self.has_enough_signal:
            return (
                f"Only {self.total_terminal} job(s) finished in {self.window_hours}h -- "
                f"not enough signal to score (need {_SUCCESS_RATE_MIN_TERMINAL_JOBS}+)"
            )
        rate = self.success_rate_pct or 0.0
        return (
            f"{rate:.1f}% success rate over {self.window_hours}h "
            f"({self.succeeded} succeeded / {self.failed} failed)"
        )


@dataclass(frozen=True)
class VitalsDeduction:
    reason: str
    points: float


@dataclass(frozen=True)
class CryoVitals:
    generated_at: datetime
    score: float
    status: VitalsStatus
    health: ProductionHealthReport
    impact: ImpactSignal
    dispatch_heartbeat: DispatchHeartbeat
    success_rate: SuccessRatePulse
    deductions: list[VitalsDeduction] = field(default_factory=list)
    # None when the daily Trino-hot-tables preset (cryo.scheduler.trino_hot_preset) isn't
    # seeded in this deployment, or its cohort is currently empty -- a genuinely different
    # state from a signal that IS computed but reads "unknown" (no Trino traffic yesterday).
    # Never contributes to `score`/`status`: it answers "is a specific cost-reduction lever
    # working," not "is Cryo healthy" -- conflating the two would make disabling an optional
    # preset look like a health regression.
    trino_hot_cost: TrinoHotCostSignal | None = None

    @property
    def headline(self) -> str:
        return f"{self.status} — {self.score:.0f}/100"


def _dispatch_heartbeat(session: Session, *, now: datetime) -> DispatchHeartbeat:
    """Most recent timestamp across started_at/finished_at -- either means dispatch (or a
    watcher finalizing dispatch's output) did something recently. A fleet that's fully caught
    up (empty queue, nothing to dispatch) will still show finished_at activity from the last
    real job, so this doesn't false-positive on "queue empty" the way a naive "nothing queued"
    check would."""
    last_started = session.execute(select(func.max(MaintenanceJob.started_at))).scalar_one()
    last_finished = session.execute(select(func.max(MaintenanceJob.finished_at))).scalar_one()
    candidates = [t for t in (last_started, last_finished) if t is not None]
    if not candidates:
        return DispatchHeartbeat(last_activity_at=None, minutes_since=None, is_stalled=True)

    last_activity = max(candidates)
    last_activity_aware = last_activity if last_activity.tzinfo is not None else last_activity.replace(
        tzinfo=UTC
    )
    minutes_since = (now - last_activity_aware).total_seconds() / 60.0
    return DispatchHeartbeat(
        last_activity_at=last_activity_aware,
        minutes_since=minutes_since,
        is_stalled=minutes_since > _DISPATCH_HEARTBEAT_MINUTES,
    )


def _success_rate_pulse(session: Session, *, now: datetime) -> SuccessRatePulse:
    window_start = now - timedelta(hours=_SUCCESS_RATE_WINDOW_HOURS)
    rows = session.execute(
        select(MaintenanceJob.status, func.count())
        .where(
            MaintenanceJob.finished_at >= window_start,
            MaintenanceJob.status.in_(["succeeded", "failed"]),
        )
        .group_by(MaintenanceJob.status)
    ).all()
    counts = {status: int(n) for status, n in rows}
    succeeded = counts.get("succeeded", 0)
    failed = counts.get("failed", 0)
    total = succeeded + failed
    return SuccessRatePulse(
        window_hours=_SUCCESS_RATE_WINDOW_HOURS,
        succeeded=succeeded,
        failed=failed,
        total_terminal=total,
        has_enough_signal=total >= _SUCCESS_RATE_MIN_TERMINAL_JOBS,
    )


def compute_cryo_vitals(session: Session, *, now: datetime | None = None) -> CryoVitals:
    now = now or datetime.now(UTC)

    health = run_production_health_checks(session, now=now)
    impact = compute_impact_signal(session, now=now)
    heartbeat = _dispatch_heartbeat(session, now=now)
    success_rate = _success_rate_pulse(session, now=now)
    trino_hot_cost = compute_trino_hot_cost_signal(
        session, preset_name=get_settings().trino_hot_preset_name, now=now
    )

    score = 100.0
    deductions: list[VitalsDeduction] = []

    for check in health.checks:
        if check.passed:
            continue
        points = _CRITICAL_DEDUCTION if check.severity == "critical" else _WARNING_DEDUCTION
        score -= points
        deductions.append(VitalsDeduction(reason=f"{check.name}: {check.summary}", points=points))

    if heartbeat.is_stalled:
        score -= _DISPATCH_HEARTBEAT_DEDUCTION
        deductions.append(
            VitalsDeduction(reason=f"dispatch_heartbeat: {heartbeat.summary}", points=_DISPATCH_HEARTBEAT_DEDUCTION)
        )

    if success_rate.has_enough_signal:
        rate = success_rate.success_rate_pct or 0.0
        if rate < _SUCCESS_RATE_CRITICAL_PCT:
            score -= _SUCCESS_RATE_CRITICAL_DEDUCTION
            deductions.append(
                VitalsDeduction(
                    reason=f"success_rate: {success_rate.summary}",
                    points=_SUCCESS_RATE_CRITICAL_DEDUCTION,
                )
            )
        elif rate < _SUCCESS_RATE_WARN_PCT:
            score -= _SUCCESS_RATE_WARN_DEDUCTION
            deductions.append(
                VitalsDeduction(
                    reason=f"success_rate: {success_rate.summary}",
                    points=_SUCCESS_RATE_WARN_DEDUCTION,
                )
            )

    score = max(0.0, min(100.0, score))

    # Status band driven primarily by health_check.py's own critical/warning verdicts (those
    # checks encode real incident shapes this module shouldn't re-judge) plus the two pulses
    # this module owns, so a stalled heartbeat or a critical success-rate drop can independently
    # push status down even on a day health_check.py's checklist otherwise passes clean.
    status: VitalsStatus
    if (
        health.critical_count > 0
        or heartbeat.is_stalled
        or (success_rate.has_enough_signal and (success_rate.success_rate_pct or 0.0) < _SUCCESS_RATE_CRITICAL_PCT)
    ):
        status = "CRITICAL"
    elif health.warning_count > 0 or (
        success_rate.has_enough_signal and (success_rate.success_rate_pct or 0.0) < _SUCCESS_RATE_WARN_PCT
    ):
        status = "DEGRADED"
    else:
        status = "HEALTHY"

    return CryoVitals(
        generated_at=now,
        score=score,
        status=status,
        health=health,
        impact=impact,
        dispatch_heartbeat=heartbeat,
        success_rate=success_rate,
        deductions=deductions,
        trino_hot_cost=trino_hot_cost,
    )


def cryo_vitals_to_payload(vitals: CryoVitals) -> dict[str, Any]:
    return {
        "generated_at": vitals.generated_at.isoformat(),
        "score": round(vitals.score, 1),
        "status": vitals.status,
        "headline": vitals.headline,
        "deductions": [{"reason": d.reason, "points": d.points} for d in vitals.deductions],
        "health": {
            "overall_status": vitals.health.overall_status,
            "critical_count": vitals.health.critical_count,
            "warning_count": vitals.health.warning_count,
        },
        "impact": {
            "direction": vitals.impact.direction,
            # Review finding F10, 2026-09-20: gb_freed alone (without this field) let a
            # consumer read "0.2 GB freed" as the real day total even when it was actually a
            # partial subtotal (1 measured action out of 1000 successful ones) or missing
            # entirely -- measurement_state makes that coverage explicit instead of silently
            # implied. See cryo/reporting/impact_signals.py::MeasurementState's docstring.
            "measurement_state": vitals.impact.measurement_state,
            "gb_freed_yesterday": measured_reclaim_gb(vitals.impact),
            "dod_pct": vitals.impact.dod_pct if vitals.impact.today.is_comparable_total else None,
            "wow_pct": vitals.impact.wow_pct if vitals.impact.today.is_comparable_total else None,
        },
        "trino_hot_cost": (
            {
                "preset_name": vitals.trino_hot_cost.preset_name,
                "trend": vitals.trino_hot_cost.trend,
                "cohort_size": vitals.trino_hot_cost.today.cohort_size,
                "gib_scanned_yesterday": round(vitals.trino_hot_cost.today.gib_scanned, 2),
                "estimated_scan_cost_usd_yesterday": vitals.trino_hot_cost.today.estimated_scan_cost_usd,
                "dod_bytes_pct": vitals.trino_hot_cost.dod_bytes_pct,
                "wow_bytes_pct": vitals.trino_hot_cost.wow_bytes_pct,
            }
            if vitals.trino_hot_cost is not None
            else None
        ),
        "dispatch_heartbeat": {
            "last_activity_at": vitals.dispatch_heartbeat.last_activity_at.isoformat()
            if vitals.dispatch_heartbeat.last_activity_at
            else None,
            "minutes_since": vitals.dispatch_heartbeat.minutes_since,
            "is_stalled": vitals.dispatch_heartbeat.is_stalled,
            "summary": vitals.dispatch_heartbeat.summary,
        },
        "success_rate": {
            "window_hours": vitals.success_rate.window_hours,
            "succeeded": vitals.success_rate.succeeded,
            "failed": vitals.success_rate.failed,
            "success_rate_pct": vitals.success_rate.success_rate_pct,
            "has_enough_signal": vitals.success_rate.has_enough_signal,
            "summary": vitals.success_rate.summary,
        },
    }
