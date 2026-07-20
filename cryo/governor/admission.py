from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from math import floor

from cryo.domain.enums import Mode
from cryo.domain.models import CapacitySnapshot

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_REPLICA = "default"


@dataclass(frozen=True)
class AdmissionJob:
    job_id: str
    table_id: str
    destination_id: str
    mode: Mode | str
    cooldown_hours: int = 0
    estimated_benefit_score: float | None = None
    # AutoComp §A2 MOOP scalarization; can be negative, so None (not 0) means "not computed".
    moop_score: float | None = None
    size_gb: float | None = None
    last_maintained_at: datetime | None = None
    deadline_at: datetime | None = None


@dataclass(frozen=True)
class AdmissionConfig:
    # Equal start/end (including the 0/0 default) means no window — heavy modes
    # may run at any hour. Only a non-empty IST range gates standard/deep jobs.
    offpeak_start_ist: int = 0
    offpeak_end_ist: int = 0
    per_replica_cap: int = 5
    wave_wait_sec: int = 300
    adaptive_budget: bool = True
    high_load_ratio: float = 0.8
    high_load_fraction: float = 0.5
    default_cooldown_hours: int = 0
    min_benefit_score: float = 0.0
    # AutoComp §A2. None disables the corresponding gate (matches min_benefit_score's 0.0-means-
    # disabled convention, but moop_score can be legitimately negative so 0.0 isn't a safe
    # "disabled" sentinel for it -- None is unambiguous).
    min_moop_score: float | None = None
    fleet_budget_gb: float | None = None


@dataclass(frozen=True)
class AdmissionDecision:
    admissible: bool
    reason: str = "ok"
    defer: bool = False


@dataclass(frozen=True)
class ReplicaBudget:
    replica: str
    capacity: int
    live_used: int
    local_used: int
    available: int


@dataclass(frozen=True)
class AdmissionBudget:
    replicas: tuple[ReplicaBudget, ...]
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_available(self) -> int:
        return sum(replica.available for replica in self.replicas)


def decide_job_admission(
    job: AdmissionJob,
    config: AdmissionConfig,
    *,
    now: datetime | None = None,
) -> AdmissionDecision:
    current = _ensure_aware(now or datetime.now(UTC))
    mode = Mode(job.mode)

    if deadline_expired(job.deadline_at, current, config.wave_wait_sec):
        return AdmissionDecision(False, "deadline_near", defer=True)

    cooldown_hours = max(int(job.cooldown_hours), int(config.default_cooldown_hours))
    if not cooldown_elapsed(job.last_maintained_at, cooldown_hours, current):
        return AdmissionDecision(False, "cooldown", defer=True)

    if (
        job.estimated_benefit_score is not None
        and config.min_benefit_score > 0
        and float(job.estimated_benefit_score) < config.min_benefit_score
    ):
        return AdmissionDecision(False, "benefit_below_threshold", defer=True)

    if (
        job.moop_score is not None
        and config.min_moop_score is not None
        and float(job.moop_score) < config.min_moop_score
    ):
        return AdmissionDecision(False, "below_moop_score", defer=True)

    if is_heavy_mode(mode) and not is_offpeak(
        current, config.offpeak_start_ist, config.offpeak_end_ist
    ):
        return AdmissionDecision(False, "outside_offpeak", defer=True)

    return AdmissionDecision(True)


def compute_admission_budget(
    live_capacity: Mapping[str, CapacitySnapshot | int],
    local_in_flight: Mapping[str, int],
    config: AdmissionConfig,
) -> AdmissionBudget:
    replicas = sorted(set(live_capacity) | set(local_in_flight) or {DEFAULT_REPLICA})
    budgets: list[ReplicaBudget] = []
    reasons: list[str] = []

    for replica in replicas:
        live_snapshot = live_capacity.get(replica)
        if isinstance(live_snapshot, CapacitySnapshot):
            capacity = live_snapshot.capacity
            live_used = max(live_snapshot.used, live_snapshot.queued)
        else:
            capacity = config.per_replica_cap
            live_used = int(live_snapshot or 0)

        local_used = int(local_in_flight.get(replica, 0))
        raw_available = max(0, capacity - max(live_used, local_used))
        available = _adapt_available(raw_available, live_used, capacity, config)
        if available < raw_available:
            reasons.append(f"{replica}:adaptive_budget")

        budgets.append(
            ReplicaBudget(
                replica=replica,
                capacity=capacity,
                live_used=live_used,
                local_used=local_used,
                available=available,
            )
        )

    return AdmissionBudget(replicas=tuple(budgets), reasons=tuple(reasons))


def pack_fleet_budget(
    jobs: Sequence[AdmissionJob],
    config: AdmissionConfig,
) -> dict[str, AdmissionDecision]:
    """AutoComp §A2 "Fleet Decide": greedy top-k GB-budget pack.

    Sorts by moop_score (falling back to estimated_benefit_score, then unscored-last) DESC and
    admits jobs while they fit ``config.fleet_budget_gb`` -- first-fit-decreasing, so a big job
    that doesn't fit doesn't block smaller ones after it from using the remaining budget.

    Returns a per-job_id decision; callers should treat "not admissible" here the same as any
    other ``decide_job_admission`` rejection (defer with the given reason). When
    ``config.fleet_budget_gb`` is None the gate is disabled and every job is admissible --
    same convention as the per-replica slot cap being a separate, always-on mechanism this
    doesn't replace.
    """
    if config.fleet_budget_gb is None:
        return {job.job_id: AdmissionDecision(True) for job in jobs}

    def sort_key(job: AdmissionJob) -> float:
        if job.moop_score is not None:
            return job.moop_score
        if job.estimated_benefit_score is not None:
            return job.estimated_benefit_score
        return float("-inf")

    decisions: dict[str, AdmissionDecision] = {}
    remaining = config.fleet_budget_gb
    for job in sorted(jobs, key=sort_key, reverse=True):
        size = job.size_gb or 0.0
        if size <= remaining:
            decisions[job.job_id] = AdmissionDecision(True)
            remaining -= size
        else:
            decisions[job.job_id] = AdmissionDecision(False, "budget_exhausted", defer=True)
    return decisions


def is_heavy_mode(mode: Mode | str) -> bool:
    return Mode(mode) in {Mode.STANDARD, Mode.DEEP}


def is_offpeak(now: datetime, start_hour_ist: int, end_hour_ist: int) -> bool:
    """Return True when *now* is inside the IST off-peak window.

    Equal ``start_hour_ist`` / ``end_hour_ist`` (including both unset → 0/0)
    disables the gate so heavy maintenance may run at any hour.
    """
    hour = _ensure_aware(now).astimezone(IST).hour
    if start_hour_ist == end_hour_ist:
        return True
    if start_hour_ist < end_hour_ist:
        return start_hour_ist <= hour < end_hour_ist
    return hour >= start_hour_ist or hour < end_hour_ist


def cooldown_elapsed(
    last_maintained_at: datetime | None,
    cooldown_hours: int,
    now: datetime,
) -> bool:
    if not last_maintained_at or cooldown_hours <= 0:
        return True
    return _ensure_aware(last_maintained_at) + timedelta(hours=cooldown_hours) <= _ensure_aware(now)


def deadline_expired(
    deadline_at: datetime | None,
    now: datetime,
    wave_wait_sec: int,
) -> bool:
    if deadline_at is None:
        return False
    return _ensure_aware(now) + timedelta(seconds=wave_wait_sec) >= _ensure_aware(deadline_at)


def _adapt_available(
    raw_available: int,
    live_used: int,
    capacity: int,
    config: AdmissionConfig,
) -> int:
    if not config.adaptive_budget or raw_available <= 0 or capacity <= 0:
        return raw_available
    load_ratio = live_used / capacity
    if load_ratio < config.high_load_ratio:
        return raw_available
    return min(raw_available, max(1, floor(raw_available * config.high_load_fraction)))


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
