"""Contextual bandit tuning for Governor admission thresholds.

Learns per-knob adjustments from realized admission outcomes (admitted + completed vs
deferred/blocked). Falls back to static ``AdmissionConfig`` when confidence is low —
deliberately narrower than full OtterTune-style autotuning.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryo.governor.admission import AdmissionConfig

# Tunable knobs (small set per ARCHITECTURE_AND_AI_STRATEGY §2.6).
_KNOBS = ("high_load_ratio", "high_load_fraction", "per_replica_cap")
_MIN_OBSERVATIONS = 20
_CONFIDENCE_Z = 1.96


@dataclass
class _ArmStats:
    successes: float = 1.0  # Beta prior
    failures: float = 1.0


@dataclass
class BanditState:
    arms: dict[str, _ArmStats] = field(default_factory=dict)
    observations: int = 0

    def record(self, knob: str, *, success: bool) -> None:
        arm = self.arms.setdefault(knob, _ArmStats())
        if success:
            arm.successes += 1.0
        else:
            arm.failures += 1.0
        self.observations += 1

    def mean_success(self, knob: str) -> float:
        arm = self.arms.get(knob, _ArmStats())
        total = arm.successes + arm.failures
        return arm.successes / total if total else 0.5

    def confidence_width(self, knob: str) -> float:
        prefix = f"{knob}:"
        matching = [arm for key, arm in self.arms.items() if key.startswith(prefix)]
        if not matching:
            return 1.0
        widths = []
        for arm in matching:
            total = arm.successes + arm.failures
            if total <= 1:
                widths.append(1.0)
                continue
            p = arm.successes / total
            widths.append(_CONFIDENCE_Z * math.sqrt(p * (1 - p) / total))
        return max(widths)


class AdmissionBandit:
    """Thompson-sampling bandit over discrete knob variants."""

    def __init__(self, state: BanditState | None = None) -> None:
        self._state = state or BanditState()
        self._variants: dict[str, tuple[float, ...]] = {
            "high_load_ratio": (0.70, 0.75, 0.80, 0.85, 0.90),
            "high_load_fraction": (0.40, 0.50, 0.60, 0.70),
            "per_replica_cap": (3.0, 4.0, 5.0, 6.0, 7.0),
        }

    @property
    def state(self) -> BanditState:
        return self._state

    def suggest_config(self, base: AdmissionConfig) -> tuple[AdmissionConfig, bool]:
        """Return tuned config and whether bandit is confident enough to apply it."""
        if self._state.observations < _MIN_OBSERVATIONS:
            return base, False

        confident = self._state.observations >= _MIN_OBSERVATIONS and all(
            self._state.confidence_width(knob) < 0.25
            for knob in _KNOBS
            if any(key.startswith(f"{knob}:") for key in self._state.arms)
        )
        if not confident:
            return base, False

        ratio = self._pick_variant("high_load_ratio", base.high_load_ratio)
        fraction = self._pick_variant("high_load_fraction", base.high_load_fraction)
        cap = int(self._pick_variant("per_replica_cap", float(base.per_replica_cap)))

        tuned = AdmissionConfig(
            offpeak_start_ist=base.offpeak_start_ist,
            offpeak_end_ist=base.offpeak_end_ist,
            per_replica_cap=max(1, cap),
            wave_wait_sec=base.wave_wait_sec,
            adaptive_budget=base.adaptive_budget,
            high_load_ratio=ratio,
            high_load_fraction=fraction,
        )
        return tuned, True

    def record_outcome(
        self,
        config: AdmissionConfig,
        *,
        admitted: bool,
        job_completed: bool,
    ) -> None:
        """Record one admission decision outcome for learning."""
        success = admitted and job_completed
        self._state.record(f"high_load_ratio:{config.high_load_ratio}", success=success)
        self._state.record(f"high_load_fraction:{config.high_load_fraction}", success=success)
        self._state.record(f"per_replica_cap:{config.per_replica_cap}", success=success)
        self._state.observations += 1

    def evaluate_vs_static(
        self,
        outcomes: list[dict[str, Any]],
        static: AdmissionConfig,
    ) -> dict[str, Any]:
        """Compare bandit-tuned vs static heuristic on held-out admission records."""
        static_success = sum(1 for row in outcomes if row.get("static_success")) / max(
            len(outcomes), 1
        )
        bandit_success = sum(1 for row in outcomes if row.get("bandit_success")) / max(
            len(outcomes), 1
        )
        return {
            "static_success_rate": round(static_success, 4),
            "bandit_success_rate": round(bandit_success, 4),
            "bandit_wins": bandit_success > static_success,
            "observations": self._state.observations,
            "confident": self._state.observations >= _MIN_OBSERVATIONS,
        }

    def _pick_variant(self, knob: str, default: float) -> float:
        variants = self._variants.get(knob, (default,))
        best = default
        best_sample = -1.0
        for value in variants:
            key = f"{knob}:{value}"
            arm = self._state.arms.get(key, _ArmStats())
            # Thompson sample from Beta posterior
            sample = _beta_sample(arm.successes, arm.failures)
            if sample > best_sample:
                best_sample = sample
                best = value
        return best

    def save(self, path: Path) -> None:
        payload = {
            "observations": self._state.observations,
            "arms": {
                key: {"successes": arm.successes, "failures": arm.failures}
                for key, arm in self._state.arms.items()
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> AdmissionBandit:
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        state = BanditState(observations=int(payload.get("observations", 0)))
        for key, arm_data in (payload.get("arms") or {}).items():
            state.arms[key] = _ArmStats(
                successes=float(arm_data.get("successes", 1)),
                failures=float(arm_data.get("failures", 1)),
            )
        return cls(state)


def _beta_sample(alpha: float, beta: float) -> float:
    """Deterministic Thompson sample using ratio of Gamma draws (no numpy)."""
    # Simple approximation: mean + noise scaled by variance
    mean = alpha / (alpha + beta)
    variance = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1))
    noise = (hash((alpha, beta, id(alpha))) % 1000) / 1000.0 - 0.5
    return max(0.0, min(1.0, mean + noise * math.sqrt(variance) * 2))
