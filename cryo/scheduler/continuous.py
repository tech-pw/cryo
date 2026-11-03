"""Continuous (drift-triggered) maintenance: when a table becomes due (#77).

Pure: no I/O. The scheduler loads a table's drift row and recent history, and this module
decides whether one job should be enqueued now, later, or not at all. Continuous mode only
changes *when* a job becomes due; the job is then admitted by the governor's normal gates,
exactly like a cron job.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from cryo.domain.enums import Mode, OperationType
from cryo.planner.guardrails import MODE_OPERATIONS

CONTINUOUS_TRIGGER_PREFIX = "continuous"

SIGNAL_COMMITS = "commits"
SIGNAL_SMALL_FILES = "small_files"
SIGNAL_DELETE_RATIO = "delete_ratio"
SIGNAL_SCORE_DROP = "score_drop"

# AuditLog actions. Not maintenance operations: they record why a job did or didn't start.
AUDIT_TRIGGER = "continuous_trigger"
AUDIT_COALESCED = "continuous_coalesced"
AUDIT_DEFERRED = "continuous_deferred"
AUDIT_RESCAN = "continuous_rescan"
AUDIT_ACTIONS = frozenset({AUDIT_TRIGGER, AUDIT_COALESCED, AUDIT_DEFERRED, AUDIT_RESCAN})

_THRESHOLDS = (
    ("max_commits", SIGNAL_COMMITS),
    ("max_small_files_added", SIGNAL_SMALL_FILES),
    ("max_delete_file_ratio", SIGNAL_DELETE_RATIO),
    ("max_score_drop", SIGNAL_SCORE_DROP),
)


def continuous_trigger(signal: str) -> str:
    return f"{CONTINUOUS_TRIGGER_PREFIX}:{signal}"


def is_continuous_trigger(trigger: str | None) -> bool:
    return bool(trigger) and str(trigger).startswith(f"{CONTINUOUS_TRIGGER_PREFIX}:")


@dataclass(frozen=True)
class ContinuousConfig:
    enabled: bool = False
    max_commits: int | None = None
    max_small_files_added: int | None = None
    max_delete_file_ratio: float | None = None
    max_score_drop: int | None = None
    debounce_seconds: int = 300
    max_delay_seconds: int = 3600
    min_interval_seconds: int = 1800
    max_runs_per_hour: int | None = 2
    max_runs_per_day: int | None = 24
    max_maintenance_commits_per_day: int | None = 48

    def thresholds(self) -> list[tuple[str, float]]:
        return [
            (signal, float(value))
            for field, signal in _THRESHOLDS
            if (value := getattr(self, field)) is not None
        ]


_INT_FIELDS: dict[str, tuple[int, int]] = {
    "max_commits": (1, 1_000_000),
    "max_small_files_added": (1, 100_000_000),
    "max_score_drop": (1, 100),
    "debounce_seconds": (0, 86_400),
    "max_delay_seconds": (60, 7 * 86_400),
    "min_interval_seconds": (60, 7 * 86_400),
    "max_runs_per_hour": (1, 60),
    "max_runs_per_day": (1, 1_440),
    "max_maintenance_commits_per_day": (1, 100_000),
}
_FLOAT_FIELDS: dict[str, tuple[float, float]] = {"max_delete_file_ratio": (0.0, 1.0)}
_KNOWN = {"enabled", *_INT_FIELDS, *_FLOAT_FIELDS}


def parse_continuous(raw: Mapping[str, Any] | None) -> tuple[ContinuousConfig | None, list[str]]:
    """Validate a preset's ``continuous`` block. Returns (config, errors); None means off."""
    if raw is None:
        return None, []
    if not isinstance(raw, Mapping):
        return None, ["continuous must be an object"]
    errors = [f"continuous.{key}: unknown field" for key in sorted(set(raw) - _KNOWN)]
    values: dict[str, Any] = {}
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        errors.append("continuous.enabled: must be true or false")
    values["enabled"] = bool(enabled)
    for key, (low, high) in _INT_FIELDS.items():
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"continuous.{key}: must be an integer")
        elif not low <= value <= high:
            errors.append(f"continuous.{key}: must be between {low} and {high}")
        else:
            values[key] = value
    for key, (flow, fhigh) in _FLOAT_FIELDS.items():
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"continuous.{key}: must be a number")
        elif not flow < float(value) <= fhigh:
            errors.append(f"continuous.{key}: must be greater than {flow} and at most {fhigh}")
        else:
            values[key] = float(value)
    config = ContinuousConfig(**values)
    if config.enabled and not config.thresholds():
        errors.append(
            "continuous: enable at least one threshold (max_commits, max_small_files_added, "
            "max_delete_file_ratio or max_score_drop)"
        )
    if config.debounce_seconds > config.max_delay_seconds:
        errors.append("continuous.debounce_seconds: must not exceed max_delay_seconds")
    if (
        config.max_runs_per_hour is not None
        and config.max_runs_per_day is not None
        and config.max_runs_per_hour > config.max_runs_per_day
    ):
        errors.append("continuous.max_runs_per_hour: must not exceed max_runs_per_day")
    return config, errors


def validate_continuous_preset(
    raw: Mapping[str, Any] | None, *, mode: Mode | str, params: Mapping[str, Any] | None
) -> list[str]:
    """Server-side validation of a preset's continuous block against the rest of the preset.

    Compaction commits snapshots, so a continuous preset that never expires them inflates the
    very snapshot count it is meant to control. Its mode must plan expire_snapshots and its
    params must not skip it.
    """
    config, errors = parse_continuous(raw)
    if config is None or not config.enabled:
        return errors
    mode_name = mode.value if isinstance(mode, Mode) else str(mode)
    try:
        mode_ops = MODE_OPERATIONS.get(Mode(mode_name), frozenset())
    except ValueError:
        mode_ops = frozenset()
    if OperationType.EXPIRE_SNAPSHOTS not in mode_ops:
        errors.append(
            f"continuous: mode {mode_name} does not plan expire_snapshots; continuous presets "
            "must expire the snapshots compaction adds"
        )
    skipped = {str(item) for item in (params or {}).get("skip_operations") or []}
    if OperationType.EXPIRE_SNAPSHOTS.value in skipped:
        errors.append(
            "continuous: params.skip_operations drops expire_snapshots; continuous presets "
            "must expire the snapshots compaction adds"
        )
    return errors


@dataclass(frozen=True)
class DriftView:
    commits: int = 0
    small_files_added: int = 0
    delete_file_ratio: float = 0.0
    score_drop: int = 0
    first_commit_at: datetime | None = None
    last_commit_at: datetime | None = None


@dataclass(frozen=True)
class History:
    last_maintained_at: datetime | None = None
    last_continuous_at: datetime | None = None
    runs_last_hour: int = 0
    runs_last_day: int = 0
    maintenance_commits_last_day: int = 0
    oldest_run_last_hour: datetime | None = None
    oldest_run_last_day: datetime | None = None


class Verdict(StrEnum):
    IDLE = "idle"
    WAIT = "wait"
    DEFER = "defer"
    DUE = "due"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str
    signal: str | None = None
    value: float | None = None
    threshold: float | None = None
    retry_at: datetime | None = None

    @property
    def detail(self) -> str:
        parts = [self.reason]
        if self.signal is not None:
            parts.append(
                f"signal={self.signal} value={_fmt(self.value)} threshold={_fmt(self.threshold)}"
            )
        if self.retry_at is not None:
            parts.append(f"retry_at={self.retry_at.isoformat()}")
        return "; ".join(parts)


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:g}"


def crossed_threshold(
    config: ContinuousConfig, drift: DriftView
) -> tuple[str, float, float] | None:
    """The first threshold the drift has reached, as (signal, value, threshold)."""
    measured = {
        SIGNAL_COMMITS: float(drift.commits),
        SIGNAL_SMALL_FILES: float(drift.small_files_added),
        SIGNAL_DELETE_RATIO: float(drift.delete_file_ratio),
        SIGNAL_SCORE_DROP: float(drift.score_drop),
    }
    for signal, threshold in config.thresholds():
        if measured[signal] >= threshold:
            return signal, measured[signal], threshold
    return None


def evaluate(
    config: ContinuousConfig,
    drift: DriftView,
    history: History,
    *,
    now: datetime,
) -> Decision:
    """Decide whether the table is due. Order: threshold, debounce, min interval, rate
    limits, churn cap. Every non-due verdict past the threshold carries a retry time."""
    if not config.enabled or drift.commits <= 0:
        return Decision(Verdict.IDLE, "no drift")
    crossed = crossed_threshold(config, drift)
    if crossed is None:
        return Decision(Verdict.IDLE, "below thresholds")
    signal, value, threshold = crossed

    def held(verdict: Verdict, reason: str, retry_at: datetime) -> Decision:
        return Decision(verdict, reason, signal, value, threshold, retry_at)

    if drift.last_commit_at is not None and drift.first_commit_at is not None:
        quiet_at = drift.last_commit_at + timedelta(seconds=config.debounce_seconds)
        forced_at = drift.first_commit_at + timedelta(seconds=config.max_delay_seconds)
        if now < quiet_at and now < forced_at:
            return held(Verdict.WAIT, "debounce", min(quiet_at, forced_at))

    last_activity = max(
        (at for at in (history.last_maintained_at, history.last_continuous_at) if at is not None),
        default=None,
    )
    if last_activity is not None:
        next_allowed = last_activity + timedelta(seconds=config.min_interval_seconds)
        if now < next_allowed:
            return held(Verdict.DEFER, "min_interval", next_allowed)

    if config.max_runs_per_hour is not None and history.runs_last_hour >= config.max_runs_per_hour:
        anchor = history.oldest_run_last_hour or now
        return held(Verdict.DEFER, "rate_limit_hour", anchor + timedelta(hours=1))
    if config.max_runs_per_day is not None and history.runs_last_day >= config.max_runs_per_day:
        anchor = history.oldest_run_last_day or now
        return held(Verdict.DEFER, "rate_limit_day", anchor + timedelta(days=1))
    if (
        config.max_maintenance_commits_per_day is not None
        and history.maintenance_commits_last_day >= config.max_maintenance_commits_per_day
    ):
        return held(Verdict.DEFER, "churn_cap", now + timedelta(hours=1))

    return Decision(Verdict.DUE, "threshold crossed", signal, value, threshold)
