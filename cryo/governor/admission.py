from __future__ import annotations

from collections.abc import Mapping
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
    last_maintained_at: datetime | None = None
    deadline_at: datetime | None = None


@dataclass(frozen=True)
class AdmissionConfig:
    offpeak_start_ist: int = 0
    offpeak_end_ist: int = 7
    per_replica_cap: int = 5
    wave_wait_sec: int = 300
    adaptive_budget: bool = True
    high_load_ratio: float = 0.8
    high_load_fraction: float = 0.5


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

    if not cooldown_elapsed(job.last_maintained_at, job.cooldown_hours, current):
        return AdmissionDecision(False, "cooldown", defer=True)

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


def is_heavy_mode(mode: Mode | str) -> bool:
    return Mode(mode) in {Mode.STANDARD, Mode.DEEP}


def is_offpeak(now: datetime, start_hour_ist: int, end_hour_ist: int) -> bool:
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
