"""Tests for cryo/scanner/silent_data_loss.py — arXiv:2604.20081."""

from __future__ import annotations

import pytest

from cryo.scanner.silent_data_loss import (
    CommittedManifestEntry,
    OrphanReport,
    StorageFile,
    detect_silent_data_loss,
    list_local_storage_files,
)


class TestDetectSilentDataLoss:
    def test_clean_table(self) -> None:
        storage = [
            StorageFile(path="s3://bucket/data/file1.parquet", size_bytes=100),
            StorageFile(path="s3://bucket/data/file2.parquet", size_bytes=200),
        ]
        committed = [
            CommittedManifestEntry(path="s3://bucket/data/file1.parquet", size_bytes=100),
            CommittedManifestEntry(path="s3://bucket/data/file2.parquet", size_bytes=200),
        ]
        report = detect_silent_data_loss(
            table_id="table-1",
            storage_files=storage,
            committed_entries=committed,
            snapshot_id=42,
        )
        assert report.is_clean()
        assert report.orphan_data_files == []
        assert report.missing_files == []
        assert report.root_cause == "clean"

    def test_orphans_detected(self) -> None:
        storage = [
            StorageFile(path="s3://bucket/data/file1.parquet", size_bytes=100),
            StorageFile(path="s3://bucket/data/orphan.parquet", size_bytes=50),
        ]
        committed = [
            CommittedManifestEntry(path="s3://bucket/data/file1.parquet", size_bytes=100),
        ]
        report = detect_silent_data_loss(
            table_id="table-2",
            storage_files=storage,
            committed_entries=committed,
        )
        assert not report.is_clean()
        # detect_silent_data_loss diffs full paths (StorageFile.path / CommittedManifestEntry
        # .path are documented as full storage paths, not basenames) -- an operator needs the
        # full s3:// path to actually locate and delete the orphan.
        assert "s3://bucket/data/orphan.parquet" in report.orphan_data_files
        assert report.orphan_size_bytes == 50
        assert report.missing_files == []
        assert report.root_cause == "orphan_files_on_storage"

    def test_missing_files_detected(self) -> None:
        storage = [
            StorageFile(path="s3://bucket/data/file1.parquet", size_bytes=100),
        ]
        committed = [
            CommittedManifestEntry(path="s3://bucket/data/file1.parquet", size_bytes=100),
            CommittedManifestEntry(path="s3://bucket/data/missing.parquet", size_bytes=200),
        ]
        report = detect_silent_data_loss(
            table_id="table-3",
            storage_files=storage,
            committed_entries=committed,
        )
        assert not report.is_clean()
        assert report.orphan_data_files == []
        assert "s3://bucket/data/missing.parquet" in report.missing_files
        assert report.root_cause == "missing_committed_files"

    def test_orphans_and_missing_concurrent_drift(self) -> None:
        storage = [
            StorageFile(path="s3://bucket/data/extra.parquet", size_bytes=100),
        ]
        committed = [
            CommittedManifestEntry(path="s3://bucket/data/missing.parquet", size_bytes=50),
        ]
        report = detect_silent_data_loss(
            table_id="table-4",
            storage_files=storage,
            committed_entries=committed,
        )
        assert report.orphan_data_files == ["s3://bucket/data/extra.parquet"]
        assert report.missing_files == ["s3://bucket/data/missing.parquet"]
        assert report.root_cause == "concurrent_writer_drift"

    def test_trailing_slash_normalized(self) -> None:
        storage = [
            StorageFile(path="s3://bucket/data/file1.parquet/", size_bytes=100),
        ]
        committed = [
            CommittedManifestEntry(path="s3://bucket/data/file1.parquet", size_bytes=100),
        ]
        report = detect_silent_data_loss(
            table_id="table-5",
            storage_files=storage,
            committed_entries=committed,
        )
        assert report.is_clean()

    def test_snapshot_id_passed_through(self) -> None:
        storage = [StorageFile(path="s3://bucket/data/f1.parquet")]
        committed = [CommittedManifestEntry(path="s3://bucket/data/f1.parquet")]
        report = detect_silent_data_loss(
            table_id="table-6",
            storage_files=storage,
            committed_entries=committed,
            snapshot_id=99,
        )
        assert report.snapshot_id == 99

    def test_detected_at_set(self) -> None:
        storage = [StorageFile(path="s3://bucket/data/f1.parquet")]
        committed = [CommittedManifestEntry(path="s3://bucket/data/f1.parquet")]
        report = detect_silent_data_loss(
            table_id="table-7",
            storage_files=storage,
            committed_entries=committed,
        )
        assert report.detected_at != ""

    def test_empty_storage_only_committed(self) -> None:
        storage: list[StorageFile] = []
        committed = [CommittedManifestEntry(path="s3://bucket/data/missing.parquet")]
        report = detect_silent_data_loss(
            table_id="table-8",
            storage_files=storage,
            committed_entries=committed,
        )
        assert "s3://bucket/data/missing.parquet" in report.missing_files
        assert report.root_cause == "missing_committed_files"

    def test_empty_committed_only_orphans(self) -> None:
        storage = [StorageFile(path="s3://bucket/data/orphan.parquet", size_bytes=100)]
        committed: list[CommittedManifestEntry] = []
        report = detect_silent_data_loss(
            table_id="table-9",
            storage_files=storage,
            committed_entries=committed,
        )
        assert "s3://bucket/data/orphan.parquet" in report.orphan_data_files
        assert report.orphan_size_bytes == 100
        assert report.root_cause == "orphan_files_on_storage"


class TestListLocalStorageFiles:
    def test_empty_table_dir(self, tmp_path: pytest.ChdirFixture) -> None:
        files = list_local_storage_files("nonexistent-table", root=str(tmp_path))
        assert files == []
        assert list_local_storage_files("../etc", root=str(tmp_path)) == []

    def test_finds_parquet_and_avro_files(self, tmp_path: pytest.ChdirFixture) -> None:
        table_id = "test-table-123"
        table_root = tmp_path / table_id.replace("-", "") / "data"
        table_root.mkdir(parents=True)
        (table_root / "file1.parquet").write_text("parquet")
        (table_root / "file2.avro").write_text("avro")
        (table_root / "file3.orc").write_text("orc")
        (table_root / "file4.txt").write_text("txt")
        (table_root / "file5.PARQUET").write_text("parquet2")

        files = list_local_storage_files(table_id, root=str(tmp_path))
        names = {f.path.split("/")[-1] for f in files}
        assert "file1.parquet" in names
        assert "file2.avro" in names
        assert "file3.orc" in names
        assert "file4.txt" not in names
        assert "file5.PARQUET" in names  # case-insensitive suffix


class TestOrphanReportDataclass:
    def test_is_clean_true(self) -> None:
        report = OrphanReport(
            table_id="t1",
            snapshot_id=1,
            orphan_data_files=[],
            missing_files=[],
        )
        assert report.is_clean() is True

    def test_is_clean_false_orphan(self) -> None:
        report = OrphanReport(
            table_id="t2",
            snapshot_id=1,
            orphan_data_files=["orphan.parquet"],
        )
        assert report.is_clean() is False

    def test_is_clean_false_missing(self) -> None:
        report = OrphanReport(
            table_id="t3",
            snapshot_id=1,
            orphan_data_files=[],
            missing_files=["missing.parquet"],
        )
        assert report.is_clean() is False
