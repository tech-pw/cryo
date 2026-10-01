from __future__ import annotations

from types import SimpleNamespace

from cryo.api.extensions import _sample_file_paths
from cryo.scanner.analyze import _PartitionAccum, _partition_raw
from cryo.scanner.go_scan import _partition_raw_from_go


def test_python_partition_raw_carries_sample_path() -> None:
    accum = _PartitionAccum(values={}, fields=[], spec_id=0, sample_path="s3://b/t/data/a.parquet")
    assert _partition_raw(accum)["sample_file_path"] == "s3://b/t/data/a.parquet"


def test_go_partition_raw_carries_sample_path() -> None:
    raw = _partition_raw_from_go({"d": "x"}, 0, None, "s3://b/t/data/a.parquet")
    assert raw["sample_file_path"] == "s3://b/t/data/a.parquet"
    assert "sample_file_path" not in _partition_raw_from_go({}, 0, None, None)


class _Session:
    def __init__(self, raws: list[dict]) -> None:
        self.raws = raws
        self.calls = 0

    def scalar(self, _stmt):
        return "2026-10-01"

    def scalars(self, _stmt):
        return iter(self.raws)


def test_sample_prefers_oldest_partitions_and_skips_missing_paths() -> None:
    raws = [
        {"sample_file_path": "s3://b/new", "last_write_at": "2026-09-30T00:00:00.000Z"},
        {"sample_file_path": "s3://b/old", "last_write_at": "2025-01-01T00:00:00.000Z"},
        {"sample_file_path": "s3://b/unknown"},
        {"delete_files": 0},
    ]
    paths = _sample_file_paths(_Session(raws), SimpleNamespace(), limit=2)
    assert paths == ["s3://b/old", "s3://b/new"]
