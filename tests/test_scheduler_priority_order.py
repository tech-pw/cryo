"""Tests for cryo/scheduler/scheduler.py's apply_priority_order -- the Trino-usage/cost
weighting added 2026-08-19 (PDAE), which previously did not exist: cohort dispatch order fell
straight from compaction_priority to small_files_count, with zero awareness of how often a
table is actually queried or how much fixing it would save; the reclaimable-bytes weighting
added 2026-08-21 after a live investigation into safely draining a 57.95TB backlog without
overwhelming the shared Hive Metastore -- see _reclaimable_weight's own docstring for the full
incident/research writeup; and the combined-weight blend added 2026-08-25 after reclaimable_bytes
being the strict primary signal (with query_weight only breaking exact ties) left real, actively
queried tables starved behind cold ones with merely larger backlogs -- see
_combined_priority_weight's own docstring for the full incident.
"""

from __future__ import annotations

from cryo.scheduler.rules import TableCandidate
from cryo.scheduler.scheduler import (
    _combined_priority_weight,
    _query_weight,
    _reclaimable_weight,
    apply_priority_order,
)


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


def test_reclaimable_weight_zero_for_untouched_table() -> None:
    table = _table("clean")
    assert _reclaimable_weight(table) == 0.0


def test_reclaimable_weight_increases_with_reclaimable_bytes() -> None:
    small = _table("small", reclaimable_bytes=1024**3)  # 1 GB
    large = _table("large", reclaimable_bytes=1024**4)  # 1 TB
    assert _reclaimable_weight(large) > _reclaimable_weight(small)


def test_reclaimable_weight_log_scaled_not_linear() -> None:
    """A 4.5TB table (the real largest backlog table found live 2026-08-21) must not
    completely drown out a 50GB one -- log1p keeps the tail from dominating the way a hard
    linear scale would, same reasoning as _query_weight's own log-scaling."""
    huge = _table("huge", reclaimable_bytes=4_500 * 1024**3)  # 4.5 TB
    moderate = _table("moderate", reclaimable_bytes=50 * 1024**3)  # 50 GB
    # Linear would be a 90x gap; log-scaled must be far smaller.
    assert _reclaimable_weight(huge) / max(_reclaimable_weight(moderate), 1e-9) < 3.0


def test_reclaimable_weight_ignores_negative_values() -> None:
    """reclaimable_bytes has no business being negative, but a stray bad value must not push
    a table's weight below the true-zero baseline via log of a negative number."""
    table = _table("weird", reclaimable_bytes=-1024**3)
    assert _reclaimable_weight(table) == 0.0


def test_apply_priority_order_ranks_by_reclaimable_bytes_within_tier() -> None:
    """Direct regression test for the live finding this was built from: the 58TB backlog is
    extremely concentrated by a power-law curve (top 20 tables = 57% of total), so draining
    highest-reclaimable-first reaches most of the backlog's value in far fewer metastore
    round-trips than an unordered or usage-only sweep."""
    tiny = _table("tiny", compaction_priority="MEDIUM", reclaimable_bytes=1024**2)  # 1 MB
    huge = _table("huge", compaction_priority="MEDIUM", reclaimable_bytes=4_500 * 1024**3)
    moderate = _table("moderate", compaction_priority="MEDIUM", reclaimable_bytes=50 * 1024**3)

    ordered = apply_priority_order([tiny, moderate, huge])

    assert [t.id for t in ordered] == ["huge", "moderate", "tiny"]


def test_apply_priority_order_reclaimable_bytes_outranks_mildly_hot_table_within_tier() -> None:
    """reclaimable_bytes still dominates a merely-warm table -- the combined-weight blend
    (2026-08-25) lets real hotness offset backlog size, it doesn't invert the ranking outright
    for every hot table. A few hundred queries/30d is nowhere near enough to out-blend a
    2,000GB backlog."""
    large_cold = _table(
        "large_cold", compaction_priority="LOW", reclaimable_bytes=2_000 * 1024**3, query_count_30d=0
    )
    small_warm = _table(
        "small_warm", compaction_priority="LOW", reclaimable_bytes=1024**2, query_count_30d=500
    )

    ordered = apply_priority_order([small_warm, large_cold])

    assert ordered[0].id == "large_cold"


def test_apply_priority_order_extreme_hotness_can_outrank_a_large_backlog_within_tier() -> None:
    """Direct regression test for the combined-weight fix (2026-08-25): before this, a table's
    query hotness had literally zero pull against reclaimable size (query_weight was only a
    tie-breaker) -- confirmed live, lsq_pw.gold_dbt_lsq_pw_prospect_activity_phone_lookup
    (2,610 queries/30d, HIGH priority, real Metabase traffic) sat behind 146 other HIGH-tier
    tables purely on reclaimable-bytes size while its own small-file count climbed monotonically
    every day (1,431 -> 17,879 files, Aug 13-24) because the fix that would have controlled it
    never got a turn. An extremely hot table must now be able to outrank a merely-larger-backlog
    one, not just tie-break against an exactly-equal one."""
    large_cold = _table(
        "large_cold", compaction_priority="LOW", reclaimable_bytes=2_000 * 1024**3, query_count_30d=0
    )
    extremely_hot = _table(
        "extremely_hot",
        compaction_priority="LOW",
        reclaimable_bytes=1024**2,
        query_count_30d=1_000_000,
    )

    ordered = apply_priority_order([extremely_hot, large_cold])

    assert ordered[0].id == "extremely_hot"


def test_combined_priority_weight_is_sum_of_reclaimable_and_query_weight() -> None:
    table = _table("t", reclaimable_bytes=5 * 1024**3, query_count_30d=1000)
    assert _combined_priority_weight(table) == _reclaimable_weight(table) + _query_weight(table)


def test_apply_priority_order_hot_small_table_now_outranks_146x_bigger_cold_backlog() -> None:
    """Regression test using the exact real-world magnitudes confirmed live 2026-08-25: a table
    with a small reclaimable backlog but real query traffic must rank ahead of a pack of cold
    tables individually larger than it but collectively representing the kind of "146 other
    HIGH-tier tables" starvation this fix exists to prevent."""
    hot_small = _table(
        "hot_small",
        compaction_priority="HIGH",
        reclaimable_bytes=9_063_196_314,  # ~8.4GB, real lsq_pw value
        query_count_30d=2610,  # real lsq_pw value
    )
    cold_medium_tables = [
        _table(f"cold_{i}", compaction_priority="HIGH", reclaimable_bytes=15 * 1024**3, query_count_30d=0)
        for i in range(5)
    ]

    ordered = apply_priority_order([*cold_medium_tables, hot_small])

    assert ordered[0].id == "hot_small"


def test_apply_priority_order_query_weight_breaks_ties_when_reclaimable_bytes_equal() -> None:
    cold = _table(
        "cold", compaction_priority="LOW", reclaimable_bytes=1024**3, query_count_30d=0
    )
    hot = _table(
        "hot", compaction_priority="LOW", reclaimable_bytes=1024**3, query_count_30d=50_000
    )

    ordered = apply_priority_order([cold, hot])

    assert ordered[0].id == "hot"


def test_apply_priority_order_still_respects_compaction_priority_first_over_reclaimable_bytes() -> (
    None
):
    """An operator-set HIGH priority must still outrank a MEDIUM table sitting on a huge
    reclaimable backlog -- reclaimable-bytes weighting only breaks ties within a tier, same
    invariant query_weight already had to preserve."""
    high_priority_tiny = _table(
        "a", compaction_priority="HIGH", reclaimable_bytes=1024**2
    )
    medium_priority_huge = _table(
        "b", compaction_priority="MEDIUM", reclaimable_bytes=4_500 * 1024**3
    )

    ordered = apply_priority_order([medium_priority_huge, high_priority_tiny])

    assert ordered[0].id == "a"
