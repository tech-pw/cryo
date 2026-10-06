"""Collecting clustering-advisor inputs from stored telemetry.

Phase 2 built the reasoning against hand-made fixtures; this is the plumbing that feeds it real
query logs and query patterns. See docs/automatic-clustering-column-selection.md §6.1.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from cryo.db.models import QueryLog, QueryPattern, Source, Table
from cryo.optimizer.advisor_inputs import (
    MIN_ROW_SAMPLE,
    build_column_signals,
    build_cooccurrence,
    build_replay_queries,
    build_row_sample,
    collect_workload_signals,
    quantile_boundaries_from_sketch,
)
from cryo.optimizer.baseline_replay import Predicate, ReplayQuery
from cryo.scanner.sketches import KLLSketch


def _seed_table(session) -> Table:
    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4()}", type="hive", config={})
    session.add(source)
    session.flush()
    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="s",
        table_name="t",
        is_active=True,
    )
    session.add(table)
    session.commit()
    return table


def _log(session, table: Table, sql: str, *, n: int = 1) -> None:
    for _ in range(n):
        session.add(
            QueryLog(
                source_id=table.source_id,
                table_id=table.id,
                query_id=str(uuid.uuid4()),
                engine="trino",
                query_text=sql,
                query_type="SELECT",
                executed_at=datetime.now(UTC),
            )
        )
    session.commit()


# --- replay queries ---------------------------------------------------------------------


def test_range_predicates_become_replayable_bounds(session) -> None:
    table = _seed_table(session)
    _log(session, table, "SELECT * FROM s.t WHERE ts BETWEEN 10 AND 20")
    queries, coverage = build_replay_queries(session, table.id)
    assert len(queries) == 1
    (predicate,) = queries[0].predicates
    assert (predicate.column, predicate.lower, predicate.upper) == ("ts", 10, 20)
    assert coverage == pytest.approx(1.0)


def test_identical_queries_collapse_into_one_weighted_entry(session) -> None:
    """A query run 5 times must weigh 5x, not appear 5 times."""
    table = _seed_table(session)
    _log(session, table, "SELECT * FROM s.t WHERE ts BETWEEN 1 AND 9", n=5)
    queries, _ = build_replay_queries(session, table.id)
    assert len(queries) == 1
    assert queries[0].frequency == 5


def test_unparseable_predicates_lower_coverage_without_faking_bounds(session) -> None:
    """A UDF-wrapped predicate cannot prune; it must reduce coverage, not invent a bound."""
    table = _seed_table(session)
    _log(session, table, "SELECT * FROM s.t WHERE lower(name) = 'x'")
    queries, coverage = build_replay_queries(session, table.id)
    assert coverage == pytest.approx(0.0)
    assert queries and queries[0].predicates == ()
    assert queries[0].unparseable_columns == ("name",)


def test_malformed_sql_does_not_stop_collection(session) -> None:
    table = _seed_table(session)
    _log(session, table, "this is not sql at all ((")
    _log(session, table, "SELECT * FROM s.t WHERE ts BETWEEN 1 AND 9")
    queries, _ = build_replay_queries(session, table.id)
    assert any(q.predicates for q in queries)


def test_no_query_text_yields_no_queries(session) -> None:
    table = _seed_table(session)
    assert build_replay_queries(session, table.id) == ([], 0.0)


# --- co-occurrence ----------------------------------------------------------------------


def test_cooccurrence_counts_pairs_filtered_together() -> None:
    queries = [
        ReplayQuery(predicates=(Predicate("a", 1, 2), Predicate("b", 1, 2)), frequency=3),
        ReplayQuery(predicates=(Predicate("a", 1, 2),), frequency=10),
    ]
    weights = build_cooccurrence(queries)
    assert weights == {("a", "b"): 3.0}  # the solo query contributes no pair


def test_cooccurrence_keys_are_order_independent() -> None:
    weights = build_cooccurrence(
        [ReplayQuery(predicates=(Predicate("z", 1, 2), Predicate("a", 1, 2)))]
    )
    assert ("a", "z") in weights


def test_three_column_query_produces_all_pairs() -> None:
    weights = build_cooccurrence(
        [ReplayQuery(predicates=(Predicate("a", 1, 2), Predicate("b", 1, 2), Predicate("c", 1, 2)))]
    )
    assert set(weights) == {("a", "b"), ("a", "c"), ("b", "c")}


# --- column signals ---------------------------------------------------------------------


def _pattern(session, table: Table, **kwargs) -> None:
    session.add(QueryPattern(table_id=table.id, **kwargs))
    session.commit()


def test_column_signals_derive_sargable_and_equality_fractions(session) -> None:
    table = _seed_table(session)
    _pattern(
        session,
        table,
        column_name="ts",
        filter_count=10,
        equality_filter_count=2,
        range_filter_count=6,
        avg_selectivity=0.05,
    )
    (signal,) = build_column_signals(session, table.id)
    assert signal.name == "ts"
    assert signal.avg_selectivity == pytest.approx(0.05)
    # 8 of 10 filters were classified as equality or range.
    assert signal.sargable_fraction == pytest.approx(0.8)
    # 2 of those 8 were equality.
    assert signal.equality_fraction == pytest.approx(0.25)


def test_columns_never_filtered_are_excluded(session) -> None:
    """Projected or joined columns cannot drive file pruning."""
    table = _seed_table(session)
    _pattern(session, table, column_name="proj", filter_count=0, projection_count=50)
    assert build_column_signals(session, table.id) == []


def test_missing_statistics_stay_unknown_rather_than_guessed(session) -> None:
    """A guessed NDV could screen out a good column; unknown simply does not fire the rule."""
    table = _seed_table(session)
    _pattern(session, table, column_name="a", filter_count=5)
    (signal,) = build_column_signals(session, table.id)
    assert signal.ndv_ratio is None
    assert signal.null_fraction == 0.0


def test_supplied_statistics_are_attached(session) -> None:
    table = _seed_table(session)
    _pattern(session, table, column_name="a", filter_count=5)
    (signal,) = build_column_signals(
        session,
        table.id,
        ndv_ratios={"a": 0.42},
        null_fractions={"a": 0.1},
        columns_with_bounds=["a"],
    )
    assert signal.ndv_ratio == pytest.approx(0.42)
    assert signal.null_fraction == pytest.approx(0.1)
    assert signal.has_bounds is True


def test_column_without_file_bounds_is_marked(session) -> None:
    table = _seed_table(session)
    _pattern(session, table, column_name="a", filter_count=5)
    (signal,) = build_column_signals(session, table.id, columns_with_bounds=["other"])
    assert signal.has_bounds is False


# --- row sample -------------------------------------------------------------------------


def test_row_sample_below_the_floor_is_rejected() -> None:
    """Too few rows makes every synthetic file bound a point, overstating pruning."""
    assert build_row_sample([{"a": 1}] * 10, ["a"]) == []


def test_row_sample_is_projected_to_candidate_columns() -> None:
    rows = [{"a": i, "b": i, "unused": i} for i in range(MIN_ROW_SAMPLE)]
    sample = build_row_sample(rows, ["a", "b"])
    assert len(sample) == MIN_ROW_SAMPLE
    assert set(sample[0]) == {"a", "b"}


def test_row_sample_preserves_joint_values() -> None:
    """Joint rows are the point: per-column sketches cannot represent correlation."""
    rows = [{"a": i, "b": i * 2} for i in range(MIN_ROW_SAMPLE)]
    sample = build_row_sample(rows, ["a", "b"])
    assert all(row["b"] == row["a"] * 2 for row in sample)


def test_missing_column_becomes_none_not_an_error() -> None:
    rows = [{"a": i} for i in range(MIN_ROW_SAMPLE)]
    sample = build_row_sample(rows, ["a", "absent"])
    assert sample[0]["absent"] is None


# --- sketch boundaries ------------------------------------------------------------------


def test_quantile_boundaries_from_sketch_are_ascending() -> None:
    sketch = KLLSketch()
    sketch.update_many(range(1000))
    boundaries = quantile_boundaries_from_sketch(sketch, buckets=16)
    assert boundaries == sorted(boundaries)
    assert len(boundaries) > 1


def test_empty_sketch_yields_no_boundaries() -> None:
    assert quantile_boundaries_from_sketch(KLLSketch()) == []
    assert quantile_boundaries_from_sketch(None) == []


# --- end to end -------------------------------------------------------------------------


def test_collect_workload_signals_assembles_every_input(session) -> None:
    table = _seed_table(session)
    _log(session, table, "SELECT * FROM s.t WHERE ts BETWEEN 1 AND 9 AND uid BETWEEN 2 AND 2", n=4)
    _pattern(
        session, table, column_name="ts", filter_count=4, range_filter_count=4, avg_selectivity=0.1
    )
    _pattern(
        session,
        table,
        column_name="uid",
        filter_count=4,
        equality_filter_count=4,
        avg_selectivity=0.01,
    )

    signals = collect_workload_signals(session, table.id)
    assert signals.observations == 4
    assert signals.parser_coverage == pytest.approx(1.0)
    assert {s.name for s in signals.column_signals} == {"ts", "uid"}
    assert signals.cooccurrence.get(("ts", "uid")) == 4.0
