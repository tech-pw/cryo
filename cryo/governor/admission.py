from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from math import floor
from typing import TYPE_CHECKING

from cryo.domain.enums import Mode
from cryo.domain.models import CapacitySnapshot

if TYPE_CHECKING:
    from cryo.worker.worker import DestinationConfig, MaintenanceJobRecord

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
    # Operator override: skip the soft scheduling gates below (deadline/cooldown/benefit
    # score/moop score/offpeak). Table locks, PII, and ABAC checks live outside this
    # function entirely and are never affected. See MaintenanceJob.force_admit.
    force_admit: bool = False


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
    # Amoro's "Balanced" scheduling policy borrowed: pure top-k pack (below) can starve a
    # correctly-scored-but-lower table indefinitely if higher-scoring tables keep refilling the
    # queue every wave -- there is no mechanism today for "it's been neglected" to matter on its
    # own. Off by default (0.0 boost is a no-op) so existing deployments' admission order is
    # unchanged until explicitly opted in. See docs/amoro-evaluation.md §3.1/§5 (Move 25a).
    staleness_boost_days: float = 0.0
    staleness_boost_max: float = 0.5


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

    if job.force_admit:
        return AdmissionDecision(True)

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


def _staleness_boost(
    job: AdmissionJob,
    config: AdmissionConfig,
    *,
    now: datetime,
) -> float:
    """Amoro-style occupation-ratio fairness, borrowed narrowly: a bounded score boost for a
    table that hasn't been maintained in a while, so it can eventually outrank a merely
    higher-scoring table that gets maintained every wave. ``staleness_boost_days<=0`` disables
    this entirely (returns 0.0, a pure no-op on the existing sort). A table with no
    ``last_maintained_at`` at all (never maintained, or the field wasn't populated) gets the
    full boost immediately -- it has been "starved" for its entire lifetime, not merely since
    some baseline. The ramp is linear from 0 at ``staleness_boost_days`` to
    ``staleness_boost_max`` at ``2 * staleness_boost_days``, then flat -- this is a tiebreak
    against starvation, not a replacement for the underlying score; it must never be able to
    push an unscored/zero-value table above a genuinely high-value one by an unbounded amount.
    """
    if config.staleness_boost_days <= 0:
        return 0.0
    if job.last_maintained_at is None:
        return config.staleness_boost_max
    idle_days = (_ensure_aware(now) - _ensure_aware(job.last_maintained_at)).total_seconds() / 86400
    if idle_days <= config.staleness_boost_days:
        return 0.0
    ramp = (idle_days - config.staleness_boost_days) / config.staleness_boost_days
    return min(config.staleness_boost_max, max(0.0, ramp) * config.staleness_boost_max)


def pack_fleet_budget(
    jobs: Sequence[AdmissionJob],
    config: AdmissionConfig,
    *,
    now: datetime | None = None,
) -> dict[str, AdmissionDecision]:
    """AutoComp §A2 "Fleet Decide": greedy top-k GB-budget pack.

    Sorts by moop_score (falling back to estimated_benefit_score, then unscored-last) DESC,
    plus a bounded staleness boost (see ``_staleness_boost``) when
    ``config.staleness_boost_days`` is set, and admits jobs while they fit
    ``config.fleet_budget_gb`` -- first-fit-decreasing, so a big job that doesn't fit doesn't
    block smaller ones after it from using the remaining budget.

    Returns a per-job_id decision; callers should treat "not admissible" here the same as any
    other ``decide_job_admission`` rejection (defer with the given reason). When
    ``config.fleet_budget_gb`` is None the gate is disabled and every job is admissible --
    same convention as the per-replica slot cap being a separate, always-on mechanism this
    doesn't replace.

    2026-09-04 fix (docs/admission-ordering-and-scheduling.md, "Consequences" -- the fleet-pack
    architecture review): a job with ``size_gb=None`` (cost genuinely unknown, not "zero") was
    silently treated as ``size_gb or 0.0`` -- a free pass that always fits regardless of real
    remaining budget, and can starve correctly-sized jobs behind it in sort order. Unknown cost
    is not a safe default; it's missing information. Such jobs are now deferred with a distinct
    reason (not admitted, not charged against the budget) so a caller can route them through a
    separate bounded exploration allowance instead of trusting an absent estimate.
    """
    if config.fleet_budget_gb is None:
        return {job.job_id: AdmissionDecision(True) for job in jobs}

    current = _ensure_aware(now or datetime.now(UTC))

    def sort_key(job: AdmissionJob) -> float:
        if job.moop_score is not None:
            base = job.moop_score
        elif job.estimated_benefit_score is not None:
            base = job.estimated_benefit_score
        else:
            base = float("-inf")
        if base == float("-inf"):
            return base
        return base + _staleness_boost(job, config, now=current)

    decisions: dict[str, AdmissionDecision] = {}
    remaining = config.fleet_budget_gb
    for job in sorted(jobs, key=sort_key, reverse=True):
        if job.size_gb is None:
            decisions[job.job_id] = AdmissionDecision(False, "unknown_cost", defer=True)
            continue
        size = job.size_gb
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


def admission_config_from_destination(
    destination: DestinationConfig, default: AdmissionConfig
) -> AdmissionConfig:
    """The one place a destination's config becomes an AdmissionConfig, shared by the governor
    and the deferred-job re-queue check so they cannot disagree about what admission would do."""
    config = destination.config
    per_replica_cap = config.get("per_replica_cap", config.get("slot_cap", default.per_replica_cap))
    return AdmissionConfig(
        offpeak_start_ist=int(config.get("offpeak_start_ist", default.offpeak_start_ist)),
        offpeak_end_ist=int(config.get("offpeak_end_ist", default.offpeak_end_ist)),
        per_replica_cap=int(per_replica_cap),
        wave_wait_sec=int(config.get("wave_wait_sec", default.wave_wait_sec)),
        adaptive_budget=bool(config.get("adaptive_budget", default.adaptive_budget)),
        high_load_ratio=float(config.get("high_load_ratio", default.high_load_ratio)),
        high_load_fraction=float(config.get("high_load_fraction", default.high_load_fraction)),
        default_cooldown_hours=int(
            config.get("default_cooldown_hours", default.default_cooldown_hours)
        ),
        min_benefit_score=float(config.get("min_benefit_score", default.min_benefit_score)),
        min_moop_score=(
            float(config["min_moop_score"])
            if config.get("min_moop_score") is not None
            else default.min_moop_score
        ),
        fleet_budget_gb=(
            float(config["fleet_budget_gb"])
            if config.get("fleet_budget_gb") is not None
            else default.fleet_budget_gb
        ),
        staleness_boost_days=float(
            config.get("staleness_boost_days", default.staleness_boost_days)
        ),
        staleness_boost_max=float(config.get("staleness_boost_max", default.staleness_boost_max)),
    )


def admission_job_from_record(job: MaintenanceJobRecord) -> AdmissionJob:
    params = dict(job.params) if job.params else {}
    estimated_benefit = params.get("min_expected_benefit_score")
    if estimated_benefit is None:
        estimated_benefit = params.get("benefit_score")
    moop_score = params.get("moop_score")
    size_gb = params.get("size_gb") or params.get("total_size_gb")
    return AdmissionJob(
        job_id=job.id,
        table_id=job.table_id,
        destination_id=job.destination_id,
        mode=job.mode,
        cooldown_hours=job.cooldown_hours,
        estimated_benefit_score=float(estimated_benefit) if estimated_benefit is not None else None,
        moop_score=float(moop_score) if moop_score is not None else None,
        size_gb=float(size_gb) if size_gb is not None else None,
        last_maintained_at=job.last_maintained_at,
        deadline_at=job.deadline_at,
        force_admit=job.force_admit,
    )
