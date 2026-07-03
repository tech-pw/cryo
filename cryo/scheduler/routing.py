from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from cryo.domain.predicates import evaluate_predicate
from cryo.scheduler.rules import PresetConfig, TableCandidate


@dataclass(frozen=True)
class DestinationRoute:
    destination_id: str
    reason: str


def resolve_destination(
    table: TableCandidate,
    preset: PresetConfig,
    destinations: Mapping[str, Mapping[str, Any]],
    *,
    default_destination_id: str | None = None,
) -> DestinationRoute:
    """Resolve the execution destination for a table under a preset.

    Checks preset guardrails.routing_rules first (e.g. size_gb > 100 → spark-large),
    then falls back to the preset's configured destination_id.
    """
    routing_rules = preset.guardrails.get("routing_rules") or []
    if isinstance(routing_rules, Mapping):
        routing_rules = [routing_rules]

    for rule in routing_rules:
        if not isinstance(rule, Mapping):
            continue
        predicate = rule.get("predicate") or rule.get("when")
        destination_key = rule.get("destination") or rule.get("destination_name")
        if predicate and destination_key and evaluate_predicate(predicate, table.row):
            destination_id = _resolve_destination_id(destination_key, destinations)
            if destination_id:
                return DestinationRoute(
                    destination_id=destination_id,
                    reason=f"routing_rule:{destination_key}",
                )

    fallback = default_destination_id or preset.destination_id
    return DestinationRoute(destination_id=fallback, reason="preset_default")


def _resolve_destination_id(
    key: str,
    destinations: Mapping[str, Mapping[str, Any]],
) -> str | None:
    if key in destinations:
        entry = destinations[key]
        return str(entry.get("id") or key)
    for destination_id, entry in destinations.items():
        if str(entry.get("name")) == key:
            return str(entry.get("id") or destination_id)
    return None


def build_destinations_index(
    destinations: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for destination in destinations:
        destination_id = str(destination.get("id") or destination.get("name"))
        index[destination_id] = dict(destination)
        name = destination.get("name")
        if name:
            index[str(name)] = dict(destination)
    return index
