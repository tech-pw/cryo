from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.domain.enums import Mode
from cryo.domain.models import CapacitySnapshot
from cryo.governor.admission import (
    AdmissionConfig,
    AdmissionJob,
    compute_admission_budget,
    cooldown_elapsed,
    decide_job_admission,
)


def test_heavy_modes_are_blocked_outside_offpeak_but_light_is_allowed() -> None:
    config = AdmissionConfig(offpeak_start_ist=0, offpeak_end_ist=7)
    midday_utc = datetime(2026, 6, 30, 8, 0, tzinfo=UTC)
    job = AdmissionJob("job-1", "table-1", "dest", Mode.STANDARD)

    decision = decide_job_admission(job, config, now=midday_utc)
    assert not decision.admissible
    assert decision.reason == "outside_offpeak"
    assert decision.defer

    light = AdmissionJob("job-2", "table-1", "dest", Mode.LIGHT)
    assert decide_job_admission(light, config, now=midday_utc).admissible


def test_unset_or_zero_zero_offpeak_allows_heavy_modes_anytime() -> None:
    """No engine window (missing → AdmissionConfig 0/0) or explicit 0/0 = anytime."""
    midday_utc = datetime(2026, 6, 30, 8, 0, tzinfo=UTC)
    deep = AdmissionJob("job-1", "table-1", "dest", Mode.DEEP)

    assert decide_job_admission(deep, AdmissionConfig(), now=midday_utc).admissible
    assert decide_job_admission(
        deep,
        AdmissionConfig(offpeak_start_ist=0, offpeak_end_ist=0),
        now=midday_utc,
    ).admissible
    # Equal non-zero hours also disable the gate (full-day window).
    assert decide_job_admission(
        deep,
        AdmissionConfig(offpeak_start_ist=15, offpeak_end_ist=15),
        now=midday_utc,
    ).admissible


def test_budget_uses_max_of_live_and_local_inflight_per_replica() -> None:
    budget = compute_admission_budget(
        {"r1": CapacitySnapshot(capacity=5, used=2), "r2": CapacitySnapshot(capacity=5, used=4)},
        {"r1": 4, "r2": 1},
        AdmissionConfig(per_replica_cap=5, adaptive_budget=False),
    )

    assert {replica.replica: replica.available for replica in budget.replicas} == {
        "r1": 1,
        "r2": 1,
    }
    assert budget.total_available == 2


def test_adaptive_budget_reduces_slots_when_live_load_is_high() -> None:
    budget = compute_admission_budget(
        {"r1": CapacitySnapshot(capacity=10, used=8)},
        {},
        AdmissionConfig(high_load_ratio=0.8, high_load_fraction=0.5),
    )

    assert budget.replicas[0].available == 1
    assert budget.reasons == ("r1:adaptive_budget",)


def test_cooldown_and_deadline_defer_jobs() -> None:
    now = datetime(2026, 6, 29, 20, 0, tzinfo=UTC)
    config = AdmissionConfig(wave_wait_sec=300)

    assert not cooldown_elapsed(now - timedelta(hours=1), 4, now)
    cooldown_job = AdmissionJob(
        "job-1",
        "table-1",
        "dest",
        Mode.LIGHT,
        cooldown_hours=4,
        last_maintained_at=now - timedelta(hours=1),
    )
    assert decide_job_admission(cooldown_job, config, now=now).reason == "cooldown"

    deadline_job = AdmissionJob(
        "job-2",
        "table-2",
        "dest",
        Mode.LIGHT,
        deadline_at=now + timedelta(seconds=200),
    )
    decision = decide_job_admission(deadline_job, config, now=now)
    assert not decision.admissible
    assert decision.reason == "deadline_near"
