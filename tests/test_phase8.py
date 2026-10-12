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


def test_rank_destinations_excludes_destination_with_repeated_failures() -> None:
    """Regression, 2026-08-10: warehouse.gold_events failed on native 4 times in a row
    across 8 days -- adaptive routing's size/capacity/latency/cost scoring had no memory of
    that and would keep scoring native identically to a never-tried table. A destination
    with >= the failure threshold consecutive failures on THIS table must be excluded, even
    though it would otherwise win purely on size (native is the larger-table favorite here)."""
    preset = PresetConfig(
        id="p1",
        name="escalate",
        destination_id="native-1",
        guardrails={"engine_pool": ["native-1", "spark-large"]},
    )
    table = TableCandidate(
        "t1",
        "raw",
        "events",
        {
            "total_size_gb": 24000,
            "engine_consecutive_failures": {"native-1": 4},
        },
    )
    destinations = {
        "native-1": {"id": "native-1", "engine": "native"},
        "spark-large": {"id": "spark-large", "engine": "spark_emr"},
    }
    ranked = rank_destinations(table, preset, destinations)
    assert ranked[0].destination_id == "spark-large"
    excluded = next(r for r in ranked if r.destination_id == "native-1")
    assert excluded.score == float("-inf")
    assert excluded.reason == "excluded:consecutive_failures=4"


def test_rank_destinations_does_not_exclude_below_threshold() -> None:
    """A single failure shouldn't trip the exclusion -- the default threshold is 2."""
    preset = PresetConfig(
        id="p1",
        name="escalate",
        destination_id="native-1",
        guardrails={"engine_pool": ["native-1", "spark-large"]},
    )
    table = TableCandidate(
        "t1",
        "raw",
        "events",
        {"total_size_gb": 24000, "engine_consecutive_failures": {"native-1": 1}},
    )
    destinations = {
        "native-1": {"id": "native-1", "engine": "native"},
        "spark-large": {"id": "spark-large", "engine": "spark_emr"},
    }
    ranked = rank_destinations(table, preset, destinations)
    native_result = next(r for r in ranked if r.destination_id == "native-1")
    assert native_result.score != float("-inf")


def test_rank_destinations_failure_threshold_is_configurable() -> None:
    preset = PresetConfig(
        id="p1",
        name="escalate",
        destination_id="native-1",
        guardrails={
            "engine_pool": ["native-1", "spark-large"],
            "engine_preferences": {"max_consecutive_failures_before_exclusion": 1},
        },
    )
    table = TableCandidate(
        "t1",
        "raw",
        "events",
        {"total_size_gb": 24000, "engine_consecutive_failures": {"native-1": 1}},
    )
    destinations = {
        "native-1": {"id": "native-1", "engine": "native"},
        "spark-large": {"id": "spark-large", "engine": "spark_emr"},
    }
    ranked = rank_destinations(table, preset, destinations)
    assert ranked[0].destination_id == "spark-large"


def test_rank_destinations_fails_open_when_every_destination_is_excluded() -> None:
    """A route must always resolve to *something* -- if every candidate in the pool has
    failed past the threshold, still return the least-bad option rather than nothing."""
    table = TableCandidate(
        "t1",
        "raw",
        "events",
        {"total_size_gb": 24000, "engine_consecutive_failures": {"native-1": 9}},
    )
    destinations = {"native-1": {"id": "native-1", "engine": "native"}}
    route = resolve_destination_adaptive(
        table,
        PresetConfig(
            id="p1",
            name="escalate",
            destination_id="native-1",
            guardrails={"adaptive_routing": True, "engine_pool": ["native-1"]},
        ),
        destinations,
        AdaptiveRoutingContext(),
    )
    assert route.destination_id == "native-1"


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
