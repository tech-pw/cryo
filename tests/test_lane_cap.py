"""plan_lanes treats max_lanes as a hard upper bound."""

from __future__ import annotations

import math
import uuid

import pytest

from cryo.maintenance.lanes import (
    _LARGEST_TIER,
    _SIZE_TIERS,
    LaneLimits,
    LaneTable,
    lane_count,
    lane_size,
    plan_lanes,
)


def _tables(*gbs: float) -> list[LaneTable]:
    return [LaneTable(uuid.uuid4(), gb) for gb in gbs]


def test_empty_input_returns_no_lanes() -> None:
    assert plan_lanes([]) == []
    assert plan_lanes([], LaneLimits(max_lanes=1)) == []
    assert plan_lanes(_tables(1, 10), LaneLimits(max_lanes=0)) == []


@pytest.mark.parametrize(
    ("gbs", "max_lanes", "expected"),
    [
        ([1, 10], 4, 1),
        ([1, 10, 250], 1, 1),
        ([1, 10, 250, 900], 2, 2),
        ([0.1, 0.1, 0.1, 0.1, 0.1], 4, 1),
    ],
)
def test_reproduced_plans_stay_within_max_lanes(
    gbs: list[float], max_lanes: int, expected: int
) -> None:
    limits = LaneLimits(max_lanes=max_lanes)
    tables = _tables(*gbs)

    assert lane_count(tables, limits) == expected
    lanes = plan_lanes(tables, limits)

    assert len(lanes) == expected
    assert len(lanes) <= max_lanes
    assert sum(len(lane.tables) for lane in lanes) == len(tables)


def test_over_cap_tiers_pack_into_the_largest_shape_they_need() -> None:
    one = plan_lanes(_tables(1, 10), LaneLimits(max_lanes=4))
    assert [(lane.cpu_milli, lane.mem_mib) for lane in one] == [(2000, 8192)]

    capped = plan_lanes(_tables(1, 10, 250), LaneLimits(max_lanes=1))
    assert [(lane.cpu_milli, lane.mem_mib) for lane in capped] == [(4000, 16384)]
    assert sorted(t.rewrite_gb for t in capped[0].tables) == [1, 10, 250]

    two = plan_lanes(_tables(1, 10, 250, 900), LaneLimits(max_lanes=2))
    packed = next(lane for lane in two if lane.largest_table_gb == 10)
    large = next(lane for lane in two if lane.largest_table_gb == 900)
    assert (packed.cpu_milli, packed.mem_mib) == (2000, 8192)
    assert sorted(t.rewrite_gb for t in packed.tables) == [1, 10]
    assert (large.cpu_milli, large.mem_mib) == (4000, 16384)
    assert sorted(t.rewrite_gb for t in large.tables) == [250, 900]


def test_tiers_stay_separate_when_they_fit_in_the_budget() -> None:
    limits = LaneLimits(max_lanes=4, target_gb_per_lane=1)
    lanes = plan_lanes(_tables(1, 10, 250), limits)

    assert len(lanes) == 3
    assert len(lanes) <= limits.max_lanes
    assert sorted((lane.largest_table_gb, lane.cpu_milli, lane.mem_mib) for lane in lanes) == [
        (1, 1000, 4096),
        (10, 2000, 8192),
        (250, 4000, 16384),
    ]


def test_packed_lane_keeps_rank_order() -> None:
    tables = _tables(1, 900, 10, 250)
    lanes = plan_lanes(tables, LaneLimits(max_lanes=1))
    rank = {table.table_id: index for index, table in enumerate(tables)}

    assert len(lanes) == 1
    assert [rank[table.table_id] for table in lanes[0].tables] == sorted(
        rank[table.table_id] for table in lanes[0].tables
    )


def test_size_tier_constants_are_the_boundaries_under_test() -> None:
    assert _SIZE_TIERS == ((5.0, 1000, 4096), (200.0, 2000, 8192))
    assert _LARGEST_TIER == (4000, 16384)


@pytest.mark.parametrize(
    ("gb", "cpu", "mem"),
    [
        pytest.param(0.0, 1000, 4096, id="zero"),
        pytest.param(math.nextafter(5.0, 0.0), 1000, 4096, id="just-under-5"),
        pytest.param(5.0, 2000, 8192, id="5"),
        pytest.param(math.nextafter(200.0, 0.0), 2000, 8192, id="just-under-200"),
        pytest.param(200.0, 4000, 16384, id="200"),
        pytest.param(900.0, 4000, 16384, id="above-200"),
    ],
)
def test_tier_boundaries(gb: float, cpu: int, mem: int) -> None:
    lanes = plan_lanes(_tables(gb))

    assert len(lanes) == 1
    assert (lanes[0].cpu_milli, lanes[0].mem_mib) == (cpu, mem)
    assert (lanes[0].cpu_milli, lanes[0].mem_mib) == lane_size(gb, LaneLimits())


@pytest.mark.parametrize("max_lanes", [1, 2, 3, 4])
@pytest.mark.parametrize(
    "gbs",
    [
        [1, 10],
        [1, 10, 250],
        [1, 10, 250, 900],
        [0.1, 0.1, 0.1, 0.1, 0.1],
        [0.0, 5.0, 200.0],
        [math.nextafter(5.0, 0.0), 5.0, math.nextafter(200.0, 0.0), 200.0],
    ],
)
def test_len_plan_lanes_never_exceeds_max_lanes(gbs: list[float], max_lanes: int) -> None:
    limits = LaneLimits(max_lanes=max_lanes)
    tables = _tables(*gbs)
    lanes = plan_lanes(tables, limits)

    assert len(lanes) <= max_lanes
    assert sum(len(lane.tables) for lane in lanes) == len(tables)
    assert all(lane.tables for lane in lanes)
    for lane in lanes:
        assert (lane.cpu_milli, lane.mem_mib) == lane_size(lane.largest_table_gb, limits)
