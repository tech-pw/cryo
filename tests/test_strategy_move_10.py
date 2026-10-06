"""Move 10 — V2→V3 format detection + deletion-vector upgrade flag.

The helpers in ``cryo/scanner/signals.py`` are already called from
``cryo/scanner/analyze.py`` and the health-score deduction lives in
``cryo/scanner/health.py``. These tests pin the contract so it cannot regress silently.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cryo.scanner.health import compute_health_score
from cryo.scanner.signals import detect_format_version, is_v3_upgrade_candidate


class _Properties:
    def __init__(self, props: dict[str, object]) -> None:
        self.props = props

    def __call__(self) -> dict[str, object]:
        return self.props


class _Spec:
    def __init__(self, spec_id: int | None = 1) -> None:
        self.spec_id = spec_id


class _Metadata:
    def __init__(self, props: dict[str, object]) -> None:
        self.properties = props


class _FakeTableObj:
    def __init__(
        self,
        *,
        properties: dict[str, object],
        metadata_props: dict[str, object] | None = None,
        spec_id: int | None = 1,
    ) -> None:
        self.properties = _Properties(properties)
        self.metadata = _Metadata(metadata_props if metadata_props is not None else {})
        self.spec = _Spec(spec_id)


def test_detect_format_version_v2_from_table_property() -> None:
    obj = _FakeTableObj(properties={"format-version": "2"})
    assert detect_format_version(obj) == 2


def test_detect_format_version_v3_from_table_property() -> None:
    obj = _FakeTableObj(properties={"format-version": "3"})
    assert detect_format_version(obj) == 3


def test_detect_format_version_falls_back_to_two_when_spec_present() -> None:
    obj = _FakeTableObj(properties={}, spec_id=1)
    assert detect_format_version(obj) == 2


def test_detect_format_version_falls_back_to_one_when_no_spec() -> None:
    obj = _FakeTableObj(properties={}, spec_id=None)
    assert detect_format_version(obj) == 1


def test_is_v3_upgrade_candidate_true_for_v2_with_high_delete_ratio() -> None:
    assert is_v3_upgrade_candidate(2, 5.0) is True
    assert is_v3_upgrade_candidate(2, 25.0) is True


def test_is_v3_upgrade_candidate_false_for_v3() -> None:
    assert is_v3_upgrade_candidate(3, 50.0) is False


def test_is_v3_upgrade_candidate_false_below_threshold() -> None:
    assert is_v3_upgrade_candidate(2, 4.9) is False


def test_health_score_deducts_v3_upgrade_candidate() -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    stats = {
        "total_files": 100,
        "total_delete_files": 0,
        "delete_file_pct": 0,
        "total_snapshots": 0,
        "first_updated_at": now,
        "manifest_count": 0,
        "total_partitions": 0,
        "partition_skew_ratio": 0,
        "avg_files_per_partition": 0,
        "write_distribution_mode": "hash",
        "has_sort_order": True,
        "target_file_size_bytes": 268_435_456,
        "file_formats": ["PARQUET"],
        "format_version": 2,
        "v3_upgrade_candidate": True,
    }

    _score, _grade, deductions = compute_health_score(stats, health_small_file_count=0, now=now)

    upgrade = [d for d in deductions if d["label"] == "V3 upgrade candidate"]
    assert upgrade, "expected V3 upgrade candidate deduction"
    assert upgrade[0]["severity"] == "warning"
    assert upgrade[0]["deduction"] == 8
    assert "V3" in upgrade[0]["reason"]


def test_health_score_no_v3_deduction_when_already_on_v3() -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    stats = {
        "total_files": 100,
        "total_delete_files": 0,
        "delete_file_pct": 0,
        "total_snapshots": 0,
        "first_updated_at": now,
        "manifest_count": 0,
        "total_partitions": 0,
        "partition_skew_ratio": 0,
        "avg_files_per_partition": 0,
        "write_distribution_mode": "hash",
        "has_sort_order": True,
        "target_file_size_bytes": 268_435_456,
        "file_formats": ["PARQUET"],
        "format_version": 3,
        "v3_upgrade_candidate": False,
    }

    _score, _grade, deductions = compute_health_score(stats, health_small_file_count=0, now=now)
    assert not any(d["label"] == "V3 upgrade candidate" for d in deductions)
