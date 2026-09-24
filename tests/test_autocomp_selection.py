"""Batch selection follows AutoComp (arXiv:2504.04186) §4.2-4.3: ΔF and GBHr traits, min-max
normalization over the pool, S_c = w1·T1' − w2·T2', greedy top-k under a GBHr budget."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from cryo.maintenance.selection import (
    AutoCompParams,
    Candidate,
    autocomp_trace,
    compute_cost_gbhr,
    fit_budget,
    min_max,
    rank_candidates,
)

GIB = 1024**3
PAPER_ONLY = AutoCompParams(heat_weight=0.0)


def _candidate(**overrides: Any) -> Candidate:
    base: dict[str, Any] = {
        "table_id": uuid.uuid4(),
        "schema_name": "s",
        "table_name": "t",
        "total_size_gb": 100.0,
        "total_files": 1000,
        "small_files": 0,
        "delete_file_pct": 0.0,
        "query_count_30d": 100,
        "projected_savings_usd_month": 0.0,
        "needs_compaction": True,
        "files_below_target": 0,
        "bytes_below_target": 0,
    }
    base.update(overrides)
    return Candidate(**base)


def test_file_count_reduction_counts_every_file_below_target_and_delete_files() -> None:
    mid_size = _candidate(small_files=0, files_below_target=1000, delete_files=40)
    unmeasured = _candidate(small_files=300, files_below_target=None, bytes_below_target=None)

    assert mid_size.file_count_reduction == 1040
    assert unmeasured.file_count_reduction == 300
    assert not unmeasured.measured_below_target


def test_compute_cost_is_executor_memory_times_rewrite_hours() -> None:
    table = _candidate(bytes_below_target=50 * GIB)

    gbhr = compute_cost_gbhr(table, AutoCompParams(executor_memory_gb=16, rewrite_gb_per_hour=25))

    assert gbhr == pytest.approx(16 * 50 / 25)


def test_min_max_matches_the_paper_and_a_uniform_trait_carries_no_signal() -> None:
    assert min_max([10.0, 20.0, 30.0]) == [0.0, 0.5, 1.0]
    assert min_max([7.0, 7.0]) == [0.0, 0.0]


def test_paper_score_is_the_weighted_difference_of_normalized_traits() -> None:
    low = _candidate(table_name="low", files_below_target=0, bytes_below_target=0)
    mid = _candidate(table_name="mid", files_below_target=50, bytes_below_target=10 * GIB)
    high = _candidate(table_name="high", files_below_target=100, bytes_below_target=20 * GIB)

    ranked = rank_candidates([low, mid, high], limit=10, params=PAPER_ONLY)

    scores = {r.candidate.table_name: r.s_c for r in ranked}
    assert scores == pytest.approx({"low": 0.0, "mid": 0.7 * 0.5 - 0.3 * 0.5, "high": 0.4})
    assert [r.candidate.table_name for r in ranked] == ["high", "mid", "low"]
    assert all(r.score == r.s_c for r in ranked)


def test_cheap_fragmentation_outranks_the_same_count_spread_over_a_huge_rewrite() -> None:
    cheap = _candidate(table_name="cheap", files_below_target=5000, bytes_below_target=2 * GIB)
    costly = _candidate(table_name="costly", files_below_target=5000, bytes_below_target=2000 * GIB)
    baseline = _candidate(table_name="baseline", files_below_target=10, bytes_below_target=0)

    ranked = rank_candidates([costly, baseline, cheap], limit=10, params=PAPER_ONLY)

    assert ranked[0].candidate.table_name == "cheap"


def test_heat_extension_blends_query_heat_into_the_paper_score() -> None:
    hot = _candidate(table_name="hot", query_count_30d=50_000, files_below_target=100)
    cold = _candidate(table_name="cold", query_count_30d=0, files_below_target=120)

    paper = rank_candidates([hot, cold], limit=10, params=PAPER_ONLY)
    extended = rank_candidates([hot, cold], limit=10, params=AutoCompParams(heat_weight=0.5))

    assert paper[0].candidate.table_name == "cold"
    assert extended[0].candidate.table_name == "hot"
    top = extended[0]
    assert top.score == pytest.approx(0.5 * top.s_c + 0.5 * top.heat)


def test_budget_is_greedy_and_skips_only_what_no_longer_fits() -> None:
    params = AutoCompParams(heat_weight=0.0, executor_memory_gb=1, rewrite_gb_per_hour=1)
    big = _candidate(table_name="big", files_below_target=1000, bytes_below_target=8 * GIB)
    medium = _candidate(table_name="medium", files_below_target=900, bytes_below_target=5 * GIB)
    small = _candidate(table_name="small", files_below_target=500, bytes_below_target=1 * GIB)
    ranked = rank_candidates([big, medium, small], limit=10, params=params)

    kept, over = fit_budget(ranked, budget_gbhr=10)

    kept_names = {r.candidate.table_name for r in kept}
    assert sum(r.gbhr for r in kept) <= 10
    assert "small" in kept_names
    assert len(kept) + len(over) == 3 and over
    assert fit_budget(ranked, None) == (ranked, [])


def test_one_table_never_costs_more_than_one_lane_and_is_marked_capped() -> None:
    params = AutoCompParams(executor_memory_gb=16, rewrite_gb_per_hour=25, max_table_gbhr=90)
    huge = _candidate(files_below_target=10**6, bytes_below_target=20_000 * GIB)

    (ranked,) = rank_candidates([huge], limit=1, params=params)

    assert ranked.gbhr == 90
    assert ranked.capped
    assert params.max_table_rewrite_gb == pytest.approx(90 * 25 / 16)


def test_trace_records_what_the_paper_only_ranking_would_have_run() -> None:
    params = AutoCompParams(heat_weight=0.9, executor_memory_gb=1, rewrite_gb_per_hour=1)
    hot = _candidate(
        table_name="hot", query_count_30d=90_000, files_below_target=10, bytes_below_target=GIB
    )
    fragmented = _candidate(
        table_name="fragmented",
        query_count_30d=0,
        files_below_target=10_000,
        bytes_below_target=GIB,
    )
    candidates = [hot, fragmented]
    chosen, _ = fit_budget(rank_candidates(candidates, limit=10, params=params), budget_gbhr=1.0)

    trace = autocomp_trace(candidates, chosen, limit=10, params=params, budget_gbhr=1.0)

    assert [r.candidate.table_name for r in chosen] == ["hot"]
    assert trace["overlap"] == 0 and trace["jaccard"] == 0.0
    assert trace["paper_only_missed"] == [str(fragmented.table_id)]
    assert trace["weights"] == {"w_file_reduction": 0.7, "w_compute_cost": 0.3, "heat_weight": 0.9}
