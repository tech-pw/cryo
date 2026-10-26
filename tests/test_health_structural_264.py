"""#264: grade on what maintenance can fix; report structural findings separately."""

from __future__ import annotations

from datetime import UTC, datetime

from cryo.scanner.health import (
    compute_design_findings,
    compute_health_score,
    file_count_floor,
)

NOW = datetime(2026, 10, 26, tzinfo=UTC)
GB_MB = 1024.0


def _issue_table(**overrides: object) -> dict[str, object]:
    """The ~28 TB production table from the issue (identity(string) x day)."""
    stats: dict[str, object] = {
        "total_files": 180_380,
        "avg_file_size_mb": 161.0,
        "total_partitions": 71_429,
        "partition_skew_ratio": 94.0,
        "total_snapshots": 364,
        "commits_per_day": 210.0,
        "is_streaming": False,
        "target_file_size_bytes": 536_870_912,
        "last_snapshot_committed_at": NOW.isoformat(),
        "has_sort_order": True,
        "write_distribution_mode": "hash",
        "file_formats": ["PARQUET"],
    }
    stats.update(overrides)
    return stats


def _labels(items: list) -> set[str]:
    return {item["label"] for item in items}


def test_structural_partition_findings_never_lower_the_score() -> None:
    stats = _issue_table()
    _score, _grade, deductions = compute_health_score(stats, 0, now=NOW)
    labels = _labels(deductions)
    assert "Partition count complexity" not in labels
    assert "Partition volume skew" not in labels

    findings = compute_design_findings(stats)
    assert _labels(findings) == {"Partition count complexity", "Partition volume skew"}
    skew = next(f for f in findings if f["label"] == "Partition volume skew")
    assert skew["severity"] == "critical"
    assert all(f["recommendation"] for f in findings)
    assert all("deduction" not in f for f in findings)


def test_issue_table_is_no_longer_stuck_at_f() -> None:
    score, grade, deductions = compute_health_score(_issue_table(), 0, now=NOW)
    # Was 32 (F): -20 files, -8 partitions, -20 skew, -20 snapshots.
    assert grade != "F"
    assert score >= 75
    assert 100 - sum(d["deduction"] for d in deductions) == score


def test_file_count_limit_is_relative_to_the_layout_floor() -> None:
    stats = _issue_table()
    # 71,429 partitions vs ~28.4 TB / 512 MB ~= 55k target-size files: partitions win.
    assert file_count_floor(stats) == 71_429
    _s, _g, deductions = compute_health_score(stats, 0, now=NOW)
    files = [d for d in deductions if d["label"] in {"Excessive file count", "High file count"}]
    # 180k files is 2.5x the floor: a warning, not the flat >100k critical.
    assert [d["severity"] for d in files] == ["warning"]

    far_past_floor = _issue_table(total_files=400_000, avg_file_size_mb=72.0)
    _s, _g, deductions = compute_health_score(far_past_floor, 0, now=NOW)
    assert any(d["label"] == "Excessive file count" for d in deductions)


def test_file_count_floor_uses_size_over_target_when_larger() -> None:
    stats = {
        "total_files": 1_000,
        "avg_file_size_mb": 10 * GB_MB,  # 1,000 files x 10 GB
        "total_partitions": 10,
        "target_file_size_bytes": 1024 * 1024 * 1024,
    }
    assert file_count_floor(stats) == 10_000


def test_unpartitioned_small_table_keeps_the_fixed_file_limits() -> None:
    stats = {"total_files": 150_000, "avg_file_size_mb": 1.0, "total_partitions": 0}
    _s, _g, deductions = compute_health_score(stats, 0, now=NOW)
    assert any(
        d["label"] == "Excessive file count" and d["severity"] == "critical" for d in deductions
    )


def test_snapshot_limit_follows_commit_rate_and_retention() -> None:
    # 210 commits/day -> planner tightens retention to 3 days -> ~630 expected snapshots.
    _s, _g, deductions = compute_health_score(_issue_table(), 0, now=NOW)
    assert "Snapshot retention buildup" not in _labels(deductions)

    backlog = _issue_table(total_snapshots=2_000)
    _s, _g, deductions = compute_health_score(backlog, 0, now=NOW)
    snap = [d for d in deductions if d["label"] == "Snapshot retention buildup"]
    assert [d["severity"] for d in snap] == ["critical"]


def test_snapshot_limit_keeps_fixed_floor_for_quiet_tables() -> None:
    quiet = _issue_table(commits_per_day=1.0, total_snapshots=250)
    _s, _g, deductions = compute_health_score(quiet, 0, now=NOW)
    snap = [d for d in deductions if d["label"] == "Snapshot retention buildup"]
    assert [d["severity"] for d in snap] == ["critical"]


def test_configured_retention_widens_the_snapshot_limit() -> None:
    stats = _issue_table(commits_per_day=10.0, total_snapshots=180, snapshot_retention_days=30)
    _s, _g, deductions = compute_health_score(stats, 0, now=NOW)
    assert "Snapshot retention buildup" not in _labels(deductions)


def test_no_design_findings_for_a_well_shaped_table() -> None:
    assert compute_design_findings({"total_partitions": 120, "partition_skew_ratio": 2.0}) == []
