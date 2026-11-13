"""Tests for unchanged-snapshot fast path, immutable manifest summary cache, and parity (#532)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cryo.config import Settings
from cryo.db.models import Source, Table, TablePartition, TableStats, TableStatsHistory
from cryo.observability.metrics import (
    scan_manifest_cache_hits_total,
    scan_manifest_cache_misses_total,
    scan_object_store_requests_total,
    scan_tables_total,
)
from cryo.scanner.analyze import analyze_table
from cryo.scanner.manifest_cache import CachedManifestEntry, ManifestSummaryCache
from cryo.scanner.persistence import persist_table_analysis
from cryo.scanner.run import run_scan
from cryo.scanner.unchanged import build_unchanged_table_analysis
from tests.scanner_parity.compare import normalize_analysis
from tests.scanner_parity.fixtures import day_iso_table
from tests.test_scanner import (
    MB,
    FakeDataFile,
    FakeManifestEntry,
    FakePartitionField,
    FakeSchema,
    FakeSnapshotMeta,
    FakeSpec,
)


@dataclass
class _CountingManifest:
    manifest_path: str
    files: list[FakeDataFile]
    manifest_length: int = 2048
    fetches: int = 0

    def fetch_manifest_entry(self, io: object, **_kwargs: Any) -> list[FakeManifestEntry]:
        del io
        self.fetches += 1
        return [FakeManifestEntry(f) for f in self.files]


class _CountingSnapshot:
    def __init__(self, snapshot_id: int, manifests: list[_CountingManifest]) -> None:
        self.snapshot_id = snapshot_id
        self._manifests = manifests
        self.manifest_list_reads = 0

    def manifests(self, io: object) -> list[_CountingManifest]:
        del io
        self.manifest_list_reads += 1
        return list(self._manifests)


class _CountingTable:
    io = object()

    def __init__(
        self,
        *,
        snapshot_id: int,
        manifests: list[_CountingManifest],
        snapshots: list[FakeSnapshotMeta],
    ) -> None:
        self._snap = _CountingSnapshot(snapshot_id, manifests)
        self.metadata = SimpleNamespace(
            current_snapshot_id=snapshot_id,
            snapshots=snapshots,
            properties={
                "write.distribution-mode": "hash",
                "write.target-file-size-bytes": "268435456",
            },
            default_sort_order_id=1,
            sort_orders=[SimpleNamespace(order_id=1, fields=[SimpleNamespace(source_id=1)])],
            format_version=2,
        )

    def spec(self) -> FakeSpec:
        return FakeSpec([FakePartitionField()])

    def schema(self) -> FakeSchema:
        return FakeSchema()

    def current_snapshot(self) -> _CountingSnapshot:
        return self._snap


class _CountingCatalog:
    def __init__(self, table: _CountingTable) -> None:
        self.table = table
        self.load_calls = 0

    def list_tables(self) -> list[tuple[str, str]]:
        return [("demo", "events")]

    def load_table(self, schema: str, table: str) -> _CountingTable:
        assert (schema, table) == ("demo", "events")
        self.load_calls += 1
        return self.table


def _seed_source(session: Any) -> Source:
    src = Source(
        id=uuid.uuid4(),
        name=f"src-{uuid.uuid4().hex[:8]}",
        type="rest",
        enabled=True,
        config={"uri": "http://localhost:8181", "warehouse": "s3://demo/wh"},
    )
    session.add(src)
    session.commit()
    return src


def test_unchanged_fast_path_skips_manifests_recomputes_stale_grade_and_reconciles_weekly(
    session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cryo.scanner.puffin_writer.write_puffin_blob",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("disabled")),
    )
    src = _seed_source(session)
    t0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    commit_at = t0 - timedelta(days=2)

    m0 = _CountingManifest(
        manifest_path="s3://demo/wh/events/metadata/m0.avro",
        files=[
            FakeDataFile(
                12 * MB, 100, ("2026-05-30",), file_path="s3://demo/wh/events/d/1.parquet"
            ),
            FakeDataFile(
                14 * MB, 120, ("2026-05-30",), file_path="s3://demo/wh/events/d/2.parquet"
            ),
        ],
    )
    tbl = _CountingTable(snapshot_id=777, manifests=[m0], snapshots=[FakeSnapshotMeta(commit_at)])
    catalog = _CountingCatalog(tbl)
    settings = Settings(
        _env_file=None,
        cost_scan_tier="heuristic",
        scan_manifest_cache_dir=str(tmp_path / "mcache"),
        scan_manifest_cache_max_mb=64,
    )

    before_full = scan_tables_total.labels(kind="full")._value.get()
    before_unchanged = scan_tables_total.labels(kind="unchanged")._value.get()

    # Day 1: initial full scan reads manifest list + manifest.
    summary1 = run_scan(
        session,
        settings=settings,
        source_id=src.id,
        num_chunks=1,
        now=t0,
        source_factory=lambda _s: catalog,
    )
    assert summary1.scanned == 1
    assert tbl._snap.manifest_list_reads == 1
    assert m0.fetches == 1
    assert scan_tables_total.labels(kind="full")._value.get() == before_full + 1

    table_row = session.query(Table).filter_by(source_id=src.id, table_name="events").one()
    stats1 = session.get(TableStats, table_row.id)
    assert stats1 is not None
    assert stats1.raw["scan_kind"] == "full"
    initial_score = stats1.overall_score
    reconcile_at_1 = stats1.last_full_reconcile_at

    # Day 4 (snapshot unchanged, within 7-day reconcile window): fast path skips manifest walk!
    reqs_before = scan_object_store_requests_total._value.get()
    summary2 = run_scan(
        session,
        settings=settings,
        source_id=src.id,
        num_chunks=1,
        now=t0 + timedelta(days=3),
        source_factory=lambda _s: catalog,
    )
    assert summary2.scanned == 1
    assert tbl._snap.manifest_list_reads == 1  # unchanged!
    assert m0.fetches == 1  # unchanged!
    assert scan_object_store_requests_total._value.get() == reqs_before + 1  # only metadata.json!
    assert scan_tables_total.labels(kind="unchanged")._value.get() == before_unchanged + 1

    session.expire_all()
    stats2 = session.get(TableStats, table_row.id)
    assert stats2 is not None
    assert stats2.raw["scan_kind"] == "unchanged"
    assert stats2.total_files == 2
    assert stats2.small_files_count == 2
    assert stats2.last_full_reconcile_at == reconcile_at_1
    assert session.query(TablePartition).filter_by(table_id=table_row.id).count() == 1
    assert session.query(TableStatsHistory).filter_by(table_id=table_row.id).count() == 2

    # Advance commit age past 30d while keeping last_full_reconcile_at recent to verify
    # time-based 'Stale table commits' (-20) is recomputed on the unchanged fast path.
    t_stale = t0 + timedelta(days=35)
    stats2.last_full_reconcile_at = t_stale - timedelta(days=2)
    session.commit()

    run_scan(
        session,
        settings=settings,
        source_id=src.id,
        num_chunks=1,
        now=t_stale,
        source_factory=lambda _s: catalog,
    )
    session.expire_all()
    stats_stale = session.get(TableStats, table_row.id)
    assert stats_stale is not None
    assert stats_stale.raw["scan_kind"] == "unchanged"
    assert any(d["label"] == "Stale table commits" for d in stats_stale.raw["health_deductions"])
    assert stats_stale.overall_score == initial_score - 20

    # Weekly reconcile (7+ days after last_full_reconcile_at) forces a full walk, and
    # --full-scan also forces a full walk.
    t_weekly = t_stale + timedelta(days=6)
    run_scan(
        session,
        settings=settings,
        source_id=src.id,
        num_chunks=1,
        now=t_weekly,
        source_factory=lambda _s: catalog,
    )
    session.expire_all()
    stats_weekly = session.get(TableStats, table_row.id)
    assert stats_weekly is not None
    assert stats_weekly.raw["scan_kind"] == "full"
    assert tbl._snap.manifest_list_reads == 2
    # Manifest m0 was cached on disk during Day 1, so m0.fetches stays 1 (cache hit!).
    assert m0.fetches == 1


def test_immutable_manifest_cache_fetches_only_new_manifest_on_commit_and_evicts_lru(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cryo.scanner.puffin_writer.write_puffin_blob",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("disabled")),
    )
    scan_at = datetime(2026, 6, 30, tzinfo=UTC)
    cache_dir = tmp_path / "cache"
    settings = Settings(
        _env_file=None,
        cost_scan_tier="heuristic",
        scan_manifest_cache_dir=str(cache_dir),
        scan_manifest_cache_max_mb=64,
        scan_parallel_manifests=1,
    )

    m_old = _CountingManifest(
        manifest_path="s3://demo/wh/events/metadata/m_old.avro",
        files=[
            FakeDataFile(
                10 * MB, 50, ("2026-06-28",), file_path="s3://demo/wh/events/d/old.parquet"
            )
        ],
    )
    m_new = _CountingManifest(
        manifest_path="s3://demo/wh/events/metadata/m_new.avro",
        files=[
            FakeDataFile(
                15 * MB, 75, ("2026-06-29",), file_path="s3://demo/wh/events/d/new.parquet"
            )
        ],
    )

    hits_before = scan_manifest_cache_hits_total._value.get()
    misses_before = scan_manifest_cache_misses_total._value.get()

    tbl1 = _CountingTable(
        snapshot_id=1, manifests=[m_old], snapshots=[FakeSnapshotMeta(scan_at - timedelta(days=1))]
    )
    res1 = analyze_table(
        tbl1, "demo", "events", scan_at=scan_at, settings=settings, parallel_manifests=False
    )
    assert m_old.fetches == 1
    assert scan_manifest_cache_misses_total._value.get() == misses_before + 1

    # Table commits a new snapshot containing m_old (unchanged) + m_new (new).
    tbl2 = _CountingTable(
        snapshot_id=2,
        manifests=[m_old, m_new],
        snapshots=[FakeSnapshotMeta(scan_at - timedelta(hours=1))],
    )
    res2 = analyze_table(
        tbl2, "demo", "events", scan_at=scan_at, settings=settings, parallel_manifests=False
    )
    assert m_old.fetches == 1  # served from immutable manifest cache!
    assert m_new.fetches == 1  # only the new manifest was fetched!
    assert scan_manifest_cache_hits_total._value.get() == hits_before + 1
    assert scan_manifest_cache_misses_total._value.get() == misses_before + 2
    assert res2.total_files == 2
    assert res2.total_records == 125

    # Verify cached scan produces identical normalized TableAnalysis to uncached scan.
    no_cache_settings = Settings(
        _env_file=None,
        cost_scan_tier="heuristic",
        scan_manifest_cache_dir="",
        scan_manifest_cache_max_mb=0,
    )
    uncached = analyze_table(
        tbl2,
        "demo",
        "events",
        scan_at=scan_at,
        settings=no_cache_settings,
        parallel_manifests=False,
    )
    assert normalize_analysis(res2) == normalize_analysis(uncached)
    del res1

    # LRU eviction by size.
    tiny_cache = ManifestSummaryCache(str(tmp_path / "tiny"), max_bytes=450)
    entry = CachedManifestEntry(
        file_path="s3://b/f.parquet",
        file_size_in_bytes=100,
        record_count=1,
        content="DATA",
        file_format="PARQUET",
        spec_id=0,
        partition_values={},
        null_value_counts={},
        value_counts={},
        lower_bounds={},
        upper_bounds={},
        snapshot_id=1,
    )
    tiny_cache.put("s3://b/m1.avro", [entry])
    assert tiny_cache.get("s3://b/m1.avro") is not None
    import os
    import time

    time.sleep(0.02)
    tiny_cache.put("s3://b/m2.avro", [entry])
    # Capacity ~450B fits only one ~300B JSON file; m1 (older mtime) is evicted, m2 stays.
    assert tiny_cache.get("s3://b/m1.avro") is None
    assert tiny_cache.get("s3://b/m2.avro") is not None
    del os


def test_parity_unchanged_fast_path_matches_full_scan_for_unchanged_table(
    session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cryo.scanner.puffin_writer.write_puffin_blob",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("disabled")),
    )
    src = _seed_source(session)
    scan_at = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    table = day_iso_table(scan_at=scan_at - timedelta(days=1))
    settings = Settings(
        _env_file=None,
        cost_scan_tier="heuristic",
        scan_manifest_cache_dir="",
        scan_manifest_cache_max_mb=0,
    )

    full = analyze_table(table, "demo", "events_day_iso", scan_at=scan_at, settings=settings)
    assert not isinstance(full, dict)
    table_id = persist_table_analysis(session, source_id=src.id, analysis=full)
    session.commit()

    existing_stats = session.get(TableStats, table_id)
    assert existing_stats is not None
    unchanged = build_unchanged_table_analysis(
        session,
        table_id=table_id,
        schema_name="demo",
        table_name="events_day_iso",
        table_obj=table,
        existing_stats=existing_stats,
        scan_at=scan_at,
        settings=settings,
    )

    norm_full = normalize_analysis(full)
    norm_unchanged = normalize_analysis(unchanged)
    norm_full["raw"].pop("scan_kind", None)
    norm_unchanged["raw"].pop("scan_kind", None)
    assert norm_unchanged == norm_full
