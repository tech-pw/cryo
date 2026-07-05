"""SLA threshold resolution from preset guardrails."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SLAThresholds:
    freshness_hours: int
    maintenance_days: int
    p95_latency_ms: int


DEFAULT_SLA = SLAThresholds(freshness_hours=24, maintenance_days=7, p95_latency_ms=5000)


def sla_from_guardrails(guardrails: Mapping[str, Any] | None) -> SLAThresholds:
    data = guardrails or {}
    return SLAThresholds(
        freshness_hours=int(data.get("sla_freshness_hours", DEFAULT_SLA.freshness_hours)),
        maintenance_days=int(data.get("sla_maintenance_days", DEFAULT_SLA.maintenance_days)),
        p95_latency_ms=int(data.get("sla_p95_latency_ms", DEFAULT_SLA.p95_latency_ms)),
    )
