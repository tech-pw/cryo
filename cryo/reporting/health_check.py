"""Daily production health checklist -- a separate report from daily_report.py's north-star
metric, answering a different question: not "how much did we reclaim," but "is anything
structurally broken right now that would silently suppress reclaiming, without necessarily
showing up as a job failure."

Built 2026-08-19 after a single investigation session found three real, independent,
long-running production bugs that no existing report or alert had ever surfaced:

1. PDAE-8546's batch-deadline fix (docs of that fix, cryo/engines/native/watcher.py) --
   batched native dispatches blanket-failing every member on a deadline kill.
2. 1,210 distinct active tables repeatedly failing with NoSuchKey / "table does not exist"
   (stale catalog entries pointing at deleted S3 objects) -- ~326-737 failures/day for at
   least a week, ~7-8x the batch-deadline issue's own failure volume.
3. cryo/governor/prefetch.py::prefetch_approved_operation_keys required BOTH
   executed_at IS NULL AND expires_at IS NOT NULL AND expires_at > now() -- live-verified 0
   of 10,460 approved rows in prod ever matched. Silently blocked expire_snapshots/
   remove_orphan_files fleet-wide for two weeks (since the safety-kernel commit that
   introduced the check, 2026-08-05).

None of these produced an alert. All three were found by manual investigation prompted by a
user noticing one specific table's orphan storage wasn't shrinking. This module exists so that
class of problem gets caught by a daily check instead of by accident.

Each check below is a *structural* signal (stuck runs, admission stalls, a safety-gate's own
match rate, status staleness, repeat-failure clustering, file-count/small-file trend) rather
than a check for these three exact bugs -- the next bug will not look identical to any of
these three, but it will very likely show up as one of these same shapes: something stuck,
something silently gating to zero, or something drifting the wrong direction day over day.

All queries are written as portable SQLAlchemy Core (no raw Postgres-only SQL like
``DISTINCT ON`` or ``extract(epoch from ...)``) so this module's tests can run against the
same sqlite-backed ``session`` fixture every other test in this repo uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from cryo.db.models import (
    MaintenanceJob,
    MaintenanceRun,
    Table,
    TableCost,
    TableStats,
    TableStatsHistory,
)
from cryo.workflows.approvals import ApprovalRequest, ApprovalStatus

logger = logging.getLogger(__name__)

# 09:45 IST -- 15 minutes before daily_report.py's north-star report (09:00 IST is the
# shared-S3 slot per anomalies/report.py; check the other reports' own CRON constants before
# assuming a slot is free) and before query_cost_report.py's 10:15 IST slot, so this lands as
# the first thing an operator sees each morning, ahead of the metric report it explains gaps
# in when something here is failing.
PRODUCTION_HEALTH_CHECK_CRON = "45 9 * * *"
PRODUCTION_HEALTH_CHECK_TZ = ZoneInfo("Asia/Kolkata")

# Thresholds -- tuned against the real incidents found 2026-08-19, not arbitrary round numbers.
_STUCK_RUN_HOURS = 12  # the real stuck run found live was 5 days old; even a slow cron run
# finishing in under 12h is the overwhelmingly common case (schedule_cron fires daily), so a
# run open longer than that is either genuinely stuck or needs its own investigation either way.
_STUCK_JOB_HOURS = 6  # matches k8s_ttl_seconds_after_finished's typical 6h window elsewhere
_MIN_APPROVAL_MATCH_RATE_PCT = 50.0  # 0% is exactly what the real bug produced; a healthy
# fleet has some fraction of approvals genuinely expired/consumed-and-not-reused at any moment,
# so 100% is not the right target -- but anything below half signals the matching logic itself
# is broken, not just individual approvals aging out normally.
_REPEAT_FAILURE_MIN_COUNT = 5  # a table hitting the identical error 5+ times in the window is
# clearly not a transient blip
_QUEUE_GROWTH_ALERT_PCT = 25.0  # pending-queue depth growing >25% day-over-day without a
# corresponding admission-rate increase is the queue losing ground, not just being busy
_QUEUE_GROWTH_MIN_BASELINE = 50  # live-verified 2026-08-31: a baseline of 5 turned a real
# ~20k-deep queue into a nonsensical "+412,240%" reading -- percent-growth off a tiny baseline
# is statistical noise, not a genuine trend, so below this the check falls back to judging the
# absolute depth directly instead of a distorted percentage

# 2026-08-31: user asked for vitals to cover "governer, cost ingestion, scanning etc" by name,
# not just the job-execution-shaped incidents the checks above were built for. Scanning and
# cost ingestion write their own per-table freshness/error columns independent of any
# MaintenanceJob (a table can be perfectly scanned and cost-priced with zero jobs ever run
# against it, e.g. brand new or already-optimal) -- so these need their own checks, not a
# derivation of the existing job-shaped ones.
_SCAN_STALE_HOURS = 48  # scanner/README's own daily-cadence cron target is 24h; double that
# before flagging catches a table that's merely due for its next run, not actually broken
_SCAN_STALE_MIN_COUNT = 25  # a handful of stale tables in a multi-thousand-table fleet is
# normal skew from scan ordering; a large chunk stale together means the scanner itself stalled
_SCAN_CONSECUTIVE_FAILURE_THRESHOLD = 3  # matches consecutive_scan_failures' own reset-on-
# success semantics (persistence.py) -- 1-2 is plausibly still a transient blip in progress
_SCAN_FAILING_TABLES_MIN_COUNT = 10  # a handful of genuinely broken tables (deleted, bad
# credentials) is an expected steady-state; a cluster of them appearing together is the
# scanner-itself-broken shape, not individually-broken tables
_COST_STALE_HOURS = 48  # table_cost.scan_at freshness -- same reasoning as scan staleness

# Live-verified 2026-08-31, first real run of scan_freshness/scan_failures/
# cost_ingestion_freshness against prod: 680 of 817 scan-failing tables and 648 of 755
# never-scanned tables were `_evolving` -- the exact orphaned-catalog-entry class from
# ace7f4b (cleanup exists, deliberately not run against these -- see git log and prior session
# instruction to leave `_evolving` cleanup alone). Their metadata.json is permanently gone by
# design of that class of problem, not a live scanner malfunction, so scoring them here just
# duplicates repeat_failures' job-side signal on the same known issue under a new name and
# drowns out genuine scanner problems. Excluded from all three new checks; the *_evolving
# suffix is the same marker the source pipeline and ace7f4b's own cleanup already key on.
_EVOLVING_SUFFIX = "_evolving"


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    severity: str  # "critical" | "warning" | "info"
    summary: str
    detail: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FleetFileTrend:
    """Day-over-day fleet-wide file/small-file/size trend -- the specific numbers requested:
    total files, small files, small-file ratio, and average file size, each compared against
    the prior day's fleet-wide totals so "are we piling up small files" has a direct answer
    rather than requiring an operator to infer it from a per-table view."""

    today_total_files: int
    today_small_files: int
    today_total_size_gb: float
    today_avg_file_size_mb: float | None
    yesterday_total_files: int | None
    yesterday_small_files: int | None
    yesterday_total_size_gb: float | None
    yesterday_avg_file_size_mb: float | None
    tables_counted: int

    @property
    def today_small_file_ratio_pct(self) -> float | None:
        if not self.today_total_files:
            return None
        return 100.0 * self.today_small_files / self.today_total_files

    @property
    def yesterday_small_file_ratio_pct(self) -> float | None:
        if not self.yesterday_total_files:
            return None
        return 100.0 * (self.yesterday_small_files or 0) / self.yesterday_total_files

    @property
    def total_files_delta(self) -> int | None:
        if self.yesterday_total_files is None:
            return None
        return self.today_total_files - self.yesterday_total_files

    @property
    def small_files_delta(self) -> int | None:
        if self.yesterday_small_files is None:
            return None
        return self.today_small_files - self.yesterday_small_files

    @property
    def small_file_ratio_delta_pct(self) -> float | None:
        today = self.today_small_file_ratio_pct
        yesterday = self.yesterday_small_file_ratio_pct
        if today is None or yesterday is None:
            return None
        return today - yesterday

    @property
    def avg_file_size_delta_mb(self) -> float | None:
        if self.today_avg_file_size_mb is None or self.yesterday_avg_file_size_mb is None:
            return None
        return self.today_avg_file_size_mb - self.yesterday_avg_file_size_mb

    @property
    def is_worsening(self) -> bool:
        """True when the fleet is structurally piling up small files faster than it's
        clearing them -- the small-file ratio is rising AND average file size is falling.
        Either alone can be noise (a single large new table skews the average); both moving
        the wrong direction together is the real signal."""
        ratio_delta = self.small_file_ratio_delta_pct
        size_delta = self.avg_file_size_delta_mb
        if ratio_delta is None or size_delta is None:
            return False
        return ratio_delta > 0.5 and size_delta < 0


@dataclass(frozen=True)
class ProductionHealthReport:
    generated_at: datetime
    checks: list[CheckResult]
    file_trend: FleetFileTrend

    @property
    def critical_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed and c.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed and c.severity == "warning")

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def overall_status(self) -> str:
        if self.critical_count:
            return "CRITICAL"
        if self.warning_count:
            return "WATCH"
        return "OK"


def _as_utc(value: datetime) -> datetime:
    """sqlite (this module's own test suite) round-trips TIMESTAMP columns as naive datetimes
    even when the value written was tz-aware -- same quirk cryo.governor.governor._as_utc and
    cryo.scheduler.scheduler._ensure_aware already work around. Real Postgres never needs this
    (its TIMESTAMPTZ columns stay aware), so this is a no-op there."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _hours_between(later: datetime, earlier: datetime) -> float:
    return (_as_utc(later) - _as_utc(earlier)).total_seconds() / 3600.0


def _check_stuck_runs(session: Session, *, now: datetime) -> CheckResult:
    """Catches PDAE-8546-shaped incidents directly: a MaintenanceRun open in status='running'
    far longer than any real run should take. This is exactly the signal that would have
    caught the Aug 14 stuck cron run (open 5 days) on day one instead of two weeks later."""
    cutoff = now - timedelta(hours=_STUCK_RUN_HOURS)
    rows = session.execute(
        select(MaintenanceRun.id, MaintenanceRun.trigger, MaintenanceRun.created_at)
        .where(MaintenanceRun.status == "running", MaintenanceRun.created_at < cutoff)
        .order_by(MaintenanceRun.created_at.asc())
        .limit(10)
    ).all()
    if not rows:
        return CheckResult(
            name="stuck_runs",
            passed=True,
            severity="info",
            summary=f"No MaintenanceRun open longer than {_STUCK_RUN_HOURS}h",
        )
    oldest_hours = _hours_between(now, rows[0].created_at)
    examples = [
        f"{r.id} ({r.trigger}, {_hours_between(now, r.created_at):.0f}h open)" for r in rows[:3]
    ]
    return CheckResult(
        name="stuck_runs",
        passed=False,
        severity="critical",
        summary=f"{len(rows)} run(s) stuck open >{_STUCK_RUN_HOURS}h (oldest {oldest_hours:.0f}h)",
        detail="A stuck run's own jobs never finalize, which can block that run's preset from "
        "building a fresh scheduled run at all -- check cryo.jobs for those run_ids' non-"
        "terminal jobs (queued/running) to find what's actually stalled underneath.",
        evidence={"count": len(rows), "examples": examples, "oldest_hours": oldest_hours},
    )


def _check_stale_jobs(session: Session, *, now: datetime) -> CheckResult:
    """Jobs stuck in running/admitted far past any real operation's duration -- the generic
    shape PDAE-8546's specific failure took (a batch dying with no terminal status update) and
    the shape a native-job-watcher regression would take again if it recurs."""
    cutoff = now - timedelta(hours=_STUCK_JOB_HOURS)
    rows = session.execute(
        select(MaintenanceJob.status, func.count())
        .where(
            MaintenanceJob.status.in_(["running", "admitted"]),
            MaintenanceJob.started_at < cutoff,
        )
        .group_by(MaintenanceJob.status)
    ).all()
    total = sum(int(n) for _, n in rows)
    if total == 0:
        return CheckResult(
            name="stale_jobs",
            passed=True,
            severity="info",
            summary=f"No jobs stuck running/admitted longer than {_STUCK_JOB_HOURS}h",
        )
    breakdown = {status: int(n) for status, n in rows}
    return CheckResult(
        name="stale_jobs",
        passed=False,
        severity="warning" if total < 50 else "critical",
        summary=f"{total} job(s) stuck running/admitted >{_STUCK_JOB_HOURS}h",
        detail="A job's K8s Job/pod likely finished (or died) without the DB status being "
        "reconciled -- check NativeJobWatcher.tick() is actually running and finding these "
        "dispatches; also feeds the metastore-concurrency admission cap (job_store."
        "count_running_jobs_by_metastore_uri), so stale rows here can throttle new admissions "
        "even when real cluster capacity is free.",
        evidence=breakdown,
    )


def _check_admission_stalls(session: Session, *, now: datetime) -> CheckResult:
    """The exact shape of the approval-gate bug: a destination with a real, non-trivial
    pending queue where recent job completions show essentially nothing landing. This doesn't
    reach into the governor's in-memory wave counters (out of process from a report job) --
    it infers the same "admitted=0 despite backlog" condition from what actually landed in the
    DB, which is what matters for whether reclaiming is happening at all."""
    recent_completions = int(
        session.execute(
            select(func.count()).where(
                MaintenanceJob.finished_at >= now - timedelta(hours=1),
                MaintenanceJob.status == "succeeded",
            )
        ).scalar_one()
    )
    backlog = int(
        session.execute(
            select(func.count()).where(MaintenanceJob.status.in_(["queued", "admitted"]))
        ).scalar_one()
    )

    if backlog < 100:
        return CheckResult(
            name="admission_stall",
            passed=True,
            severity="info",
            summary=f"Queue depth {backlog} -- too small to meaningfully stall",
        )
    if recent_completions > 0:
        return CheckResult(
            name="admission_stall",
            passed=True,
            severity="info",
            summary=f"{recent_completions} job(s) completed in the last hour against a "
            f"{backlog}-deep queue -- draining",
        )
    return CheckResult(
        name="admission_stall",
        passed=False,
        severity="critical",
        summary=f"Zero jobs completed in the last hour with a {backlog}-deep queue backlog",
        detail="This is the exact signature of the approval-gate bug found 2026-08-19: a "
        "real, non-empty queue with nothing draining. Check governor logs for the actual "
        "wave-level admitted=0 reason (approval gate, ROI gate, capacity, metastore "
        "concurrency cap) -- this check can only tell you admission has stalled, not why.",
        evidence={"backlog": backlog, "recent_completions": recent_completions},
    )


def _check_approval_match_rate(session: Session, *, now: datetime) -> CheckResult:
    """Direct canary against the exact bug found 2026-08-19: re-runs the real prefetch query
    (not a re-implementation of its logic) against every currently-approved row and reports
    what fraction still matches. 0% is exactly what the live bug produced for two weeks with
    no other symptom visible except "maintenance isn't running" three layers downstream."""
    from cryo.governor.prefetch import prefetch_approved_operation_keys

    total = int(
        session.execute(
            select(func.count()).where(ApprovalRequest.status == ApprovalStatus.APPROVED)
        ).scalar_one()
    )
    if total == 0:
        return CheckResult(
            name="approval_match_rate",
            passed=True,
            severity="info",
            summary="No approved-status rows to check (nothing pending on this gate)",
        )

    table_ids = set(
        session.execute(
            select(ApprovalRequest.table_id)
            .where(ApprovalRequest.status == ApprovalStatus.APPROVED)
            .distinct()
        ).scalars()
    )
    matched_keys = prefetch_approved_operation_keys(session, table_ids)
    matched_rows = sum(len(v) for v in matched_keys.values())
    match_rate_pct = 100.0 * matched_rows / total if total else 100.0

    if match_rate_pct >= _MIN_APPROVAL_MATCH_RATE_PCT:
        return CheckResult(
            name="approval_match_rate",
            passed=True,
            severity="info",
            summary=f"{match_rate_pct:.0f}% of {total} approved rows currently match "
            f"(threshold {_MIN_APPROVAL_MATCH_RATE_PCT:.0f}%)",
        )
    return CheckResult(
        name="approval_match_rate",
        passed=False,
        severity="critical",
        summary=f"Only {match_rate_pct:.0f}% of {total} approved rows currently match "
        f"(threshold {_MIN_APPROVAL_MATCH_RATE_PCT:.0f}%)",
        detail="prefetch_approved_operation_keys is returning far fewer matches than there "
        "are approved rows -- this is the exact shape of the 2026-08-19 bug (query filters "
        "excluding valid rows). expire_snapshots/remove_orphan_files admission will silently "
        "defer on 'destructive operation requires approval' for every table whose approval "
        "doesn't match, with no other symptom until someone notices storage isn't shrinking.",
        evidence={"total_approved": total, "matched": matched_rows, "match_rate_pct": match_rate_pct},
    )


_ORPHAN_NOSUCHKEY = "orphaned_catalog_entry (NoSuchKey)"
_ORPHAN_MISSING = "orphaned_catalog_entry (table missing)"
_NATIVE_JOB_FAILED = "native_job_failed"


def _check_repeat_failures(session: Session, *, now: datetime) -> CheckResult:
    """Catches the orphaned-catalog-entry class of problem: a table failing with the identical
    error repeatedly (not just once) over a real window -- distinct from a one-off transient
    failure, and distinct from the batch-deadline salvage case (which produces a bare
    'native_job_failed' with no distinguishing detail). Groups by a normalized error prefix
    so distinct S3 keys in an otherwise-identical NoSuchKey message still count as one pattern.
    """
    window_start = now - timedelta(days=3)
    pattern_expr = case(
        (MaintenanceJob.error.like("%NoSuchKey%"), _ORPHAN_NOSUCHKEY),
        (MaintenanceJob.error.like("%does not exist%"), _ORPHAN_MISSING),
        (MaintenanceJob.error.like("native_job_failed%"), _NATIVE_JOB_FAILED),
        else_=None,
    ).label("pattern")
    rows = session.execute(
        select(pattern_expr, func.count(), func.count(func.distinct(MaintenanceJob.table_id)))
        .where(MaintenanceJob.status == "failed", MaintenanceJob.finished_at >= window_start)
        .group_by(pattern_expr)
        .having(func.count() >= _REPEAT_FAILURE_MIN_COUNT)
    ).all()
    patterns = [(pattern, count, tables) for pattern, count, tables in rows if pattern is not None]
    if not patterns:
        return CheckResult(
            name="repeat_failures",
            passed=True,
            severity="info",
            summary="No repeat-failure pattern above threshold in the last 3 days",
        )
    total_failures = sum(int(count) for _, count, _ in patterns)
    breakdown = {
        pattern: {"failures": int(count), "tables": int(tables)}
        for pattern, count, tables in patterns
    }
    severity = "critical" if total_failures > 500 else "warning"
    top_pattern, top_count, top_tables = max(patterns, key=lambda p: p[1])
    return CheckResult(
        name="repeat_failures",
        passed=False,
        severity=severity,
        summary=f"{total_failures} repeat failures across {len(patterns)} pattern(s) in 3 days "
        f"-- worst: {top_pattern} ({top_count} failures, {top_tables} tables)",
        detail="A table hitting the same error repeatedly (not once) means retries are "
        "wasting compute against a condition that won't resolve itself -- e.g. orphaned "
        "catalog entries need de-registration, not another retry.",
        evidence=breakdown,
    )


def _check_queue_growth(session: Session, *, now: datetime) -> CheckResult:
    """Pending-queue depth day-over-day -- a generic 'losing ground' signal independent of
    which specific mechanism is at fault. Reads from cryo.jobs (status counts), not Redis
    directly, since a report job shouldn't need a live Redis connection to do its own check --
    if Redis and Postgres ever disagree about queue depth that's a separate, worse problem."""
    today_depth = int(
        session.execute(
            select(func.count()).where(MaintenanceJob.status.in_(["queued", "admitted"]))
        ).scalar_one()
    )

    # Approximate yesterday's queue depth via jobs that were still queued/admitted as of this
    # time yesterday: their parent run was created before then (MaintenanceJob itself has no
    # created_at of its own -- a job is created at the same time as its run), and the job
    # hadn't finished yet by then.
    yesterday = now - timedelta(days=1)
    yesterday_depth = int(
        session.execute(
            select(func.count())
            .select_from(MaintenanceJob)
            .join(MaintenanceRun, MaintenanceRun.id == MaintenanceJob.run_id)
            .where(
                MaintenanceRun.created_at <= yesterday,
                (MaintenanceJob.finished_at.is_(None)) | (MaintenanceJob.finished_at > yesterday),
                MaintenanceJob.status.not_in(["failed", "succeeded", "skipped", "cancelled"]),
            )
        ).scalar_one()
    )

    if yesterday_depth < _QUEUE_GROWTH_MIN_BASELINE:
        # A tiny baseline (or zero) makes percent-growth meaningless -- e.g. 5 -> 20,617 reads
        # as "+412,240%" which is technically correct but tells an operator nothing. Fall back
        # to judging the absolute depth against admission_stall's own "meaningfully large"
        # threshold instead, so a real backlog still surfaces, just not via a distorted ratio.
        if today_depth < 100:
            return CheckResult(
                name="queue_growth",
                passed=True,
                severity="info",
                summary=f"Queue depth {today_depth} (baseline too small yesterday "
                f"[{yesterday_depth}] for a percentage to be meaningful)",
            )
        return CheckResult(
            name="queue_growth",
            passed=False,
            severity="warning",
            summary=f"Queue depth {today_depth} -- yesterday's baseline ({yesterday_depth}) was "
            f"too small for a percentage to be meaningful, but the absolute depth is real",
            detail="admission_stall (above) is the check that judges whether this depth is "
            "actually draining -- this one exists only to flag the depth itself when the "
            "day-over-day percentage can't be trusted.",
            evidence={"today": today_depth, "yesterday": yesterday_depth},
        )
    growth_pct = 100.0 * (today_depth - yesterday_depth) / yesterday_depth
    if growth_pct <= _QUEUE_GROWTH_ALERT_PCT:
        return CheckResult(
            name="queue_growth",
            passed=True,
            severity="info",
            summary=f"Queue depth {today_depth} ({growth_pct:+.0f}% vs yesterday)",
        )
    return CheckResult(
        name="queue_growth",
        passed=False,
        severity="warning",
        summary=f"Queue depth grew {growth_pct:+.0f}% to {today_depth} "
        f"(was {yesterday_depth}, threshold +{_QUEUE_GROWTH_ALERT_PCT:.0f}%)",
        detail="Admission isn't keeping pace with new work -- check the other checks above "
        "for a specific stalled mechanism, or this may just be real fleet growth outpacing "
        "current slot_cap/replica sizing.",
        evidence={"today": today_depth, "yesterday": yesterday_depth, "growth_pct": growth_pct},
    )


def _check_scan_freshness(session: Session, *, now: datetime) -> CheckResult:
    """Is the scanner itself still running -- distinct from any single table's scan being
    old. Compares each active table's table_stats.scan_at (the scanner's one-row-per-table
    write) against a generous staleness window; a large cluster of active tables all stale
    together is the scanner-stalled shape (cron not firing, scan_queue backed up, go_scan
    binary erroring on startup), not individually slow tables."""
    active_total = int(
        session.execute(
            select(func.count())
            .select_from(Table)
            .where(Table.is_active.is_(True), Table.table_name.not_like(f"%{_EVOLVING_SUFFIX}"))
        ).scalar_one()
    )
    if active_total == 0:
        return CheckResult(
            name="scan_freshness",
            passed=True,
            severity="info",
            summary="No active tables to check",
        )
    cutoff = now - timedelta(hours=_SCAN_STALE_HOURS)
    fresh_count = int(
        session.execute(
            select(func.count())
            .select_from(TableStats)
            .join(Table, Table.id == TableStats.table_id)
            .where(
                Table.is_active.is_(True),
                Table.table_name.not_like(f"%{_EVOLVING_SUFFIX}"),
                TableStats.scan_at >= cutoff,
            )
        ).scalar_one()
    )
    stale_count = active_total - fresh_count
    if stale_count < _SCAN_STALE_MIN_COUNT:
        return CheckResult(
            name="scan_freshness",
            passed=True,
            severity="info",
            summary=f"{fresh_count}/{active_total} active tables scanned within {_SCAN_STALE_HOURS}h",
        )
    severity = "critical" if stale_count > active_total * 0.5 else "warning"
    return CheckResult(
        name="scan_freshness",
        passed=False,
        severity=severity,
        summary=f"{stale_count}/{active_total} active tables have no scan within {_SCAN_STALE_HOURS}h",
        detail="A large cluster of stale scans together (not just a few individually old "
        "tables) usually means the scanner's own cron/queue stopped feeding it, not that "
        "these specific tables are broken -- check scan_queue depth and go_scan's own liveness "
        "before assuming a per-table problem.",
        evidence={"active_total": active_total, "fresh_count": fresh_count, "stale_count": stale_count},
    )


def _check_scan_failures(session: Session, *, now: datetime) -> CheckResult:
    """Tables the scanner can reach but can't successfully read -- consecutive_scan_failures
    already resets to 0 on any success (persistence.py), so a nonzero streak here is real,
    ongoing breakage, not noise from one bad run. A cluster of these appearing together (not
    just steady-state individually-broken tables) is itself worth flagging -- it's the same
    "orphaned catalog entry" shape health_check.py's repeat_failures check catches on the job
    side, mirrored here on the scan side."""
    rows = session.execute(
        select(Table.id, Table.schema_name, Table.table_name, Table.consecutive_scan_failures)
        .where(
            Table.is_active.is_(True),
            Table.table_name.not_like(f"%{_EVOLVING_SUFFIX}"),
            Table.consecutive_scan_failures >= _SCAN_CONSECUTIVE_FAILURE_THRESHOLD,
        )
        .order_by(Table.consecutive_scan_failures.desc())
        .limit(10)
    ).all()
    if not rows:
        return CheckResult(
            name="scan_failures",
            passed=True,
            severity="info",
            summary=f"No active table with {_SCAN_CONSECUTIVE_FAILURE_THRESHOLD}+ consecutive scan failures",
        )
    total_failing = len(rows)
    # rows is capped at 10 by the query LIMIT -- get the real total separately so a fleet with
    # e.g. 400 failing tables doesn't under-report as "10".
    real_total = int(
        session.execute(
            select(func.count())
            .select_from(Table)
            .where(
                Table.is_active.is_(True),
                Table.table_name.not_like(f"%{_EVOLVING_SUFFIX}"),
                Table.consecutive_scan_failures >= _SCAN_CONSECUTIVE_FAILURE_THRESHOLD,
            )
        ).scalar_one()
    )
    examples = [f"{r.schema_name}.{r.table_name} ({r.consecutive_scan_failures} failures)" for r in rows[:3]]
    passed = real_total < _SCAN_FAILING_TABLES_MIN_COUNT
    return CheckResult(
        name="scan_failures",
        passed=passed,
        severity="info" if passed else ("critical" if real_total > 200 else "warning"),
        summary=f"{real_total} active table(s) with {_SCAN_CONSECUTIVE_FAILURE_THRESHOLD}+ "
        f"consecutive scan failures",
        detail=None
        if passed
        else "Each of these fails to even read, not just needs maintenance -- check "
        "last_scan_error on the worst offenders; a cluster appearing together (vs. steady "
        "individually-broken tables) suggests a shared cause (credentials, a source-wide "
        "outage) rather than N unrelated table problems.",
        evidence={"total_failing": real_total, "examples": examples} if not passed else {},
    )


def _check_cost_ingestion_freshness(session: Session, *, now: datetime) -> CheckResult:
    """Is cost ingestion (table_cost, written by cost_signals.py / s3_request_cost_ingest.py)
    still running. Cost pricing directly feeds the S3-cost side of the north-star reporting and
    the cost-ceiling admission gate -- stale cost data doesn't fail loudly, it just quietly
    freezes cost numbers and ROI-gate decisions at whatever they were last computed."""
    active_total = int(
        session.execute(
            select(func.count())
            .select_from(Table)
            .where(Table.is_active.is_(True), Table.table_name.not_like(f"%{_EVOLVING_SUFFIX}"))
        ).scalar_one()
    )
    if active_total == 0:
        return CheckResult(
            name="cost_ingestion_freshness",
            passed=True,
            severity="info",
            summary="No active tables to check",
        )
    cutoff = now - timedelta(hours=_COST_STALE_HOURS)
    priced_total = int(
        session.execute(
            select(func.count())
            .select_from(TableCost)
            .join(Table, Table.id == TableCost.table_id)
            .where(Table.table_name.not_like(f"%{_EVOLVING_SUFFIX}"))
        ).scalar_one()
    )
    if priced_total == 0:
        return CheckResult(
            name="cost_ingestion_freshness",
            passed=True,
            severity="info",
            summary="No table_cost rows yet -- cost ingestion not yet run for any table",
        )
    fresh_count = int(
        session.execute(
            select(func.count())
            .select_from(TableCost)
            .join(Table, Table.id == TableCost.table_id)
            .where(Table.table_name.not_like(f"%{_EVOLVING_SUFFIX}"), TableCost.scan_at >= cutoff)
        ).scalar_one()
    )
    stale_count = priced_total - fresh_count
    stale_pct = 100.0 * stale_count / priced_total if priced_total else 0.0
    if stale_pct < 50.0:
        return CheckResult(
            name="cost_ingestion_freshness",
            passed=True,
            severity="info",
            summary=f"{fresh_count}/{priced_total} priced tables refreshed within {_COST_STALE_HOURS}h",
        )
    return CheckResult(
        name="cost_ingestion_freshness",
        passed=False,
        severity="critical" if stale_pct > 90.0 else "warning",
        summary=f"{stale_count}/{priced_total} priced tables ({stale_pct:.0f}%) not refreshed "
        f"within {_COST_STALE_HOURS}h",
        detail="Cost ingestion feeds both the north-star S3-cost reporting and the cost-"
        "ceiling admission gate -- when this goes stale, both keep reporting old numbers "
        "without any error, which reads as 'nothing changed' rather than 'ingestion stopped'.",
        evidence={"priced_total": priced_total, "fresh_count": fresh_count, "stale_count": stale_count},
    )


def compute_fleet_file_trend(session: Session, *, now: datetime | None = None) -> FleetFileTrend:
    now = now or datetime.now(UTC)
    today_row = session.execute(
        select(
            func.sum(TableStats.total_files),
            func.sum(TableStats.small_files_count),
            func.sum(TableStats.total_size_gb),
            func.count(),
        )
    ).one()
    today_total_files = int(today_row[0] or 0)
    today_small_files = int(today_row[1] or 0)
    today_size_gb = float(today_row[2] or 0.0)
    tables_counted = int(today_row[3] or 0)
    today_avg_mb = (today_size_gb * 1024) / today_total_files if today_total_files else None

    # "Yesterday" fleet totals from each table's latest TableStatsHistory row scanned before
    # the cutoff -- a row_number() window function (portable across sqlite/Postgres) rather
    # than Postgres-only DISTINCT ON, picking the most recent scan per table_id as of then.
    yesterday_cutoff = now - timedelta(days=1)
    row_num = (
        func.row_number()
        .over(
            partition_by=TableStatsHistory.table_id,
            order_by=TableStatsHistory.scan_at.desc(),
        )
        .label("rn")
    )
    ranked = (
        select(
            TableStatsHistory.table_id,
            TableStatsHistory.total_files,
            TableStatsHistory.small_files_count,
            TableStatsHistory.total_size_gb,
            row_num,
        )
        .where(TableStatsHistory.scan_at < yesterday_cutoff)
        .subquery()
    )
    yesterday_row = session.execute(
        select(
            func.sum(ranked.c.total_files),
            func.sum(ranked.c.small_files_count),
            func.sum(ranked.c.total_size_gb),
        ).where(ranked.c.rn == 1)
    ).one()
    yesterday_total_files = int(yesterday_row[0]) if yesterday_row[0] is not None else None
    yesterday_small_files = int(yesterday_row[1]) if yesterday_row[1] is not None else None
    yesterday_size_gb = float(yesterday_row[2]) if yesterday_row[2] is not None else None
    yesterday_avg_mb = (
        (yesterday_size_gb * 1024) / yesterday_total_files
        if yesterday_total_files and yesterday_size_gb is not None
        else None
    )

    return FleetFileTrend(
        today_total_files=today_total_files,
        today_small_files=today_small_files,
        today_total_size_gb=today_size_gb,
        today_avg_file_size_mb=today_avg_mb,
        yesterday_total_files=yesterday_total_files,
        yesterday_small_files=yesterday_small_files,
        yesterday_total_size_gb=yesterday_size_gb,
        yesterday_avg_file_size_mb=yesterday_avg_mb,
        tables_counted=tables_counted,
    )


_ALL_CHECKS = (
    _check_stuck_runs,
    _check_stale_jobs,
    _check_admission_stalls,
    _check_approval_match_rate,
    _check_repeat_failures,
    _check_queue_growth,
    _check_scan_freshness,
    _check_scan_failures,
    _check_cost_ingestion_freshness,
)


def run_production_health_checks(
    session: Session, *, now: datetime | None = None
) -> ProductionHealthReport:
    now = now or datetime.now(UTC)
    results: list[CheckResult] = []
    for check_fn in _ALL_CHECKS:
        try:
            results.append(check_fn(session, now=now))
        except Exception as exc:
            logger.exception("Production health check %s failed to run", check_fn.__name__)
            results.append(
                CheckResult(
                    name=check_fn.__name__.lstrip("_"),
                    passed=False,
                    severity="warning",
                    summary=f"Check itself failed to run: {exc}",
                    detail="A check erroring is not the same as a check failing -- treat as "
                    "'unknown', not 'confirmed broken', but still worth fixing the check.",
                )
            )

    file_trend = compute_fleet_file_trend(session, now=now)
    if file_trend.is_worsening:
        ratio_delta = file_trend.small_file_ratio_delta_pct or 0.0
        results.append(
            CheckResult(
                name="fleet_small_file_trend",
                passed=False,
                severity="warning",
                summary=f"Fleet small-file ratio rose {ratio_delta:+.1f}pp and avg file size "
                "fell day-over-day -- files are piling up faster than maintenance clears them",
                detail="Both signals moving the wrong direction together (not just one, which "
                "can be noise from a single new large/small table) means the fleet's net "
                "compaction throughput is currently negative.",
                evidence={
                    "small_file_ratio_delta_pct": ratio_delta,
                    "avg_file_size_delta_mb": file_trend.avg_file_size_delta_mb,
                },
            )
        )
    else:
        results.append(
            CheckResult(
                name="fleet_small_file_trend",
                passed=True,
                severity="info",
                summary="Fleet small-file ratio and average file size not both worsening",
            )
        )

    return ProductionHealthReport(generated_at=now, checks=results, file_trend=file_trend)
