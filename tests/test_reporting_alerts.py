"""Cost alerts come from the existing signal and do not invent a percentage."""

from __future__ import annotations

from cryo.reporting.alerts import alert_from_cost
from cryo.reporting.cost_signals import CostSignal, MaintenanceCorrelation


def _signal(**overrides: object) -> CostSignal:
    base = dict(
        direction="spike",
        dod_pct=12.0,
        wow_pct=None,
        primary_signal="dod",
        current_cost_usd=100.0,
        baseline_dod_cost_usd=88.0,
        baseline_wow_cost_usd=None,
    )
    base.update(overrides)
    return CostSignal(**base)  # type: ignore[arg-type]


def test_stable_cost_is_not_an_alert() -> None:
    signal = _signal(direction="stable", primary_signal=None, dod_pct=1.0)
    assert alert_from_cost(signal, None) is None


def test_unexplained_spike_is_high_and_keeps_the_percent() -> None:
    correlation = MaintenanceCorrelation(
        tables_involved=0,
        actions_succeeded=0,
        bytes_freed=0,
        explains_change=False,
        summary="No successful rewrite activity in the last 1 day.",
    )
    alert = alert_from_cost(_signal(), correlation)
    assert alert is not None
    assert alert.severity == "high"
    assert alert.explained is False
    assert "+12.0%" in alert.summary
    assert "No successful rewrite" in alert.summary


def test_missing_percent_is_not_rendered_as_zero() -> None:
    alert = alert_from_cost(_signal(dod_pct=None), None)
    assert alert is not None
    assert "0.0%" not in alert.summary
    assert "No maintenance correlation" in alert.summary
