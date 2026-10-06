"""Admission capacity must be what the operator configured.

A Thompson-sampling bandit used to resample per_replica_cap, high_load_ratio,
high_load_fraction, default_cooldown_hours and min_benefit_score on every wave, with no flag
to turn it off. Two problems, both live: the same fleet state could admit a different number
of jobs each wave, which makes the governor unreproducible exactly when it is being debugged;
and recording the signals it learned from did a load-modify-save of one Postgres row per job,
so a wave deferring 800 jobs meant ~1,600 round trips and 800 commits contending on that
single row -- plus a read-modify-write of a node-local JSON file per admission that multiple
governor replicas cannot keep consistent.
"""

from __future__ import annotations

import pytest

from cryo.domain.enums import EngineType
from cryo.governor.admission import AdmissionConfig
from cryo.governor.governor import _admission_config
from cryo.worker.worker import DestinationConfig

CONFIGURED_CAP = 9


@pytest.fixture
def destination() -> DestinationConfig:
    return DestinationConfig(
        id="dest",
        engine=EngineType.SPARK_EMR,
        config={"per_replica_cap": CONFIGURED_CAP, "default_cooldown_hours": 24},
    )


def test_the_configured_cap_is_used_verbatim(destination):
    config = _admission_config(destination, AdmissionConfig())

    assert config.per_replica_cap == CONFIGURED_CAP
    assert config.default_cooldown_hours == 24


def test_repeated_calls_return_the_same_config(destination):
    """The property the bandit broke: identical inputs, identical admission budget."""
    configs = [_admission_config(destination, AdmissionConfig()) for _ in range(20)]

    assert len({c.per_replica_cap for c in configs}) == 1
    assert len({c.default_cooldown_hours for c in configs}) == 1
    assert len({c.min_benefit_score for c in configs}) == 1


def test_no_database_is_touched_to_build_an_admission_config(destination, monkeypatch):
    """This ran once per destination per wave purely to consult the bandit."""

    def _explode(*_a, **_k):
        raise AssertionError("admission config must not open a database session")

    monkeypatch.setattr("cryo.db.session.SessionLocal", _explode)

    assert _admission_config(destination, AdmissionConfig()).per_replica_cap == CONFIGURED_CAP


def test_learning_signals_are_not_recorded_while_tuning_is_off(monkeypatch):
    from cryo.governor import bandit_feedback

    def _explode(*_a, **_k):
        raise AssertionError("bandit state must not be touched while tuning is disabled")

    monkeypatch.setattr(bandit_feedback, "_load_bandit", _explode)
    monkeypatch.setattr(bandit_feedback, "_load_pending", _explode)

    bandit_feedback.register_admission("job-1", AdmissionConfig())
    bandit_feedback.record_deferred_admission(AdmissionConfig())
    bandit_feedback.finalize_admission("job-1", job_succeeded=True)


def test_enabling_tuning_restores_the_learning_path(monkeypatch):
    """Off by default, not deleted -- the opt-in has to still work."""
    from cryo.governor import bandit_feedback

    monkeypatch.setattr(bandit_feedback, "_learning_enabled", lambda: True)
    recorded: list[str] = []
    monkeypatch.setattr(bandit_feedback, "_load_pending", lambda: {})
    monkeypatch.setattr(bandit_feedback, "_save_pending", lambda pending: recorded.extend(pending))

    bandit_feedback.register_admission("job-1", AdmissionConfig())

    assert recorded == ["job-1"]
