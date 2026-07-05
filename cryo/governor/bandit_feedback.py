"""Persist bandit learning signals across Governor (admit) and Worker (complete) processes."""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryo.governor.admission import AdmissionConfig
from cryo.governor.bandit import AdmissionBandit

_STATE_DIR = Path(os.environ.get("CRYO_STATE_DIR", "/tmp/cryo"))
_BANDIT_PATH = _STATE_DIR / "admission_bandit.json"
_PENDING_PATH = _STATE_DIR / "admission_pending.json"


def _state_dir() -> Path:
    return Path(os.environ.get("CRYO_STATE_DIR", "/tmp/cryo"))


def _bandit_path() -> Path:
    return _state_dir() / "admission_bandit.json"


def _pending_path() -> Path:
    return _state_dir() / "admission_pending.json"


def _ensure_state_dir() -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)


def _load_pending() -> dict[str, dict[str, float | int | bool]]:
    path = _pending_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_pending(pending: dict[str, dict[str, float | int | bool]]) -> None:
    _ensure_state_dir()
    _pending_path().write_text(json.dumps(pending), encoding="utf-8")


def register_admission(job_id: str, config: AdmissionConfig) -> None:
    """Record the admission config used when a job is admitted."""
    pending = _load_pending()
    pending[job_id] = {
        "offpeak_start_ist": config.offpeak_start_ist,
        "offpeak_end_ist": config.offpeak_end_ist,
        "per_replica_cap": config.per_replica_cap,
        "wave_wait_sec": config.wave_wait_sec,
        "adaptive_budget": config.adaptive_budget,
        "high_load_ratio": config.high_load_ratio,
        "high_load_fraction": config.high_load_fraction,
    }
    _save_pending(pending)


def finalize_admission(job_id: str, *, job_succeeded: bool) -> None:
    """Update bandit state when a previously admitted job reaches a terminal status."""
    pending = _load_pending()
    raw = pending.pop(job_id, None)
    if raw is None:
        return
    _save_pending(pending)

    config = AdmissionConfig(
        offpeak_start_ist=int(raw["offpeak_start_ist"]),
        offpeak_end_ist=int(raw["offpeak_end_ist"]),
        per_replica_cap=int(raw["per_replica_cap"]),
        wave_wait_sec=int(raw["wave_wait_sec"]),
        adaptive_budget=bool(raw["adaptive_budget"]),
        high_load_ratio=float(raw["high_load_ratio"]),
        high_load_fraction=float(raw["high_load_fraction"]),
    )
    bandit = AdmissionBandit.load(_bandit_path())
    bandit.record_outcome(config, admitted=True, job_completed=job_succeeded)
    bandit.save(_bandit_path())


def record_deferred_admission(config: AdmissionConfig) -> None:
    """Record a deferred admission decision (not enough capacity / off-peak / cooldown)."""
    bandit = AdmissionBandit.load(_bandit_path())
    bandit.record_outcome(config, admitted=False, job_completed=False)
    bandit.save(_bandit_path())
