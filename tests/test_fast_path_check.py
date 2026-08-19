"""Tests for cryo/scanner/fast_path_check.py (RFC Features 2 + 3)."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryo.db.models import AuditLog, Source, Table, TableStats, TableStatsHistory
from cryo.scanner.fast_path_check import (
    COOLDOWN,
    FINDING_MISSING_TARGET,
    FINDING_SMALL_FILES,
    run_fast_path_check,
)


def _add_source(session) -> uuid.UUID:
    source_id = uuid.uuid4()
    session.add(
        Source(
            id=source_id,
            name=f"lake-{source_id}",
            type="rest",
            config={"catalog_name": "iceberg", "type": "rest", "uri": "http://localhost"},
        )
    )
    return source_id


def _add_table(
    session,
    *,
    source_id: uuid.UUID,
    table_name: str,
    first_seen_at: datetime,
    schema_name: str = "analytics",
    is_active: bool = True,
) -> uuid.UUID:
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name=schema_name,
            table_name=table_name,
            first_seen_at=first_seen_at,
            is_active=is_active,
        )
    )
    return table_id


def _add_stats(
    session,
    table_id: uuid.UUID,
    *,
    scan_at: datetime,
    total_files: int,
    small_files_count: int,
    target_file_size_bytes: int | None = 134217728,
) -> None:
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="C",
            overall_score=65,
            total_files=total_files,
            small_files_count=small_files_count,
            total_size_gb=10.0,
            avg_file_size_mb=8.0,
            total_partitions=4,
            partition_skew_ratio=1.0,
            manifest_count=10,
            total_snapshots=3,
            delete_file_pct=0.0,
            total_delete_files=0,
            write_distribution_mode="hash",
            has_sort_order=False,
            target_file_size_bytes=target_file_size_bytes,
            query_count_30d=0,
            raw={
                "iceberg_properties": (
                    {"write.target-file-size-bytes": str(target_file_size_bytes)}
                    if target_file_size_bytes is not None
                    else {}
                )
            },
        )
    )


def _add_history(session, table_id: uuid.UUID, scan_at: datetime) -> None:
    session.add(
        TableStatsHistory(
            table_id=table_id,
            scan_at=scan_at,
            small_files_count=10,
            total_files=20,
            total_size_gb=1.0,
            grade="B",
            overall_score=80,
            raw={},
        )
    )


def test_new_table_high_small_file_ratio_flagged(session) -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    source_id = _add_source(session)
    table_id = _add_table(session, source_id=source_id, table_name="fresh", first_seen_at=now)
    _add_stats(session, table_id, scan_at=now, total_files=100, small_files_count=50)
    session.commit()

    findings = run_fast_path_check(session, now=now)
    types = {f.finding_type for f in findings if f.table_id == table_id}
    assert FINDING_SMALL_FILES in types
    small = next(f for f in findings if f.finding_type == FINDING_SMALL_FILES)
    assert small.metric_value == 0.5
    assert small.table_name == "analytics.fresh"


def test_old_table_with_history_skipped(session) -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=30)
    source_id = _add_source(session)
    table_id = _add_table(session, source_id=source_id, table_name="legacy", first_seen_at=old)
    _add_stats(session, table_id, scan_at=old, total_files=100, small_files_count=80)
    _add_history(session, table_id, old)
    _add_history(session, table_id, old + timedelta(days=1))
    session.commit()

    findings = run_fast_path_check(session, now=now)
    assert all(f.table_id != table_id for f in findings)


def test_missing_target_file_size_without_table_stats(session) -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    source_id = _add_source(session)
    table_id = _add_table(session, source_id=source_id, table_name="unscanned", first_seen_at=now)
    session.commit()

    findings = run_fast_path_check(session, now=now)
    missing = [f for f in findings if f.table_id == table_id]
    assert any(f.finding_type == FINDING_MISSING_TARGET for f in missing)
    assert all(f.finding_type != FINDING_SMALL_FILES for f in missing)


def test_cooldown_skips_duplicate_finding(session) -> None:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    source_id = _add_source(session)
    table_id = _add_table(session, source_id=source_id, table_name="fresh", first_seen_at=now)
    _add_stats(session, table_id, scan_at=now, total_files=100, small_files_count=50)
    session.commit()

    first = run_fast_path_check(session, now=now)
    assert any(f.finding_type == FINDING_SMALL_FILES for f in first)
    audits = list(session.query(AuditLog).filter(AuditLog.table_id == table_id))
    assert audits

    second = run_fast_path_check(session, now=now + timedelta(hours=1))
    assert second == []
    assert session.query(AuditLog).filter(AuditLog.table_id == table_id).count() == len(audits)

    later = run_fast_path_check(session, now=now + COOLDOWN + timedelta(minutes=1))
    assert any(f.finding_type == FINDING_SMALL_FILES for f in later)


def test_never_dispatches_compaction() -> None:
    src = Path("cryo/scanner/fast_path_check.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("cryo.planner")
                assert not alias.name.startswith("cryo.engines")
                assert not alias.name.startswith("cryo.worker")
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("cryo.planner")
            assert not node.module.startswith("cryo.engines")
            assert not node.module.startswith("cryo.worker")
            assert node.module != "cryo.worker.execute"
