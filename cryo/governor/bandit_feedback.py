"""Persist bandit learning signals across Governor (admit) and Worker (complete) processes."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import cast

from cryo.db.session import SessionLocal
from cryo.governor.admission import AdmissionConfig
from cryo.governor.bandit import AdmissionBandit
from cryo.governor.bandit_store import load_bandit, save_bandit

logger = logging.getLogger(__name__)


def _state_dir() -> Path:
    return Path(os.environ.get("CRYO_STATE_DIR", "/tmp/cryo"))


def _pending_path() -> Path:
    return _state_dir() / "admission_pending.json"


def _ensure_state_dir() -> None:
    _state_dir().mkdir(parents=True, exist_ok=True)


def _load_bandit() -> AdmissionBandit | None:
    try:
        with SessionLocal() as session:
            return load_bandit(session)
    except Exception as exc:
        logger.error("Bandit state unavailable from Postgres; skipping learning update: %s", exc)
        return None


def _persist_bandit(bandit: AdmissionBandit) -> None:
    try:
        with SessionLocal() as session:
            save_bandit(session, bandit)
    except Exception as exc:
        logger.error("Failed to persist bandit state to Postgres; update skipped: %s", exc)


def _load_pending() -> dict[str, dict[str, float | int | bool]]:
    path = _pending_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cast(dict[str, dict[str, float | int | bool]], raw)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_pending(pending: dict[str, dict[str, float | int | bool]]) -> None:
    _ensure_state_dir()
    _pending_path().write_text(json.dumps(pending), encoding="utf-8")


def _learning_enabled() -> bool:
    """Whether to record bandit learning signals at all.

    Each of the three entry points below runs per job, and every one of them does real I/O:
    a load-modify-save of a single Postgres row, or a read-modify-write of a node-local JSON
    file. Nothing reads what they record unless the tuning half is also enabled, so when it
    is off this is pure cost on the governor's hottest path -- a wave deferring 800 jobs meant
    ~1,600 round trips and 800 commits contending on one row.
    """
    from cryo.config import get_settings

    return get_settings().governor_bandit_tuning_enabled


def register_admission(job_id: str, config: AdmissionConfig) -> None:
    """Record the admission config used when a job is admitted."""
    if not _learning_enabled():
        return
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
    if not _learning_enabled():
        return
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
    bandit = _load_bandit()
    if bandit is None:
        return
    bandit.record_outcome(config, admitted=True, job_completed=job_succeeded)
    _persist_bandit(bandit)


def record_deferred_admission(config: AdmissionConfig) -> None:
    """Record a deferred admission decision (not enough capacity / off-peak / cooldown)."""
    if not _learning_enabled():
        return
    bandit = _load_bandit()
    if bandit is None:
        return
    bandit.record_outcome(config, admitted=False, job_completed=False)
    _persist_bandit(bandit)
