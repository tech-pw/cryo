from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryo.scheduler.adaptive_routing import (
    AdaptiveRoutingContext,
    DestinationSignal,
    rank_destinations,
    resolve_destination_adaptive,
)
from cryo.scheduler.rules import PresetConfig, TableCandidate
from cryo.tiering.policies import TierPolicy
from cryo.tiering.service import tier_candidates_for_table


def test_adaptive_routing_prefers_spark_for_large_table() -> None:
    preset = PresetConfig(
        id="p1",
        name="adaptive",
        destination_id="trino-default",
        guardrails={
            "adaptive_routing": True,
            "engine_pool": ["trino-default", "spark-large"],
        },
    )
    table = TableCandidate("t1", "raw", "events", {"total_size_gb": 200})
    destinations = {
        "trino-default": {"id": "trino-default", "name": "trino-default", "engine": "trino"},
        "spark-large": {"id": "spark-large", "name": "spark-large", "engine": "spark_emr"},
    }
    route = resolve_destination_adaptive(table, preset, destinations, AdaptiveRoutingContext())
    assert route.destination_id == "spark-large"
    assert route.reason.startswith("adaptive:")


def test_explicit_routing_rule_wins_over_adaptive() -> None:
    preset = PresetConfig(
        id="p1",
        name="mixed",
        destination_id="trino-default",
        guardrails={
            "adaptive_routing": True,
            "routing_rules": [
                {
                    "predicate": {"field": "total_size_gb", "op": ">", "value": 100},
                    "destination": "spark-large",
                }
            ],
        },
    )
    table = TableCandidate("t1", "raw", "events", {"total_size_gb": 200})
    destinations = {
        "trino-default": {"id": "trino-default", "name": "trino-default", "engine": "trino"},
        "spark-large": {"id": "spark-large", "name": "spark-large", "engine": "spark_emr"},
    }
    route = resolve_destination_adaptive(table, preset, destinations)
    assert route.reason == "routing_rule:spark-large"


def test_rank_destinations_considers_capacity() -> None:
    preset = PresetConfig(
        id="p1",
        name="cap",
        destination_id="trino-default",
        guardrails={"engine_pool": ["trino-default", "spark-large"]},
    )
    table = TableCandidate("t1", "raw", "events", {"total_size_gb": 10})
    destinations = {
        "trino-default": {"id": "trino-default", "engine": "trino"},
        "spark-large": {"id": "spark-large", "engine": "spark_emr"},
    }
    context = AdaptiveRoutingContext(
        signals=[
            DestinationSignal("trino-default", "trino", available_slots=8),
            DestinationSignal("spark-large", "spark_emr", available_slots=1),
        ]
    )
    ranked = rank_destinations(table, preset, destinations, context)
    assert ranked[0].destination_id == "trino-default"


def test_tier_candidates_by_age() -> None:
    old = (datetime.now(UTC) - timedelta(days=45)).isoformat()
    candidates = tier_candidates_for_table(
        "iceberg.raw.events",
        [{"partition_value": "2024-01-01", "last_write_at": old}],
        [TierPolicy(name="cold", storage_class="GLACIER", min_age_days=30)],
    )
    assert len(candidates) == 1
    assert candidates[0].target_storage_class == "GLACIER"
    assert candidates[0].age_days >= 30


def test_tier_skips_young_partitions() -> None:
    recent = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    candidates = tier_candidates_for_table(
        "iceberg.raw.events",
        [{"partition_value": "2026-07-01", "last_write_at": recent}],
        [TierPolicy(name="cold", storage_class="GLACIER", min_age_days=30)],
    )
    assert candidates == []
