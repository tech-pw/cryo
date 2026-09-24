import pytest

from cryo.optimizer.wair import (
    BoundaryFile,
    FileBounds,
    QueryRange,
    WairCostParams,
    find_boundary_files,
    select_boundary_files,
)
from cryo.optimizer.wair.boundary import utilization

GIB = 1024**3


def test_boundary_detection_keeps_partial_overlaps_and_scores_eq3_savings() -> None:
    files = [
        FileBounds("lower.parquet", 1200, {"value": 0}, {"value": 12}),
        FileBounds("interior.parquet", 100, {"value": 12}, {"value": 18}),
        FileBounds("upper.parquet", 700, {"value": 18}, {"value": 25}),
        FileBounds("outside.parquet", 100, {"value": 30}, {"value": 40}),
    ]
    query = QueryRange("value", 10, 20, frequency=3, cpu_seconds_per_byte=1e-6)

    result = find_boundary_files(files, [query])

    assert [item.file_path for item in result] == ["lower.parquet", "upper.parquet"]
    # (1 - u) * size * t_read/size_read * frequency, with u = overlap / file range.
    assert result[0].savings_cpu_seconds == pytest.approx((1 - 2 / 12) * 1200 * 1e-6 * 3)
    assert result[1].savings_cpu_seconds == pytest.approx((1 - 2 / 7) * 700 * 1e-6 * 3)
    assert result[0].edges == {"value": (10,)}
    assert result[1].edges == {"value": (20,)}


def test_a_file_starting_at_the_upper_endpoint_is_a_boundary_file() -> None:
    files = [FileBounds("edge.parquet", 100, {"value": 20}, {"value": 30})]

    result = find_boundary_files(files, [QueryRange("value", 10, 20, cpu_seconds_per_byte=1.0)])

    assert [b.file_path for b in result] == ["edge.parquet"]
    assert result[0].savings_cpu_seconds == pytest.approx(100.0)


def test_open_ended_ranges_and_temporal_bounds_compare_on_one_axis() -> None:
    files = [
        FileBounds(
            "june.parquet",
            100,
            {"event_date": "2026-06-01T00:00:00Z"},
            {"event_date": "2026-06-30"},
        )
    ]

    result = find_boundary_files(
        files,
        [QueryRange("event_date", "2026-06-15", None, cpu_seconds_per_byte=1.0)],
    )

    assert result[0].edges == {"event_date": ("2026-06-15",)}
    assert result[0].savings_cpu_seconds == pytest.approx(100 * 14 / 29)


def test_ranges_without_cost_use_the_default_rate() -> None:
    files = [FileBounds("f.parquet", 100, {"v": 0}, {"v": 10})]

    result = find_boundary_files(files, [QueryRange("v", 5, 50)], default_cpu_seconds_per_byte=0.5)

    assert result[0].savings_cpu_seconds == pytest.approx(0.5 * 100 * 0.5)


def test_boundary_detection_ignores_incomparable_bounds() -> None:
    files = [FileBounds("bad.parquet", 100, {"value": "a"}, {"value": "z"})]

    assert find_boundary_files(files, [QueryRange("value", 1, 2)]) == []


def test_utilization_is_none_when_pruning_skips_the_file() -> None:
    assert utilization(0, 10, 20, 30) is None
    assert utilization(5, 5, 0, 10) == 1.0
    assert utilization(0, 10, None, 5) == 0.5


def _boundary(path: str, savings: float, column: str = "a") -> BoundaryFile:
    return BoundaryFile(path, GIB, savings, {column: savings}, {column: (1,)})


def test_algorithm_2_cuts_at_the_prefix_with_the_most_net_savings() -> None:
    params = WairCostParams(rewrite_gb_per_hour=1.0, lane_vcpus=1.0)  # 3600 CPU-s per GiB
    boundaries = [_boundary("a", 10_000), _boundary("b", 5_000, "b"), _boundary("c", 1_000)]

    result = select_boundary_files(boundaries, params)

    assert [b.file_path for b in result.selected] == ["a", "b"]
    assert result.recluster_cpu_seconds == pytest.approx(7200)
    assert result.net_cpu_seconds == pytest.approx(15_000 - 7200)
    assert result.clustering_column == "a"


def test_aggressiveness_and_cost_limit_shrink_the_cut() -> None:
    boundaries = [_boundary("a", 10_000), _boundary("b", 5_000), _boundary("c", 1_000)]

    timid = select_boundary_files(
        boundaries, WairCostParams(rewrite_gb_per_hour=1.0, lane_vcpus=1.0, aggressiveness=3.0)
    )
    capped = select_boundary_files(
        boundaries,
        WairCostParams(rewrite_gb_per_hour=1.0, lane_vcpus=1.0, cost_limit_cpu_seconds=5000),
    )

    assert timid.selected == ()
    assert timid.clustering_column is None
    assert [b.file_path for b in capped.selected] == ["a"]
