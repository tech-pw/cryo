from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Any


@dataclass(frozen=True)
class Anomaly:
    metric: str
    value: float
    baseline: float
    z_score: float
    severity: str


def detect_anomalies(
    history: Sequence[Mapping[str, Any]],
    *,
    metrics: Sequence[str] = (
        "total_snapshots",
        "total_delete_files",
        "manifest_count",
        "projected_savings_usd_month",
    ),
    z_threshold: float = 2.5,
) -> list[Anomaly]:
    if len(history) < 3:
        return []

    anomalies: list[Anomaly] = []
    latest = history[-1]
    baseline_rows = history[:-1]

    for metric in metrics:
        series = [_float(row.get(metric)) for row in baseline_rows if row.get(metric) is not None]
        current = _float(latest.get(metric))
        if current is None or len(series) < 2:
            continue
        baseline = mean(series)
        spread = pstdev(series) or 1.0
        z_score = (current - baseline) / spread
        if abs(z_score) >= z_threshold:
            anomalies.append(
                Anomaly(
                    metric=metric,
                    value=current,
                    baseline=round(baseline, 3),
                    z_score=round(z_score, 3),
                    severity="critical" if abs(z_score) >= z_threshold * 1.5 else "warning",
                )
            )
    return anomalies


def _float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
