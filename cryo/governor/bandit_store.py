"""Postgres-backed persistence for governor bandit state."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import GovernorBanditState
from cryo.governor.bandit import AdmissionBandit, BanditState, _ArmStats

_STATE_NAME = "admission_bandit"


def load_bandit(session: Session | None) -> AdmissionBandit:
    if session is None:
        return AdmissionBandit()
    row = session.scalar(select(GovernorBanditState).where(GovernorBanditState.name == _STATE_NAME))
    if row is None or not row.payload:
        return AdmissionBandit()
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    state = BanditState(observations=int(payload.get("observations", 0)))
    for key, arm_data in (payload.get("arms") or {}).items():
        if isinstance(arm_data, dict):
            state.arms[key] = _ArmStats(
                successes=float(arm_data.get("successes", 1)),
                failures=float(arm_data.get("failures", 1)),
            )
    return AdmissionBandit(state)


def save_bandit(session: Session, bandit: AdmissionBandit) -> None:
    payload: dict[str, Any] = {
        "observations": bandit.state.observations,
        "arms": {
            key: {"successes": arm.successes, "failures": arm.failures}
            for key, arm in bandit.state.arms.items()
        },
    }
    row = session.scalar(select(GovernorBanditState).where(GovernorBanditState.name == _STATE_NAME))
    if row is None:
        row = GovernorBanditState(name=_STATE_NAME, payload=payload)
        session.add(row)
    else:
        row.payload = payload
    session.commit()
