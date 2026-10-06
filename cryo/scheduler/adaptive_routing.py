from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cryo.domain.enums import EngineType
from cryo.scheduler.routing import DestinationRoute, resolve_destination
from cryo.scheduler.rules import PresetConfig, TableCandidate


@dataclass(frozen=True)
class DestinationSignal:
    destination_id: str
    engine: str
    available_slots: int = 0
    catalog_p99_ms: float = 0.0
    estimated_cost_usd: float = 0.0


@dataclass(frozen=True)
class AdaptiveRoutingContext:
    signals: Sequence[DestinationSignal] = ()
    catalog_p99_ms: float = 0.0


@dataclass(frozen=True)
class RankedDestination:
    destination_id: str
    engine: str
    score: float
    reason: str


def resolve_destination_adaptive(
    table: TableCandidate,
    preset: PresetConfig,
    destinations: Mapping[str, Mapping[str, Any]],
    context: AdaptiveRoutingContext | None = None,
    *,
    default_destination_id: str | None = None,
) -> DestinationRoute:
    """Resolve destination using explicit rules first, then adaptive scoring."""
    explicit = resolve_destination(
        table,
        preset,
        destinations,
        default_destination_id=default_destination_id,
    )
    if explicit.reason != "preset_default":
        return explicit

    if not preset.guardrails.get("adaptive_routing"):
        return explicit

    ranked = rank_destinations(table, preset, destinations, context)
    if not ranked:
        return explicit

    best = ranked[0]
    return DestinationRoute(destination_id=best.destination_id, reason=best.reason)


def rank_destinations(
    table: TableCandidate,
    preset: PresetConfig,
    destinations: Mapping[str, Mapping[str, Any]],
    context: AdaptiveRoutingContext | None = None,
) -> list[RankedDestination]:
    """Score destinations for a table using size, capacity, latency, and cost signals.

    A destination that has failed this specific table `max_consecutive_failures_before_
    exclusion` times in a row (default 2) is excluded -- scored -inf, so it never wins while
    any non-excluded destination exists in the pool. Regression context, 2026-08-10:
    unified_bq.gold_dbt_pw_bq failed on the native engine 4 times running (8 days, 4 different
    error messages) with nothing in this scoring function aware of that history -- it scored
    an already-4x-failed table identically to one that had never been tried. The failure
    signal itself (table.row["engine_consecutive_failures"]) is computed once per scheduler
    tick from real MaintenanceJob history, not invented here -- see
    SQLSchedulerStore.consecutive_engine_failures().
    """
    context = context or AdaptiveRoutingContext()
    pool = _destination_pool(preset, destinations)
    signals_by_id = {signal.destination_id: signal for signal in context.signals}
    size_gb = float(table.row.get("total_size_gb") or 0)
    preferences = preset.guardrails.get("engine_preferences") or {}
    failure_threshold = int(preferences.get("max_consecutive_failures_before_exclusion") or 2)
    failures_by_destination = table.row.get("engine_consecutive_failures") or {}
    ranked: list[RankedDestination] = []

    for destination_id in pool:
        entry = destinations.get(destination_id) or {}
        engine = str(entry.get("engine") or EngineType.TRINO.value)
        consecutive_failures = int(failures_by_destination.get(destination_id) or 0)
        if consecutive_failures >= failure_threshold:
            ranked.append(
                RankedDestination(
                    destination_id=destination_id,
                    engine=engine,
                    score=float("-inf"),
                    reason=f"excluded:consecutive_failures={consecutive_failures}",
                )
            )
            continue
        signal = signals_by_id.get(destination_id, DestinationSignal(destination_id, engine))
        score, reason = _score_destination(
            engine=engine,
            size_gb=size_gb,
            available_slots=signal.available_slots,
            catalog_p99_ms=max(signal.catalog_p99_ms, context.catalog_p99_ms),
            estimated_cost_usd=signal.estimated_cost_usd,
            preferences=preferences,
        )
        ranked.append(
            RankedDestination(
                destination_id=destination_id,
                engine=engine,
                score=score,
                reason=reason,
            )
        )

    return sorted(ranked, key=lambda item: item.score, reverse=True)


def _destination_pool(
    preset: PresetConfig,
    destinations: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    pool = preset.guardrails.get("engine_pool")
    if isinstance(pool, str):
        pool = [pool]
    if isinstance(pool, Sequence) and not isinstance(pool, (str, bytes)):
        resolved = []
        for key in pool:
            entry = destinations.get(str(key))
            if entry is None:
                continue
            resolved.append(str(entry.get("id") or key))
        if resolved:
            return resolved

    return [
        str(entry.get("id") or destination_id)
        for destination_id, entry in destinations.items()
        if isinstance(entry, Mapping) and entry.get("enabled", True) is not False
    ]


def _score_destination(
    *,
    engine: str,
    size_gb: float,
    available_slots: int,
    catalog_p99_ms: float,
    estimated_cost_usd: float,
    preferences: Mapping[str, Any],
) -> tuple[float, str]:
    score = 0.0
    reasons: list[str] = []

    if engine == EngineType.SPARK_EMR.value and size_gb >= float(
        preferences.get("spark_min_gb") or 100
    ):
        score += 40
        reasons.append("large_table_spark")
    elif engine == EngineType.TRINO.value and size_gb < float(
        preferences.get("spark_min_gb") or 100
    ):
        score += 30
        reasons.append("small_table_trino")
    elif engine == EngineType.NATIVE.value:
        native_min = float(preferences.get("native_min_gb") or 50)
        if size_gb >= native_min:
            score += 35
            reasons.append("native_candidate")

    if available_slots > 0:
        slot_bonus = min(available_slots, 10) * 2
        score += slot_bonus
        reasons.append(f"capacity:{available_slots}")

    latency_threshold = float(preferences.get("latency_p99_ms") or 500)
    if catalog_p99_ms <= latency_threshold:
        score += 15
        reasons.append("low_catalog_latency")
    elif catalog_p99_ms > latency_threshold * 2:
        score -= 10
        reasons.append("high_catalog_latency")

    if estimated_cost_usd > 0:
        score -= min(estimated_cost_usd, 20)
        reasons.append("cost_penalty")

    if not reasons:
        reasons.append("default_engine_score")

    return score, "adaptive:" + ",".join(reasons)
