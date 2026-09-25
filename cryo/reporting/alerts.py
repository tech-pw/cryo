"""Fleet cost alerts for the in-app attention view.

Drift alerts stay on ``GET /advanced/anomalies``. This module does not rescan
table history and does not post to Slack. A stable cost signal is not an alert,
and a missing percentage is not rendered as zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from cryo.reporting.cost_signals import (
    CostSignal,
    MaintenanceCorrelation,
    compute_cost_signal,
    correlate_with_maintenance,
)

AlertKind = Literal["cost"]
AlertSeverity = Literal["low", "high"]


@dataclass(frozen=True)
class FleetAlert:
    kind: AlertKind
    severity: AlertSeverity
    summary: str
    explained: bool


def alert_from_cost(
    signal: CostSignal,
    correlation: MaintenanceCorrelation | None,
) -> FleetAlert | None:
    if signal.direction == "stable" or signal.primary_signal is None:
        return None
    pct = signal.dod_pct if signal.primary_signal == "dod" else signal.wow_pct
    window = "day-over-day" if signal.primary_signal == "dod" else "week-over-week"
    if pct is None:
        move = f"Fleet cost {signal.direction} ({window})"
    else:
        move = f"Fleet cost {signal.direction} {pct:+.1f}% {window}"
    explained = bool(correlation and correlation.explains_change)
    reason = (
        correlation.summary
        if correlation is not None
        else "No maintenance correlation was computed."
    )
    return FleetAlert(
        kind="cost",
        severity="low" if explained else "high",
        summary=f"{move}. {reason}",
        explained=explained,
    )


def collect_cost_alert(session: Session, *, now: datetime | None = None) -> FleetAlert | None:
    signal = compute_cost_signal(session, now=now)
    correlation = correlate_with_maintenance(session, signal, now=now)
    return alert_from_cost(signal, correlation)
