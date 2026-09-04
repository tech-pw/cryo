from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.domain.enums import Mode
from cryo.domain.models import CapacitySnapshot
from cryo.governor.admission import (
    AdmissionConfig,
    AdmissionDecision,
    AdmissionJob,
    compute_admission_budget,
    cooldown_elapsed,
    decide_job_admission,
    pack_fleet_budget,
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


def test_force_admit_bypasses_cooldown() -> None:
    """Regression, 2026-07-22: last_maintained_at is computed live from the most recent
    successful AuditLog entry, not stored per-job -- so repeatedly re-running the same
    table for manual testing keeps re-arming its cooldown clock, and any other pending job
    for that table (or a retried defer of the same job) gets stuck on "cooldown" with no
    way for an operator to unstick it short of editing DB/Redis state directly. force_admit
    is the operator escape hatch: it must skip cooldown (and the other soft scheduling
    gates) entirely."""
    now = datetime(2026, 6, 29, 20, 0, tzinfo=UTC)
    config = AdmissionConfig(wave_wait_sec=300)
    job = AdmissionJob(
        "job-1",
        "table-1",
        "dest",
        Mode.LIGHT,
        cooldown_hours=24,
        last_maintained_at=now - timedelta(minutes=5),
        deadline_at=now + timedelta(seconds=200),  # would also defer as deadline_near
        force_admit=True,
    )
    decision = decide_job_admission(job, config, now=now)
    assert decision.admissible
    assert decision.reason == "ok"
    assert not decision.defer


def test_min_benefit_score_rejects_jobs_below_threshold() -> None:
    config = AdmissionConfig(min_benefit_score=5.0)
    low = AdmissionJob("job-1", "table-1", "dest", Mode.LIGHT, estimated_benefit_score=2.0)
    decision = decide_job_admission(low, config)
    assert not decision.admissible
    assert decision.reason == "benefit_below_threshold"
    assert decision.defer

    high = AdmissionJob("job-2", "table-2", "dest", Mode.LIGHT, estimated_benefit_score=10.0)
    assert decide_job_admission(high, config).admissible


def test_min_benefit_score_gate_is_disabled_when_score_is_unknown_or_threshold_is_zero() -> None:
    # No score computed yet (estimated_benefit_score=None) -- can't compare against a
    # threshold, so don't block on it.
    unscored = AdmissionJob("job-1", "table-1", "dest", Mode.LIGHT, estimated_benefit_score=None)
    assert decide_job_admission(unscored, AdmissionConfig(min_benefit_score=5.0)).admissible

    # Threshold at the 0.0 default disables the gate even for a scored job.
    scored = AdmissionJob("job-2", "table-2", "dest", Mode.LIGHT, estimated_benefit_score=0.1)
    assert decide_job_admission(scored, AdmissionConfig()).admissible


def test_min_moop_score_rejects_jobs_below_threshold_including_negative_scores() -> None:
    config = AdmissionConfig(min_moop_score=0.0)
    # A real MOOP score can be negative (T2 compute-cost term is subtracted) -- must still be
    # rejected, not treated as "no score" the way a 0-sentinel would incorrectly do.
    negative = AdmissionJob("job-1", "table-1", "dest", Mode.LIGHT, moop_score=-0.2)
    decision = decide_job_admission(negative, config)
    assert not decision.admissible
    assert decision.reason == "below_moop_score"

    positive = AdmissionJob("job-2", "table-2", "dest", Mode.LIGHT, moop_score=0.4)
    assert decide_job_admission(positive, config).admissible


def test_min_moop_score_gate_disabled_by_default_none() -> None:
    job = AdmissionJob("job-1", "table-1", "dest", Mode.LIGHT, moop_score=-99.0)
    assert decide_job_admission(job, AdmissionConfig()).admissible


def test_pack_fleet_budget_admits_highest_moop_score_first_until_budget_exhausted() -> None:
    config = AdmissionConfig(fleet_budget_gb=15.0)
    jobs = [
        AdmissionJob("cold", "t1", "dest", Mode.STANDARD, moop_score=0.1, size_gb=10.0),
        AdmissionJob("hot", "t2", "dest", Mode.STANDARD, moop_score=0.9, size_gb=10.0),
        AdmissionJob("warm", "t3", "dest", Mode.STANDARD, moop_score=0.5, size_gb=4.0),
    ]

    decisions = pack_fleet_budget(jobs, config)

    # hot (10GB) admitted first, leaving 5GB; warm (4GB) fits next; cold (10GB) doesn't fit in
    # the remaining 1GB even though it was considered before warm's leftover space -- confirms
    # this is first-fit-decreasing, not "stop at the first job that doesn't fit".
    assert decisions["hot"].admissible
    assert decisions["warm"].admissible
    assert not decisions["cold"].admissible
    assert decisions["cold"].reason == "budget_exhausted"
    assert decisions["cold"].defer


def test_pack_fleet_budget_falls_back_to_benefit_score_then_treats_unscored_as_lowest() -> None:
    config = AdmissionConfig(fleet_budget_gb=5.0)
    jobs = [
        AdmissionJob("unscored", "t1", "dest", Mode.STANDARD, size_gb=1.0),
        AdmissionJob(
            "benefit-only", "t2", "dest", Mode.STANDARD, estimated_benefit_score=3.0, size_gb=4.0
        ),
    ]

    decisions = pack_fleet_budget(jobs, config)

    assert decisions["benefit-only"].admissible
    assert decisions["unscored"].admissible  # still fits in the leftover 1GB


def test_pack_fleet_budget_disabled_admits_everyone_when_budget_is_none() -> None:
    jobs = [AdmissionJob("job-1", "t1", "dest", Mode.STANDARD, size_gb=999.0)]
    decisions = pack_fleet_budget(jobs, AdmissionConfig())
    assert decisions["job-1"] == AdmissionDecision(True)


def test_pack_fleet_budget_defers_unknown_cost_instead_of_treating_it_as_free() -> None:
    """2026-09-04 fix (docs/admission-ordering-and-scheduling.md): size_gb=None previously
    fell through `job.size_gb or 0.0`, so a job with genuinely unknown cost was treated as
    zero cost -- it always fit the budget regardless of real remaining capacity, and could
    consume a slot ahead of a correctly-sized job later in sort order. Unknown cost must
    defer, not silently pass for free."""
    config = AdmissionConfig(fleet_budget_gb=5.0)
    jobs = [
        AdmissionJob("unknown-cost", "t1", "dest", Mode.STANDARD, moop_score=0.9, size_gb=None),
        AdmissionJob("known-cost", "t2", "dest", Mode.STANDARD, moop_score=0.1, size_gb=5.0),
    ]

    decisions = pack_fleet_budget(jobs, config)

    assert not decisions["unknown-cost"].admissible
    assert decisions["unknown-cost"].reason == "unknown_cost"
    assert decisions["unknown-cost"].defer
    # The unknown-cost job (sorted first by moop_score) must not have consumed any budget --
    # the known-cost job right behind it still gets the full 5GB.
    assert decisions["known-cost"].admissible
