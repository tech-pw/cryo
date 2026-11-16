"""When a data rewrite may commit without failing an external writer's commit.

Iceberg commits optimistically. A copy-on-write ``overwrite`` (MERGE/UPDATE/DELETE) plans the
data files it will replace against the snapshot it read; if a rewrite commits a ``replace``
that removes any of those files before the writer commits, the writer fails with "Missing
required files to delete" and the ingestion task fails, not Cryo. A merge-on-read row delta
fails the same way when the data files its deletes point at were rewritten. Appends never
conflict with a rewrite, so only ``overwrite`` and ``delete`` commits count as writers here.

The writer's commit times are in the table metadata, so a writer that commits on a schedule
(an hourly ingestion DAG) has a predictable cadence. A rewrite started right after one writer
commit, and short enough to finish before the next writer run reads the table, never races
it. ``decide_writer_window`` answers whether now is such a moment; the planner keeps each
rewrite statement small (``contended_chunk_size_gb``) so it fits.

Pure: no catalog, database or clock access. Callers pass commits and ``now``.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from cryo.domain.enums import OperationType

# Snapshot operations a writer commits that a concurrent rewrite can fail.
CONFLICTING_OPERATIONS = frozenset({"overwrite", "delete"})

# Operations that remove or replace data files, and so can fail a writer that committed
# after they started. Metadata-only operations, snapshot expiry and orphan removal never
# remove a file a live snapshot references, and position-delete rewrites leave data files
# alone.
DATA_REWRITE_OPERATIONS = frozenset(
    {
        OperationType.OPTIMIZE,
        OperationType.CONVERT_DELETES,
        OperationType.CONVERT_EQUALITY_DELETES,
        OperationType.EXPIRE_DATA,
        OperationType.MIGRATE_PARTITION_SPEC,
    }
)

WRITER_WINDOW_MARKER = "writer_window"
_RETRY_AT = re.compile(r"retry_at=(\S+)")
_MINUTE_MS = 60_000


class WindowVerdict(StrEnum):
    # No regular external writer: rewrite whenever.
    UNTRACKED = "untracked"
    # Inside the quiet gap between writer runs, with at least min_window left.
    OPEN = "open"
    # A writer run is in progress or due soon; retry after it commits.
    HOLD = "hold"
    # The writer commits so often that no gap is long enough for a rewrite.
    NO_WINDOW = "no_window"


@dataclass(frozen=True)
class WriterCommit:
    timestamp_ms: int
    operation: str | None = None

    @property
    def conflicts(self) -> bool:
        return (self.operation or "").lower() in CONFLICTING_OPERATIONS


@dataclass(frozen=True)
class WriterWindowPolicy:
    enabled: bool = True
    lookback_hours: float = 24.0
    # Writer runs (not commits) needed in the lookback before the cadence is trusted.
    min_runs: int = 4
    # Writers less frequent than this are left alone: a bounded rewrite rarely meets them.
    max_interval_minutes: float = 360.0
    # Commits closer together than this belong to the same writer run.
    run_gap_minutes: float = 10.0
    # How long before its commit a writer reads the table. A rewrite committing inside this
    # lead fails the writer, so the window closes this long before the next expected commit.
    lead_minutes: float = 15.0
    # Wait after a writer commit before starting, in case the run commits again.
    settle_minutes: float = 2.0
    # The shortest quiet gap worth starting a rewrite in.
    min_window_minutes: float = 20.0
    # Re-check interval while an expected writer commit is overdue.
    overdue_poll_minutes: float = 5.0
    # Largest rewrite statement the planner emits for a table with a regular writer.
    contended_chunk_size_gb: float = 4.0

    @classmethod
    def from_params(cls, params: Mapping[str, Any] | None) -> WriterWindowPolicy:
        """Defaults, overridden by a preset's ``writer_window`` param.

        ``writer_window: false`` turns the gate off for the preset; a mapping overrides
        individual fields (unknown keys and invalid values are ignored).
        """
        raw = (params or {}).get("writer_window")
        if raw is False:
            return cls(enabled=False)
        if not isinstance(raw, Mapping):
            return cls()
        policy = cls()
        updates: dict[str, Any] = {}
        for spec in fields(cls):
            if spec.name not in raw:
                continue
            value = raw[spec.name]
            if spec.name == "enabled":
                if isinstance(value, bool):
                    updates["enabled"] = value
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                continue
            updates[spec.name] = int(value) if spec.name == "min_runs" else float(value)
        return replace(policy, **updates)

    def minutes_ms(self, minutes: float) -> int:
        return int(minutes * _MINUTE_MS)


@dataclass(frozen=True)
class WriterCadence:
    runs: int
    interval_ms: int
    # Median time from a run's first commit to its last.
    run_span_ms: int
    last_run_start_ms: int
    last_run_end_ms: int

    @property
    def interval_minutes(self) -> float:
        return self.interval_ms / _MINUTE_MS


@dataclass(frozen=True)
class WriterWindow:
    verdict: WindowVerdict
    reason: str
    closes_at_ms: int | None = None
    retry_at_ms: int | None = None
    cadence: WriterCadence | None = None

    @property
    def allows_rewrite(self) -> bool:
        return self.verdict in {WindowVerdict.OPEN, WindowVerdict.UNTRACKED}


def writer_runs(commits: Iterable[WriterCommit], *, run_gap_ms: int) -> list[tuple[int, int]]:
    """(first, last) commit time of each writer run, oldest first."""
    times = sorted(c.timestamp_ms for c in commits if c.conflicts)
    runs: list[tuple[int, int]] = []
    for ts in times:
        if runs and ts - runs[-1][1] <= run_gap_ms:
            runs[-1] = (runs[-1][0], ts)
        else:
            runs.append((ts, ts))
    return runs


def writer_cadence(
    commits: Iterable[WriterCommit],
    *,
    now_ms: int,
    policy: WriterWindowPolicy,
) -> WriterCadence | None:
    """The cadence of the table's external writer, or None when it has no regular one."""
    since = now_ms - policy.minutes_ms(policy.lookback_hours * 60)
    recent = [c for c in commits if since <= c.timestamp_ms <= now_ms]
    runs = writer_runs(recent, run_gap_ms=policy.minutes_ms(policy.run_gap_minutes))
    if len(runs) < max(2, policy.min_runs):
        return None
    gaps = [b[0] - a[0] for a, b in zip(runs, runs[1:], strict=False)]
    interval = int(statistics.median(gaps))
    if interval <= 0 or interval > policy.minutes_ms(policy.max_interval_minutes):
        return None
    return WriterCadence(
        runs=len(runs),
        interval_ms=interval,
        run_span_ms=int(statistics.median(end - start for start, end in runs)),
        last_run_start_ms=runs[-1][0],
        last_run_end_ms=runs[-1][1],
    )


def decide_writer_window(
    cadence: WriterCadence | None,
    *,
    now_ms: int,
    policy: WriterWindowPolicy,
) -> WriterWindow:
    if cadence is None:
        return WriterWindow(
            WindowVerdict.UNTRACKED,
            f"no regular overwrite/delete writer in the last {policy.lookback_hours:g}h",
        )
    interval = cadence.interval_ms
    lead = min(policy.minutes_ms(policy.lead_minutes), interval // 2)
    settle = policy.minutes_ms(policy.settle_minutes)
    min_window = policy.minutes_ms(policy.min_window_minutes)
    every = f"writer commits every ~{cadence.interval_minutes:.0f} min"

    quiet_gap = interval - cadence.run_span_ms - lead - settle
    if quiet_gap < min_window:
        return WriterWindow(
            WindowVerdict.NO_WINDOW,
            f"{every}; the quiet gap between its runs (~{max(0, quiet_gap) // _MINUTE_MS} min) "
            f"is shorter than the {policy.min_window_minutes:g} min a rewrite needs",
            cadence=cadence,
        )

    next_start = cadence.last_run_start_ms + interval
    if now_ms > next_start + interval:
        # Missed a whole run: the writer is paused or failing. Holding would only stall
        # maintenance on a cadence that no longer exists.
        return WriterWindow(
            WindowVerdict.UNTRACKED,
            f"{every} but last committed at {iso_utc(cadence.last_run_end_ms)}, "
            "more than one interval overdue",
            cadence=cadence,
        )

    run_settled_at = max(cadence.last_run_end_ms, cadence.last_run_start_ms + cadence.run_span_ms)
    if now_ms < run_settled_at + settle:
        return WriterWindow(
            WindowVerdict.HOLD,
            f"{every}; its current run committed at {iso_utc(cadence.last_run_end_ms)} "
            "and may commit again",
            retry_at_ms=run_settled_at + settle,
            cadence=cadence,
        )

    closes_at = next_start - lead
    if closes_at - now_ms >= min_window:
        return WriterWindow(
            WindowVerdict.OPEN,
            f"{every}; quiet until {iso_utc(closes_at)}",
            closes_at_ms=closes_at,
            cadence=cadence,
        )

    retry_at = max(
        next_start + cadence.run_span_ms + settle,
        now_ms + policy.minutes_ms(policy.overdue_poll_minutes),
    )
    return WriterWindow(
        WindowVerdict.HOLD,
        f"{every}; next writer commit expected around {iso_utc(next_start)}, "
        "too close to start a rewrite",
        retry_at_ms=retry_at,
        cadence=cadence,
    )


def iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_hold(window: WriterWindow) -> str:
    """The error a job deferred by the gate carries; ``hold_retry_at`` parses it back."""
    retry = f"; retry_at={iso_utc(window.retry_at_ms)}" if window.retry_at_ms is not None else ""
    return f"{WRITER_WINDOW_MARKER}: {window.reason}{retry}"


def hold_retry_at(error: str | None) -> datetime | None:
    if not error or not error.startswith(f"{WRITER_WINDOW_MARKER}:"):
        return None
    match = _RETRY_AT.search(error)
    if match is None:
        return None
    try:
        return datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None


def stored_cadence_summary(cadence: WriterCadence | None) -> dict[str, Any] | None:
    """The scanner's copy of the cadence, stored under ``raw.commit_signals``."""
    if cadence is None:
        return None
    return {
        "runs": cadence.runs,
        "interval_minutes": round(cadence.interval_minutes, 1),
        "run_span_minutes": round(cadence.run_span_ms / _MINUTE_MS, 1),
        "last_run_at": iso_utc(cadence.last_run_end_ms),
    }


def contended_chunk_size_gb(
    stats: Mapping[str, Any],
    params: Mapping[str, Any],
) -> float | None:
    """The rewrite chunk cap for a table with a regular external writer, else None.

    Read from the last scan's commit log, so it never needs a catalog call at plan time.
    Scans older than ``writer_cadence`` fall back to the data-commit arrival gap, which also
    counts appends and so can only over-protect.
    """
    policy = WriterWindowPolicy.from_params(params)
    if not policy.enabled or policy.contended_chunk_size_gb <= 0:
        return None
    raw = stats.get("raw")
    signals = raw.get("commit_signals") if isinstance(raw, Mapping) else None
    if not isinstance(signals, Mapping):
        return None
    if "writer_cadence" in signals:
        stored = signals.get("writer_cadence")
        if not isinstance(stored, Mapping):
            return None
        runs = _number(stored.get("runs"))
        interval_minutes = _number(stored.get("interval_minutes"))
    else:
        runs = _number(signals.get("data_commits"))
        gap_hours = _number(signals.get("arrival_gap_p50_hours"))
        interval_minutes = gap_hours * 60 if gap_hours is not None else None
        since = _number(signals.get("hours_since_data_commit"))
        if since is not None and since > policy.lookback_hours:
            return None
    if runs is None or interval_minutes is None or interval_minutes <= 0:
        return None
    if runs < policy.min_runs or interval_minutes > policy.max_interval_minutes:
        return None
    return policy.contended_chunk_size_gb


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
