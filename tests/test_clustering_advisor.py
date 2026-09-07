"""Phase 0+1 clustering advisor: parser fidelity, baseline replay, curve gating, codec value.

Scope note: these cover the *evidence* layer only. Candidate simulation (Phase 2) and canary
validation (Phase 3) are not implemented, so nothing here asserts that a recommended layout
delivers a predicted saving -- see docs/automatic-clustering-column-selection.md §8.
"""

from __future__ import annotations

import pytest

from cryo.engines.spark.sql import render_clustering_operation
from cryo.optimizer.baseline_replay import (
    AbstainReason,
    Predicate,
    ReplayQuery,
    build_baseline,
    replay_query,
)
from cryo.optimizer.codec_advisor import (
    CodecAbstainReason,
    CodecAction,
    estimate_codec_change,
    sample_plan,
)
from cryo.optimizer.curve_capability import (
    CurveStrategy,
    CurveUnsupportedReason,
    parse_version,
    resolve_curve_strategy,
)
from cryo.telemetry.parser import parse_query_predicates

# --------------------------------------------------------------------------------------
# Parser: predicate operators must survive
# --------------------------------------------------------------------------------------


def _by_column(sql: str) -> dict[str, list]:
    result: dict[str, list] = {}
    for col in parse_query_predicates(sql).filter_columns:
        result.setdefault(col.column, []).append(col)
    return result


def test_range_predicate_is_not_recorded_as_equality() -> None:
    """Regression: the sqlglot path stamped is_equality=True on every WHERE column.

    A range scan and a point lookup imply different layouts, so conflating them silently
    corrupts every downstream clustering decision.
    """
    cols = _by_column("SELECT * FROM t WHERE ts > 100")
    (ts,) = cols["ts"]
    assert ts.operator == ">"
    assert ts.is_range is True
    assert ts.is_equality is False


def test_equality_and_range_are_distinguished_in_one_query() -> None:
    cols = _by_column("SELECT * FROM t WHERE ts >= 100 AND user_id = 5")
    assert cols["user_id"][0].is_equality is True
    assert cols["user_id"][0].is_range is False
    assert cols["ts"][0].is_range is True


def test_between_is_a_range() -> None:
    (ts,) = _by_column("SELECT * FROM t WHERE ts BETWEEN 1 AND 9")["ts"]
    assert ts.operator == "BETWEEN"
    assert ts.is_range is True


def test_in_list_is_equality() -> None:
    (region,) = _by_column("SELECT * FROM t WHERE region IN ('a', 'b')")["region"]
    assert region.operator == "IN"
    assert region.is_equality is True


def test_unclassifiable_predicate_is_recorded_without_faking_equality() -> None:
    """A UDF-wrapped column cannot drive min/max skipping and must not claim it can."""
    (name,) = _by_column("SELECT * FROM t WHERE lower(name) = 'x'")["name"]
    assert name.operator is None
    assert name.is_equality is False
    assert name.is_range is False


def test_same_column_under_two_predicates_is_recorded_twice() -> None:
    """``a > 1 AND a < 9`` is two observations of one column, not one."""
    assert len(_by_column("SELECT * FROM t WHERE a > 1 AND a < 9")["a"]) == 2


# --------------------------------------------------------------------------------------
# Baseline replay
# --------------------------------------------------------------------------------------


def _file(low: object, high: object, size: int = 1000, column: str = "ts") -> dict:
    return {
        "lower_bounds": {column: low},
        "upper_bounds": {column: high},
        "file_size_bytes": size,
    }


def test_replay_prunes_non_overlapping_files() -> None:
    files = [_file(0, 10), _file(20, 30), _file(40, 50)]
    result = replay_query(ReplayQuery(predicates=(Predicate("ts", lower=21, upper=29),)), files)
    assert result.files_touched == 1
    assert result.bytes_touched == 1000
    assert result.file_prune_ratio == pytest.approx(2 / 3)


def test_replay_touches_every_file_when_predicate_spans_all() -> None:
    files = [_file(0, 10), _file(20, 30)]
    result = replay_query(ReplayQuery(predicates=(Predicate("ts", lower=0, upper=100),)), files)
    assert result.files_touched == 2


def test_replay_is_conjunctive_across_columns() -> None:
    """Two predicates must both be satisfiable for a file to be read."""
    files = [
        {"lower_bounds": {"a": 0, "b": 0}, "upper_bounds": {"a": 5, "b": 5}, "file_size_bytes": 1},
        {
            "lower_bounds": {"a": 0, "b": 90},
            "upper_bounds": {"a": 5, "b": 99},
            "file_size_bytes": 1,
        },
    ]
    query = ReplayQuery(predicates=(Predicate("a", 1, 2), Predicate("b", 1, 2)))
    assert replay_query(query, files).files_touched == 1


def test_missing_bounds_never_prune() -> None:
    """A file without bounds for the filtered column must be read, not skipped."""
    files = [{"lower_bounds": {}, "upper_bounds": {}, "file_size_bytes": 10}]
    result = replay_query(ReplayQuery(predicates=(Predicate("ts", 1, 2),)), files)
    assert result.files_touched == 1
    assert result.columns_without_bounds == ("ts",)


def test_incomparable_bound_types_do_not_prune() -> None:
    """A string bound against a numeric literal is undecidable; read the file."""
    files = [_file("2026-01-01", "2026-01-02")]
    assert replay_query(ReplayQuery(predicates=(Predicate("ts", 1, 2),)), files).files_touched == 1


def test_open_ended_range_prunes_one_side_only() -> None:
    files = [_file(0, 10), _file(20, 30)]
    result = replay_query(ReplayQuery(predicates=(Predicate("ts", lower=15),)), files)
    assert result.files_touched == 1


def test_build_baseline_abstains_without_files() -> None:
    report = build_baseline([ReplayQuery(predicates=(Predicate("ts", 1, 2),))], [])
    assert report.abstained is AbstainReason.NO_FILE_STATS


def test_build_baseline_abstains_without_parseable_predicates() -> None:
    report = build_baseline(
        [ReplayQuery(predicates=(), unparseable_columns=("name",))], [_file(0, 10)]
    )
    assert report.abstained is AbstainReason.NO_PARSEABLE_PREDICATES


def test_build_baseline_abstains_below_observation_floor() -> None:
    report = build_baseline(
        [ReplayQuery(predicates=(Predicate("ts", 1, 2),), frequency=5)],
        [_file(0, 10)],
        min_observations=200,
    )
    assert report.abstained is AbstainReason.INSUFFICIENT_OBSERVATIONS


def test_build_baseline_abstains_below_parser_coverage() -> None:
    report = build_baseline(
        [ReplayQuery(predicates=(Predicate("ts", 1, 2),), unparseable_columns=("a", "b", "c"))],
        [_file(0, 10)],
        min_parser_coverage=0.8,
    )
    assert report.abstained is AbstainReason.LOW_PARSER_COVERAGE
    assert report.parser_coverage == pytest.approx(0.25)


def test_build_baseline_weights_by_frequency() -> None:
    """A frequent query must dominate the weighted mean over a rare one."""
    files = [_file(0, 10), _file(20, 30), _file(40, 50)]
    report = build_baseline(
        [
            ReplayQuery(predicates=(Predicate("ts", 0, 5),), frequency=99),  # 1 file
            ReplayQuery(predicates=(Predicate("ts", 0, 100),), frequency=1),  # 3 files
        ],
        files,
    )
    assert report.abstained is None
    assert report.mean_files_touched == pytest.approx((99 * 1 + 1 * 3) / 100)
    assert report.mean_file_prune_ratio > 0.6


# --------------------------------------------------------------------------------------
# Hilbert capability gating
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.12.0", (1, 12, 0)),
        ("1.12.0-amzn-1", (1, 12, 0)),  # vendor-suffixed EMR build
        ("v4.1", (4, 1)),
        ("", None),
        (None, None),
        ("unknown", None),
    ],
)
def test_parse_version(raw: str | None, expected: tuple[int, ...] | None) -> None:
    assert parse_version(raw) == expected


def test_hilbert_allowed_on_supported_runtime() -> None:
    decision = resolve_curve_strategy(
        "hilbert", enabled=True, iceberg_version="1.12.0", spark_version="4.1.0"
    )
    assert decision.strategy is CurveStrategy.HILBERT
    assert decision.downgraded is False
    assert decision.detail() is None


@pytest.mark.parametrize(
    ("enabled", "iceberg", "spark", "reason"),
    [
        (False, "1.12.0", "4.1", CurveUnsupportedReason.DISABLED),
        (True, "1.11.9", "4.1", CurveUnsupportedReason.ICEBERG_TOO_OLD),
        (True, "1.12.0", "3.5.1", CurveUnsupportedReason.SPARK_TOO_OLD),
        (True, "1.12.0", "4.0.0", CurveUnsupportedReason.SPARK_TOO_OLD),
        (True, None, "4.1", CurveUnsupportedReason.UNKNOWN_ICEBERG_VERSION),
        (True, "1.12.0", None, CurveUnsupportedReason.UNKNOWN_SPARK_VERSION),
    ],
)
def test_hilbert_downgrades_to_zorder(
    enabled: bool, iceberg: str | None, spark: str | None, reason: CurveUnsupportedReason
) -> None:
    """An unsupported Hilbert request must degrade, not fail the job after submission."""
    decision = resolve_curve_strategy(
        "hilbert", enabled=enabled, iceberg_version=iceberg, spark_version=spark
    )
    assert decision.strategy is CurveStrategy.ZORDER
    assert decision.reason is reason
    assert "downgraded" in (decision.detail() or "")


def test_unknown_version_is_never_treated_as_new_enough() -> None:
    assert (
        resolve_curve_strategy(
            "hilbert", enabled=True, iceberg_version="garbage", spark_version="4.1"
        ).strategy
        is CurveStrategy.ZORDER
    )


def test_zorder_request_is_untouched_by_the_gate() -> None:
    decision = resolve_curve_strategy("zorder", enabled=False)
    assert decision.strategy is CurveStrategy.ZORDER
    assert decision.downgraded is False


# --------------------------------------------------------------------------------------
# Spark SQL rendering
# --------------------------------------------------------------------------------------


def test_hilbert_renders_as_sort_strategy_with_hilbert_sort_order() -> None:
    """Iceberg has no 'hilbert' *strategy*; it is strategy => 'sort' plus hilbert() ordering.

    Passing strategy => 'hilbert' fails at procedure-parse time.
    """
    sql = render_clustering_operation("ice", "db.t", "hilbert", ["a", "b"])
    assert "strategy => 'sort'" in sql
    assert "'sort-order', 'hilbert(`a`, `b`)'" in sql
    assert "strategy => 'hilbert'" not in sql


def test_zorder_rendering_is_unchanged() -> None:
    sql = render_clustering_operation("ice", "db.t", "zorder", ["a", "b"])
    assert "strategy => 'zorder'" in sql
    assert "'sort-order', 'zorder(`a`, `b`)'" in sql


# --------------------------------------------------------------------------------------
# Codec advisor
# --------------------------------------------------------------------------------------

_TB = 1024**4


def test_snappy_to_zstd_is_recommended_on_a_large_table() -> None:
    rec = estimate_codec_change(current_codec="snappy", table_size_bytes=_TB)
    assert rec.action is CodecAction.SWITCH_CODEC
    assert rec.recommended is True
    assert rec.expected_saving_ratio == pytest.approx(0.39)
    assert rec.is_estimate is True  # never presented as measured


def test_zstd_9_is_already_optimal() -> None:
    rec = estimate_codec_change(current_codec="zstd", current_level=9, table_size_bytes=_TB)
    assert rec.abstained is CodecAbstainReason.ALREADY_OPTIMAL
    assert rec.recommended is False


def test_small_absolute_saving_is_rejected() -> None:
    """5% of a small table is not worth a rewrite even though the ratio clears the bar."""
    rec = estimate_codec_change(current_codec="snappy", table_size_bytes=1024**3)
    assert rec.abstained is CodecAbstainReason.BELOW_ABSOLUTE_THRESHOLD


def test_brotli_saving_below_relative_threshold_is_rejected() -> None:
    rec = estimate_codec_change(current_codec="brotli", table_size_bytes=100 * _TB)
    assert rec.abstained is CodecAbstainReason.BELOW_RELATIVE_THRESHOLD


def test_expensive_rewrite_makes_net_value_negative() -> None:
    rec = estimate_codec_change(
        current_codec="snappy", table_size_bytes=_TB, rewrite_cost_per_gb=10.0
    )
    assert rec.abstained is CodecAbstainReason.NEGATIVE_NET_VALUE
    assert rec.net_value_usd < 0


def test_unknown_table_size_abstains() -> None:
    rec = estimate_codec_change(current_codec="snappy", table_size_bytes=None)
    assert rec.abstained is CodecAbstainReason.UNKNOWN_TABLE_SIZE


def test_zstd_level_gain_stays_in_the_single_digit_band() -> None:
    """Guards against the estimate drifting toward vendor-headline territory.

    An already-compacted zstd-3 table has ~3-12% cheap residual headroom; a codec-only
    recommendation must never imply the 20-50% a vendor quotes after also changing file size,
    row-group size, and sort order.
    """
    rec = estimate_codec_change(current_codec="zstd", current_level=3, table_size_bytes=_TB)
    assert 0.0 < rec.expected_saving_ratio <= 0.12


def test_sample_plan_is_bounded_and_includes_a_control() -> None:
    plan = sample_plan("snappy")
    assert len(plan.probes) <= 12
    assert plan.probes[0].codec == "snappy"  # control on the same sample
    assert any(p.level == 9 for p in plan.probes)
    assert not any(p.level == 19 for p in plan.probes)  # cold-archival only


def test_sample_plan_includes_level_19_only_for_cold_data() -> None:
    assert any(p.level == 19 for p in sample_plan("zstd", include_cold_archival=True).probes)
