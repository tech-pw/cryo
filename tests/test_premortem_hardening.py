"""Premortem hardening tests."""

from __future__ import annotations

from cryo.governor.admission import AdmissionConfig
from cryo.governor.bandit import AdmissionBandit, BanditState, _ArmStats
from cryo.security.crypto import decrypt_secret, encrypt_secret


def test_aws_secret_roundtrip_encryption() -> None:
    plain = "AKIAEXAMPLESECRET"
    stored = encrypt_secret(plain)
    assert stored is not None
    assert stored.startswith("enc:")
    assert decrypt_secret(stored) == plain


def test_legacy_plaintext_secrets_still_readable() -> None:
    assert decrypt_secret("legacy-plaintext-key") == "legacy-plaintext-key"


def test_bandit_confidence_uses_composite_arm_keys() -> None:
    state = BanditState(
        observations=25,
        arms={
            "high_load_ratio:0.75": _ArmStats(successes=2.0, failures=2.0),
            "high_load_fraction:0.50": _ArmStats(successes=2.0, failures=2.0),
            "per_replica_cap:5": _ArmStats(successes=2.0, failures=2.0),
        },
    )
    bandit = AdmissionBandit(state)
    base = AdmissionConfig()
    _, confident = bandit.suggest_config(base)
    assert confident is False
