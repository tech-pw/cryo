"""Tests for Governor-driven scan Job admission (cryo/governor/scan_admit.py)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.orm import Session

from cryo.db.models import Source, Table, TableStats
from cryo.engines.native.job_dispatch import JobCreateResult
from cryo.governor.scan_admit import admit_scan_jobs, scan_job_dispatch_enabled
from cryo.queue.scan_queue import InMemoryScanQueue


def _make_table_with_stats(session: Session, **stats_overrides: object) -> Table:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="finance",
        table_name="orders",
        is_active=True,
    )
    session.add(table)
    session.flush()
    defaults: dict[str, object] = dict(
        table_id=table.id,
        scan_at=datetime.now(UTC),
        needs_compaction=False,
        compaction_priority="LOW",
        grade="A",
        overall_score=90,
        total_files=100,
        small_files_count=5,
        total_size_gb=10.0,
        manifest_count=5,
        max_files_per_partition=20,
    )
    defaults.update(stats_overrides)
    session.add(TableStats(**defaults))
    session.flush()
    return table


def _fake_create_scan_job_ok(payload, **kwargs):
    return JobCreateResult(
        ok=True, job_name="cryo-scan-test", cpu_milli=kwargs["cpu_milli"], mem_mib=kwargs["mem_mib"]
    )


def test_scan_job_dispatch_enabled_reads_env(monkeypatch) -> None:
    monkeypatch.delenv("CRYO_SCANNER_JOB_DISPATCH_ENABLED", raising=False)
    assert scan_job_dispatch_enabled() is False
    monkeypatch.setenv("CRYO_SCANNER_JOB_DISPATCH_ENABLED", "true")
    assert scan_job_dispatch_enabled() is True


def test_scan_job_dispatch_enabled_config_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("CRYO_SCANNER_JOB_DISPATCH_ENABLED", "true")
    assert scan_job_dispatch_enabled({"scanner_job_dispatch_enabled": False}) is False


def test_admit_scan_jobs_empty_queue_returns_empty(session: Session) -> None:
    queue = InMemoryScanQueue()
    result = admit_scan_jobs(
        queue=queue,
        session=session,
        session_factory=lambda: session,
    )
    assert result.admitted == 0
    assert result.empty is True


def test_admit_scan_jobs_sizes_from_prior_table_stats(session: Session) -> None:
    table = _make_table_with_stats(session, total_files=500_000, manifest_count=50_000)
    queue = InMemoryScanQueue()
    queue.enqueue("run-1", {"table_ids": [str(table.id)], "chunk_id": 0, "num_chunks": 1})

    captured: dict = {}

    def _capture(payload, **kwargs):
        captured.update(kwargs)
        return JobCreateResult(
            ok=True,
            job_name="cryo-scan-test",
            cpu_milli=kwargs["cpu_milli"],
            mem_mib=kwargs["mem_mib"],
        )

    with patch("cryo.governor.scan_admit.create_scan_job", side_effect=_capture):
        result = admit_scan_jobs(
            queue=queue,
            session=session,
            session_factory=lambda: session,
        )

    assert result.admitted == 1
    # A table with 500k files/50k manifests should size well above the 512Mi floor.
    assert captured["mem_mib"] > 512


def test_admit_scan_jobs_falls_back_to_cold_estimate_without_prior_stats(session: Session) -> None:
    queue = InMemoryScanQueue()
    queue.enqueue("run-1", {"table_ids": [], "chunk_id": 0, "num_chunks": 1})

    with patch("cryo.governor.scan_admit.create_scan_job", side_effect=_fake_create_scan_job_ok):
        result = admit_scan_jobs(
            queue=queue,
            session=session,
            session_factory=lambda: session,
        )

    assert result.admitted == 1


def test_admit_scan_jobs_respects_max_dispatch(session: Session) -> None:
    queue = InMemoryScanQueue()
    for i in range(3):
        queue.enqueue(f"run-{i}", {"table_ids": [], "chunk_id": 0, "num_chunks": 1})

    with patch("cryo.governor.scan_admit.create_scan_job", side_effect=_fake_create_scan_job_ok):
        result = admit_scan_jobs(
            queue=queue,
            session=session,
            session_factory=lambda: session,
            max_dispatch=2,
        )

    assert result.admitted == 2
    assert queue.pending_count() == 1


def test_admit_scan_jobs_continues_after_dispatch_failure(session: Session) -> None:
    queue = InMemoryScanQueue()
    queue.enqueue("run-1", {"table_ids": [], "chunk_id": 0, "num_chunks": 1})
    queue.enqueue("run-2", {"table_ids": [], "chunk_id": 0, "num_chunks": 1})

    results = [
        JobCreateResult(ok=False, job_name=None, cpu_milli=250, mem_mib=512, error="boom"),
        JobCreateResult(ok=True, job_name="cryo-scan-ok", cpu_milli=250, mem_mib=512),
    ]

    with patch("cryo.governor.scan_admit.create_scan_job", side_effect=results):
        result = admit_scan_jobs(
            queue=queue,
            session=session,
            session_factory=lambda: session,
            max_dispatch=2,
        )

    assert result.admitted == 1
