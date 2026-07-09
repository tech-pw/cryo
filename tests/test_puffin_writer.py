"""Regression tests for Puffin sketch seeding.

Large tables previously hung the scanner because ``build_sketch_payload`` synthesized
~value_count/log2(value_count) distinct tokens per hot column (hundreds of millions on
billion-row tables) and fed them into ThetaSketch one-by-one.
"""

from __future__ import annotations

import time

from cryo.scanner.puffin_writer import (
    _MAX_SYNTHETIC_NDV,
    _approx_ndv_from_quality,
    _synthesize_distinct_values,
    build_sketch_payload,
    select_hot_columns,
)


def test_approx_ndv_caps_billion_row_columns() -> None:
    col = {"value_count": 3_687_247_585}
    assert _approx_ndv_from_quality(col) == _MAX_SYNTHETIC_NDV


def test_synthesize_distinct_values_never_exceeds_cap() -> None:
    values = list(_synthesize_distinct_values({"lower_bound": 0, "upper_bound": 1}, 10**9))
    assert len(values) == _MAX_SYNTHETIC_NDV


def test_build_sketch_payload_completes_for_geoip_scale_columns() -> None:
    """Reproduce the geoip.silver_geoip_locations hang shape: many hot columns, billions of rows."""
    columns = [
        {
            "field_id": index,
            "column_name": f"col_{index}",
            "column_type": "long",
            "null_count": 0,
            "value_count": 3_687_247_585,
            "lower_bound": 0,
            "upper_bound": 10**12,
        }
        for index in range(29)
    ]
    assert len(select_hot_columns(columns)) == 29

    started = time.perf_counter()
    payload = build_sketch_payload(columns)
    elapsed = time.perf_counter() - started

    assert len(payload) == 29
    assert all("theta" in sketches and "kll" in sketches for sketches in payload.values())
    # Must stay well under scan_table_timeout_seconds (180s); a few seconds is plenty.
    assert elapsed < 5.0, f"sketch build took {elapsed:.1f}s; uncapped NDV would hang the scanner"
