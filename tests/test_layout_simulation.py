"""Phase 2 what-if optimizer: curve encoding, simulation, candidate generation, gating.

Everything here is shadow-only. No test asserts that a simulated benefit is realized -- that
claim requires a canary (Phase 3). See docs/automatic-clustering-column-selection.md §5.3, §6.3.
"""

from __future__ import annotations

import random

import pytest

from cryo.optimizer.baseline_replay import Predicate, ReplayQuery
from cryo.optimizer.candidate_generation import (
    MAX_CANDIDATES,
    ColumnIneligibility,
    ColumnSignal,
    generate_candidates,
    screen_columns,
)
from cryo.optimizer.clustering_advisor import AdviceOutcome, advise
from cryo.optimizer.curve_encoding import (
    NULL_COORDINATE,
    hilbert_key,
    quantile_boundaries,
    rank_normalise,
    zorder_key,
)
from cryo.optimizer.layout_simulation import (
    LayoutCandidate,
    simulate_candidate,
    simulate_candidates,
)

# --------------------------------------------------------------------------------------
# Curve encoding
# --------------------------------------------------------------------------------------


def test_hilbert_is_a_bijection_over_the_grid() -> None:
    bits = 4
    keys = {hilbert_key([x, y], bits=bits) for x in range(16) for y in range(16)}
    assert len(keys) == 256


def test_hilbert_steps_are_always_to_an_adjacent_cell() -> None:
    """The defining property of the Hilbert curve, and the reason it beats Z-order.

    Consecutive curve indices must be spatial neighbours -- no jumps. This is what produces
    the ~24-47% fewer contiguous curve runs reported by Moon et al. (TKDE 2001); note that
    curve runs are a locality proxy, not files pruned.
    """
    bits = 4
    cells = {hilbert_key([x, y], bits=bits): (x, y) for x in range(16) for y in range(16)}
    ordered = [cells[k] for k in sorted(cells)]
    for (x1, y1), (x2, y2) in zip(ordered, ordered[1:], strict=False):
        assert abs(x1 - x2) + abs(y1 - y2) == 1


def test_zorder_has_jumps_that_hilbert_does_not() -> None:
    """Contrast test: Z-order's quadrant transitions are exactly its weakness."""
    bits = 4
    cells = {zorder_key([x, y], bits=bits): (x, y) for x in range(16) for y in range(16)}
    ordered = [cells[k] for k in sorted(cells)]
    jumps = sum(
        1
        for (x1, y1), (x2, y2) in zip(ordered, ordered[1:], strict=False)
        if abs(x1 - x2) + abs(y1 - y2) != 1
    )
    assert jumps > 0


def test_zorder_interleaves_bits() -> None:
    # x=0b01, y=0b10 over 2 bits -> interleaved msb-first: x1 y1 x0 y0 = 0 1 1 0
    assert zorder_key([0b01, 0b10], bits=2) == 0b0110


def test_hilbert_is_deterministic() -> None:
    assert hilbert_key([13, 7], bits=8) == hilbert_key([13, 7], bits=8)


def test_rank_normalise_flattens_a_skewed_column() -> None:
    """The transform that matters more than curve choice (research note §4, §8).

    99% of values crammed into a tiny raw range must spread across the coordinate space,
    otherwise the curve cannot separate the dense region at all.
    """
    values = [1] * 990 + list(range(1000, 1010))
    boundaries = quantile_boundaries(values, 256)
    coords = {rank_normalise(v, boundaries) for v in set(values)}
    assert len(coords) > 1  # the dense value and the tail land in different buckets


def test_null_gets_a_reserved_coordinate() -> None:
    boundaries = quantile_boundaries([1, 2, 3, 4, 5], 4)
    assert rank_normalise(None, boundaries) == NULL_COORDINATE
    assert rank_normalise(3, boundaries) != NULL_COORDINATE


def test_rank_normalise_without_boundaries_collapses_to_one_coordinate() -> None:
    """No distribution info must simulate 'this dimension separates nothing', not crash."""
    assert rank_normalise(5, []) == rank_normalise(9999, [])


def test_quantile_boundaries_are_deterministic_and_ascending() -> None:
    values = list(range(1000))
    first = quantile_boundaries(values, 16)
    assert first == quantile_boundaries(values, 16)
    assert first == sorted(first)


def test_quantile_boundaries_collapse_heavy_hitter_ties() -> None:
    boundaries = quantile_boundaries([7] * 500, 16)
    assert len(boundaries) <= 1


def test_unorderable_values_do_not_crash_boundary_derivation() -> None:
    assert quantile_boundaries([1, "a", None, 2], 8) == []


# --------------------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------------------


def _correlated_rows(n: int = 2000, seed: int = 11) -> list[dict]:
    rng = random.Random(seed)
    return [
        {
            "user_id": rng.randint(0, 499),
            "ts": rng.randint(0, 100_000),
            "noise": rng.randint(0, 50),
        }
        for _ in range(n)
    ]


def _two_column_queries(n: int = 24, seed: int = 12) -> list[ReplayQuery]:
    rng = random.Random(seed)
    queries = []
    for _ in range(n):
        user = rng.randint(0, 499)
        start = rng.randint(0, 90_000)
        queries.append(
            ReplayQuery(
                predicates=(
                    Predicate("user_id", user, user),
                    Predicate("ts", start, start + 5_000),
                ),
                frequency=1,
            )
        )
    return queries


def test_clustering_beats_an_unsorted_layout() -> None:
    rows = _correlated_rows()
    queries = _two_column_queries()
    clustered = simulate_candidate(
        LayoutCandidate("zorder", ("user_id", "ts")),
        rows=rows,
        queries=queries,
        target_files=100,
    )
    # A layout on a column nothing filters is the control for "no useful clustering".
    unclustered = simulate_candidate(
        LayoutCandidate("sort", ("noise",)),
        rows=rows,
        queries=queries,
        target_files=100,
    )
    assert clustered.mean_files_touched < unclustered.mean_files_touched


def test_rank_normalisation_outperforms_raw_coordinates() -> None:
    """Skew treatment is the larger effect; the research review says so and simulation agrees."""
    rows = _correlated_rows()
    queries = _two_column_queries()
    normalised = simulate_candidate(
        LayoutCandidate("zorder", ("user_id", "ts"), rank_normalised=True),
        rows=rows,
        queries=queries,
        target_files=100,
    )
    raw = simulate_candidate(
        LayoutCandidate("zorder", ("user_id", "ts"), rank_normalised=False),
        rows=rows,
        queries=queries,
        target_files=100,
    )
    assert normalised.mean_files_touched < raw.mean_files_touched


def test_adding_an_irrelevant_dimension_hurts() -> None:
    """Databricks documents Z-order effectiveness falling with every added column."""
    rows = _correlated_rows()
    queries = _two_column_queries()
    two = simulate_candidate(
        LayoutCandidate("zorder", ("user_id", "ts")),
        rows=rows,
        queries=queries,
        target_files=100,
    )
    three = simulate_candidate(
        LayoutCandidate("zorder", ("noise", "ts", "user_id")),
        rows=rows,
        queries=queries,
        target_files=100,
    )
    assert three.mean_files_touched > two.mean_files_touched


def test_simulation_is_deterministic_for_a_fixed_seed() -> None:
    rows = _correlated_rows()
    queries = _two_column_queries()
    kwargs = {"rows": rows, "queries": queries, "target_files": 50}
    a = simulate_candidate(LayoutCandidate("hilbert", ("user_id", "ts")), **kwargs)
    b = simulate_candidate(LayoutCandidate("hilbert", ("user_id", "ts")), **kwargs)
    assert a.mean_files_touched == b.mean_files_touched
    assert a.conservative_files_touched == b.conservative_files_touched


def test_conservative_bound_is_never_more_optimistic_than_the_mean() -> None:
    """Ranking uses the conservative bound, so it must be the pessimistic end."""
    result = simulate_candidate(
        LayoutCandidate("zorder", ("user_id", "ts")),
        rows=_correlated_rows(),
        queries=_two_column_queries(),
        target_files=100,
    )
    assert result.conservative_files_touched >= result.mean_files_touched


def test_results_are_always_flagged_as_predictions() -> None:
    result = simulate_candidate(
        LayoutCandidate("sort", ("user_id",)),
        rows=_correlated_rows(),
        queries=_two_column_queries(),
        target_files=20,
    )
    assert result.is_prediction is True


def test_ranking_uses_the_conservative_bound() -> None:
    report = simulate_candidates(
        [
            LayoutCandidate("sort", ("user_id",)),
            LayoutCandidate("sort", ("noise",)),
            LayoutCandidate("zorder", ("user_id", "ts")),
        ],
        rows=_correlated_rows(),
        queries=_two_column_queries(),
        baseline_files=100.0,
        target_files=100,
    )
    ranked = report.ranked()
    reductions = [r.conservative_file_reduction for r in ranked]
    assert reductions == sorted(reductions, reverse=True)
    assert ranked[0].candidate.columns != ("noise",)  # the useless layout must not win


def test_simulation_without_rows_or_queries_returns_empty() -> None:
    assert (
        simulate_candidates(
            [LayoutCandidate("sort", ("a",))],
            rows=[],
            queries=_two_column_queries(),
            baseline_files=10.0,
            target_files=5,
        ).results
        == []
    )


# --------------------------------------------------------------------------------------
# Candidate generation
# --------------------------------------------------------------------------------------


def test_screening_rejects_columns_that_cannot_prune() -> None:
    screened = screen_columns(
        [
            ColumnSignal("ok", filter_frequency=0.5, avg_selectivity=0.1, ndv_ratio=0.4),
            ColumnSignal("flag", filter_frequency=0.9, ndv_ratio=0.00001),
            ColumnSignal("blob", filter_frequency=0.5, has_bounds=False),
            ColumnSignal("nulls", filter_frequency=0.5, null_fraction=0.99, ndv_ratio=0.4),
            ColumnSignal("udf", filter_frequency=0.5, sargable_fraction=0.1, ndv_ratio=0.4),
            ColumnSignal("weird", filter_frequency=0.5, orderable=False),
        ]
    )
    assert [c.name for c in screened.eligible] == ["ok"]
    assert screened.rejected["flag"] is ColumnIneligibility.NEAR_CONSTANT
    assert screened.rejected["blob"] is ColumnIneligibility.NO_BOUNDS
    assert screened.rejected["nulls"] is ColumnIneligibility.NULL_HEAVY
    assert screened.rejected["udf"] is ColumnIneligibility.NOT_SARGABLE
    assert screened.rejected["weird"] is ColumnIneligibility.UNORDERABLE


def test_near_unique_column_is_kept_when_the_workload_is_point_lookups() -> None:
    """A high-cardinality id is a great sort key for equality and a poor one for ranges."""
    point = screen_columns(
        [ColumnSignal("id", filter_frequency=0.9, ndv_ratio=0.99, equality_fraction=0.95)]
    )
    assert [c.name for c in point.eligible] == ["id"]
    ranged = screen_columns(
        [ColumnSignal("id", filter_frequency=0.9, ndv_ratio=0.99, equality_fraction=0.05)]
    )
    assert ranged.rejected["id"] is ColumnIneligibility.NEAR_UNIQUE


def test_univariate_value_prefers_selective_over_merely_frequent() -> None:
    frequent = ColumnSignal("f", filter_frequency=1.0, avg_selectivity=0.9)
    selective = ColumnSignal("s", filter_frequency=0.5, avg_selectivity=0.01)
    assert selective.univariate_value > frequent.univariate_value


def test_unknown_selectivity_is_treated_as_no_information() -> None:
    assert ColumnSignal("x", filter_frequency=1.0).univariate_value == pytest.approx(0.5)


def test_current_layout_is_always_a_candidate() -> None:
    """Simulation must be able to conclude that doing nothing wins."""
    candidates = generate_candidates([ColumnSignal("a", filter_frequency=0.9)])
    assert any(c.strategy == "current" for c in candidates)


def test_multicolumn_candidates_require_cooccurring_predicates() -> None:
    """A curve over columns no query filters together helps nobody."""
    signals = [
        ColumnSignal("a", filter_frequency=0.9, avg_selectivity=0.1, ndv_ratio=0.5),
        ColumnSignal("b", filter_frequency=0.8, avg_selectivity=0.1, ndv_ratio=0.5),
    ]
    without = generate_candidates(signals, cooccurrence={})
    assert all(len(c.columns) <= 1 for c in without)
    with_co = generate_candidates(signals, cooccurrence={("a", "b"): 50.0})
    assert any(c.strategy == "zorder" and len(c.columns) == 2 for c in with_co)


def test_correlated_columns_are_not_paired() -> None:
    """Two columns that move together contribute one dimension of pruning for two dimensions."""
    signals = [
        ColumnSignal("a", filter_frequency=0.9, avg_selectivity=0.1, ndv_ratio=0.5),
        ColumnSignal("a_copy", filter_frequency=0.9, avg_selectivity=0.1, ndv_ratio=0.5),
    ]
    candidates = generate_candidates(
        signals,
        cooccurrence={("a", "a_copy"): 100.0},
        correlations={("a", "a_copy"): 0.99},
    )
    assert all(len(c.columns) <= 1 for c in candidates)


def test_hilbert_candidates_appear_only_when_the_engine_supports_it() -> None:
    signals = [
        ColumnSignal("a", filter_frequency=0.9, avg_selectivity=0.1, ndv_ratio=0.5),
        ColumnSignal("b", filter_frequency=0.8, avg_selectivity=0.1, ndv_ratio=0.5),
    ]
    co = {("a", "b"): 50.0}
    assert not any(
        c.strategy == "hilbert"
        for c in generate_candidates(signals, cooccurrence=co, hilbert_available=False)
    )
    assert any(
        c.strategy == "hilbert"
        for c in generate_candidates(signals, cooccurrence=co, hilbert_available=True)
    )


def test_candidate_count_stays_bounded() -> None:
    """Bounded beam, not a powerset: the search must not explode with column count."""
    signals = [
        ColumnSignal(f"c{i}", filter_frequency=0.9, avg_selectivity=0.05, ndv_ratio=0.5)
        for i in range(12)
    ]
    co = {(f"c{i}", f"c{j}"): 10.0 for i in range(12) for j in range(i + 1, 12)}
    candidates = generate_candidates(signals, cooccurrence=co, hilbert_available=True)
    assert len(candidates) <= MAX_CANDIDATES
    assert all(len(c.columns) <= 4 for c in candidates)


# --------------------------------------------------------------------------------------
# Advisor gating
# --------------------------------------------------------------------------------------


def _files(n: int = 40) -> list[dict]:
    return [
        {
            "lower_bounds": {"user_id": i * 10, "ts": i * 1000},
            "upper_bounds": {"user_id": i * 10 + 9, "ts": i * 1000 + 999},
            "file_size_bytes": 1_000_000,
        }
        for i in range(n)
    ]


def _signals() -> list[ColumnSignal]:
    return [
        ColumnSignal("user_id", filter_frequency=0.9, avg_selectivity=0.02, ndv_ratio=0.4),
        ColumnSignal("ts", filter_frequency=0.8, avg_selectivity=0.05, ndv_ratio=0.6),
    ]


def test_advisor_abstains_without_enough_observations() -> None:
    advice = advise(
        table_ref="db.t",
        queries=[ReplayQuery(predicates=(Predicate("user_id", 1, 2),), frequency=3)],
        files=_files(),
        row_sample=_correlated_rows(200),
        column_signals=_signals(),
    )
    assert advice.outcome is AdviceOutcome.ABSTAIN_NO_BASELINE
    assert advice.recommended is False


def test_advisor_abstains_without_a_row_sample() -> None:
    """Curves have no closed-form predictor; without joint rows there is nothing to simulate."""
    advice = advise(
        table_ref="db.t",
        queries=_two_column_queries(300),
        files=_files(),
        row_sample=[],
        column_signals=_signals(),
        min_observations=10,
    )
    assert advice.outcome is AdviceOutcome.ABSTAIN_NO_SAMPLE


def test_advisor_abstains_when_every_column_is_screened_out() -> None:
    advice = advise(
        table_ref="db.t",
        queries=_two_column_queries(300),
        files=_files(),
        row_sample=_correlated_rows(500),
        column_signals=[ColumnSignal("flag", filter_frequency=0.9, ndv_ratio=0.000001)],
        min_observations=10,
    )
    assert advice.outcome is AdviceOutcome.ABSTAIN_NO_ELIGIBLE_COLUMNS
    assert advice.rejected_columns["flag"] == "near_constant"


def test_advisor_abstains_below_the_benefit_floor() -> None:
    advice = advise(
        table_ref="db.t",
        queries=_two_column_queries(300),
        files=_files(),
        row_sample=_correlated_rows(500),
        column_signals=_signals(),
        cooccurrence={("user_id", "ts"): 100.0},
        min_observations=10,
        min_bytes_reduction=0.999,  # unreachable
    )
    assert advice.outcome is AdviceOutcome.ABSTAIN_BELOW_BENEFIT_THRESHOLD


def test_advisor_abstains_when_payback_is_too_long() -> None:
    advice = advise(
        table_ref="db.t",
        queries=_two_column_queries(300),
        files=_files(),
        row_sample=_correlated_rows(500),
        column_signals=_signals(),
        cooccurrence={("user_id", "ts"): 100.0},
        min_observations=10,
        table_size_bytes=10 * 1024**3,
        rewrite_cost_per_gb=1000.0,  # absurdly expensive rewrite
        max_payback_days=30,
    )
    assert advice.outcome is AdviceOutcome.ABSTAIN_PAYBACK_TOO_LONG


def test_advisor_recommends_and_always_requires_a_canary() -> None:
    advice = advise(
        table_ref="db.t",
        queries=_two_column_queries(300),
        files=_files(),
        row_sample=_correlated_rows(800),
        column_signals=_signals(),
        cooccurrence={("user_id", "ts"): 100.0},
        min_observations=10,
        table_size_bytes=100 * 1024**3,
    )
    assert advice.outcome is AdviceOutcome.RECOMMEND
    assert advice.winner is not None
    assert advice.requires_canary is True
    assert "canary" in advice.detail


def test_evidence_summary_never_claims_measurement() -> None:
    """A prediction must be labelled as one everywhere it surfaces."""
    advice = advise(
        table_ref="db.t",
        queries=_two_column_queries(300),
        files=_files(),
        row_sample=_correlated_rows(800),
        column_signals=_signals(),
        cooccurrence={("user_id", "ts"): 100.0},
        min_observations=10,
        table_size_bytes=100 * 1024**3,
    )
    summary = advice.evidence_summary()
    assert summary["is_prediction"] is True
    assert summary["requires_canary"] is True
    assert summary["winner"] is not None
    assert summary["parser_coverage"] is not None
