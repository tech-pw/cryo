"""Regression for #288: the health score's size scaling was dead code.

``compute_health_score`` scales the count-based limits (file count, partition count,
manifest count) by table size, so a large table is not permanently penalised for holding the
number of files its own layout requires. But neither scanner put ``total_size_gb`` into the
dict it scores, so the scale was always 1 and every table -- 1 GB or 100 TB -- was judged
against the same flat limits.

The two scan-path tests are the real regression guards: they capture the stats dict the
scorer actually receives, on both the Python and the Go scan path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cryo.scanner.health import (
    HEALTH_THRESHOLDS,
    _scaled_thresholds,
    health_thresholds_for,
)

SCANNED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def test_scaled_thresholds_scale_with_size() -> None:
    """The helper itself scales -- this is the behaviour the scanners were not reaching."""
    small = _scaled_thresholds(1.0)
    large = _scaled_thresholds(10_000.0)

    assert small["partition_count_warn"] == HEALTH_THRESHOLDS["partition_count_warn"]
    assert large["partition_count_warn"] > HEALTH_THRESHOLDS["partition_count_warn"]


def test_size_scaled_thresholds_are_used_by_the_scorer() -> None:
    """The scorer's limits reflect the size it is given."""
    stats = {
        "total_files": 100,
        "total_partitions": 50,
        "manifest_count": 10,
        "avg_file_size_mb": 100,
        "total_size_gb": 40_000.0,
    }

    thresholds = health_thresholds_for(stats)

    assert thresholds["partition_count_warn"] > HEALTH_THRESHOLDS["partition_count_warn"]
    assert thresholds["manifest_count_warn"] > HEALTH_THRESHOLDS["manifest_count_warn"]


def test_percentages_are_not_scaled() -> None:
    """Ratio and percentage limits are scale-independent by design."""
    thresholds = _scaled_thresholds(100_000.0)

    assert thresholds["small_file_pct_warn"] == HEALTH_THRESHOLDS["small_file_pct_warn"]
    assert thresholds["delete_file_pct_crit"] == HEALTH_THRESHOLDS["delete_file_pct_crit"]
    assert (
        thresholds["metadata_to_data_ratio_warn"]
        == HEALTH_THRESHOLDS["metadata_to_data_ratio_warn"]
    )


def test_missing_size_falls_back_to_the_flat_limits() -> None:
    """A stats dict with no size still grades against the flat limits."""
    thresholds = health_thresholds_for({"total_files": 10, "total_partitions": 1})

    assert thresholds["partition_count_warn"] == HEALTH_THRESHOLDS["partition_count_warn"]


def test_python_scan_passes_the_size_into_the_scorer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Python scan path hands the scorer a stats dict that carries the size.

    ``analyze_table`` builds ``stats_for_health`` and scores it. If that dict lacks
    ``total_size_gb``, the scale is 1 no matter how large the table is, and every
    count-based limit stays flat.
    """
    import cryo.scanner.analyze as analyze_module
    from cryo.scanner.analyze import analyze_table
    from tests.test_scanner import FakeDataFile, FakeSnapshotMeta, FakeTable

    captured: dict[str, Any] = {}
    real_compute = analyze_module.compute_health_score

    def _capture(stats: Any, small_file_count: int, **kwargs: Any) -> Any:
        captured.update(stats)
        return real_compute(stats, small_file_count, **kwargs)

    monkeypatch.setattr(analyze_module, "compute_health_score", _capture)

    # 400 partitions of 512 MiB is ~200 GB, past the 100 GB floor where the size scale
    # starts to lift the count-based limits.
    files = [FakeDataFile(512 * 1024 * 1024, 1, (f"2026-01-{i:03d}",)) for i in range(400)]
    table = FakeTable(files=files, snapshots=[FakeSnapshotMeta(SCANNED_AT - timedelta(days=1))])

    analyze_table(table, "demo", "events", scan_at=SCANNED_AT)

    assert captured, "the scorer was never called"
    assert "total_size_gb" in captured, "the scorer got a stats dict with no size"
    assert float(captured["total_size_gb"]) > 100.0


def test_go_scan_passes_the_size_into_the_scorer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Go scan path hands the scorer a stats dict that carries the size."""
    import cryo.scanner.go_scan as go_module
    from cryo.config import Settings
    from tests.test_scanner_parity import _go_payload_fixture

    captured: dict[str, Any] = {}
    real_compute = go_module.compute_health_score

    def _capture(stats: Any, small_file_count: int, **kwargs: Any) -> Any:
        captured.update(stats)
        return real_compute(stats, small_file_count, **kwargs)

    monkeypatch.setattr(go_module, "compute_health_score", _capture)

    # 400 files of 512 MiB is ~200 GB, past the 100 GB floor.
    payload = _go_payload_fixture(
        total_files=400,
        total_size_bytes=400 * 512 * 1024 * 1024,
        total_partitions=400,
    )

    go_module.table_analysis_from_go_payload(
        payload,
        schema_name="demo",
        table_name="events",
        scan_at=SCANNED_AT,
        settings=Settings(),
    )

    assert captured, "the scorer was never called"
    assert "total_size_gb" in captured, "the scorer got a stats dict with no size"
    assert float(captured["total_size_gb"]) > 100.0
