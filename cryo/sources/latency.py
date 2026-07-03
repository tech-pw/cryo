from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class LatencySample:
    operation: str
    duration_ms: float


_MONITOR_REGISTRY: dict[str, CatalogLatencyMonitor] = {}


def get_catalog_latency_monitor(source_id: str) -> CatalogLatencyMonitor:
    return _MONITOR_REGISTRY.setdefault(source_id, CatalogLatencyMonitor())


class CatalogLatencyMonitor:
    """Track P50/P99 latency for catalog operations."""

    def __init__(self) -> None:
        self._samples: dict[str, list[float]] = defaultdict(list)

    def record(self, operation: str, duration_ms: float) -> None:
        self._samples[operation].append(duration_ms)

    def summary(self, operation: str) -> dict[str, float]:
        values = sorted(self._samples.get(operation, []))
        if not values:
            return {"p50_ms": 0.0, "p99_ms": 0.0, "count": 0.0}
        return {
            "p50_ms": _percentile(values, 50),
            "p99_ms": _percentile(values, 99),
            "count": float(len(values)),
        }

    def all_summaries(self) -> dict[str, dict[str, float]]:
        return {operation: self.summary(operation) for operation in self._samples}


def timed_catalog_call[T](
    monitor: CatalogLatencyMonitor,
    operation: str,
    fn: Callable[[], T],
) -> T:
    start = time.perf_counter()
    try:
        return fn()
    finally:
        monitor.record(operation, (time.perf_counter() - start) * 1000)


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, int(len(values) * percentile / 100) - 1))
    return round(values[index], 3)
