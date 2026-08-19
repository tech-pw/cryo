"""Tests for cryo/scheduler/scheduler.py's apply_priority_order -- specifically the
Trino-usage/cost weighting added 2026-08-19 (PDAE), which previously did not exist: cohort
dispatch order fell straight from compaction_priority to small_files_count, with zero
awareness of how often a table is actually queried or how much fixing it would save.
"""

from __future__ import annotations

from cryo.scheduler.rules import TableCandidate
from cryo.scheduler.scheduler import _query_weight, apply_priority_order


def _table(name: str, **stats) -> TableCandidate:
    return TableCandidate(id=name, schema_name="s", table_name=name, stats=stats)


def test_query_weight_zero_for_cold_untouched_table() -> None:
    table = _table("cold")
    assert _query_weight(table) == 0.0


def test_query_weight_increases_with_query_count() -> None:
    low = _table("low", query_count_30d=10)
    high = _table("high", query_count_30d=10_000)
    assert _query_weight(high) > _query_weight(low)


def test_query_weight_increases_with_projected_savings() -> None:
    cheap = _table("cheap", projected_savings_usd_month=1.0)
    expensive = _table("expensive", projected_savings_usd_month=500.0)
    assert _query_weight(expensive) > _query_weight(cheap)


def test_query_weight_log_scaled_not_linear() -> None:
    """A 100,000-query table must not completely drown out a 500-query one -- log1p keeps the
    tail from dominating every tier the way a hard linear scale would."""
    huge = _table("huge", query_count_30d=100_000)
    moderate = _table("moderate", query_count_30d=500)
    # Linear would be a ~200x gap; log-scaled must be far smaller.
    assert _query_weight(huge) / max(_query_weight(moderate), 1e-9) < 3.0


def test_query_weight_ignores_negative_savings() -> None:
    """projected_savings_usd_month has no business being negative, but a stray bad value must
    not push a table's weight below the true-zero baseline via log of a negative number."""
    table = _table("weird", projected_savings_usd_month=-50.0)
    assert _query_weight(table) == 0.0


def test_apply_priority_order_still_respects_compaction_priority_first() -> None:
    """An operator-set HIGH priority must still outrank a hot-but-MEDIUM table -- query
    weighting only breaks ties within a tier, it never promotes across tiers."""
    high_priority_cold = _table("a", compaction_priority="HIGH", query_count_30d=0)
    medium_priority_hot = _table("b", compaction_priority="MEDIUM", query_count_30d=1_000_000)

    ordered = apply_priority_order([medium_priority_hot, high_priority_cold])

    assert ordered[0].id == "a"


def test_apply_priority_order_ranks_by_query_weight_within_tier() -> None:
    cold = _table("cold", compaction_priority="MEDIUM", query_count_30d=0)
    hot = _table("hot", compaction_priority="MEDIUM", query_count_30d=50_000)
    warm = _table("warm", compaction_priority="MEDIUM", query_count_30d=500)

    ordered = apply_priority_order([cold, warm, hot])

    assert [t.id for t in ordered] == ["hot", "warm", "cold"]


def test_apply_priority_order_savings_breaks_ties_when_query_count_equal() -> None:
    cheap = _table(
        "cheap", compaction_priority="LOW", query_count_30d=10, projected_savings_usd_month=1.0
    )
    expensive = _table(
        "expensive",
        compaction_priority="LOW",
        query_count_30d=10,
        projected_savings_usd_month=1000.0,
    )

    ordered = apply_priority_order([cheap, expensive])

    assert ordered[0].id == "expensive"


def test_apply_priority_order_falls_back_to_small_files_when_query_weight_ties() -> None:
    """Zero-query tables (this session's real Wave-1 cohort shape) must still break ties the
    old way -- small_files_count -- rather than collapsing to arbitrary/schema-name order."""
    fragmented = _table("frag", compaction_priority="LOW", small_files_count=500)
    tidy = _table("tidy", compaction_priority="LOW", small_files_count=5)

    ordered = apply_priority_order([tidy, fragmented])

    assert ordered[0].id == "frag"


def test_apply_priority_order_is_deterministic_for_fully_tied_tables() -> None:
    a = _table("zzz", compaction_priority="LOW")
    b = _table("aaa", compaction_priority="LOW")

    ordered = apply_priority_order([a, b])

    assert [t.id for t in ordered] == ["aaa", "zzz"]
