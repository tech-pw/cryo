"""Scanner reliability: stable chunk membership (#154), discovery failures are not an empty
success (#162), and the one-shot CLI exits even with an abandoned catalog call (#163)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from cryo.db.models import Source, Table, TableStats
from cryo.scanner import run as scanner_run
from cryo.scanner.run import (
    ScanCandidate,
    ScanSummary,
    order_by_staleness,
    run_scan,
    stable_chunk,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[1]


class _Catalog:
    def __init__(self, tables: list[tuple[str, str]] | None = None) -> None:
        self._tables = tables or []

    def list_tables(self) -> list[tuple[str, str]]:
        return list(self._tables)

    def load_table(self, schema: str, table: str) -> None:  # pragma: no cover - unused
        raise NotImplementedError


class _BrokenCatalog:
    def list_tables(self) -> list[tuple[str, str]]:
        raise ConnectionError("TTransportException: TSocket read 0 bytes")


def _source(session, name: str | None = None) -> Source:
    source = Source(id=uuid4(), name=name or f"src-{uuid4()}", type="rest", config={}, enabled=True)
    session.add(source)
    session.flush()
    return source


def _write_scan(session, source: Source, name: str, at: datetime) -> None:
    table = session.query(Table).filter_by(source_id=source.id, table_name=name).one_or_none()
    if table is None:
        table = Table(id=uuid4(), source_id=source.id, schema_name="s", table_name=name)
        session.add(table)
        session.flush()
    session.merge(
        TableStats(
            table_id=table.id,
            scan_at=at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=1,
            small_files_count=0,
            total_size_gb=1.0,
        )
    )
    session.flush()


# --- #154 ---------------------------------------------------------------------------------


def test_chunks_cover_every_table_once_even_as_earlier_chunks_write_scan_at(session) -> None:
    source = _source(session)
    names = [f"t{i:02d}" for i in range(38)]
    # 27 already scanned at varied ages, 11 new (never scanned): the #154 shape.
    for index, name in enumerate(names[:27]):
        _write_scan(session, source, name, NOW - timedelta(hours=index + 1))
    session.commit()
    candidates = [
        ScanCandidate(source=source, catalog=_Catalog(), schema_name="s", table_name=name)
        for name in names
    ]

    seen: list[str] = []
    for chunk_id in range(4):
        assigned = order_by_staleness(
            session, stable_chunk(candidates, chunk_id=chunk_id, num_chunks=4)
        )
        seen.extend(c.table_name for c in assigned)
        # The chunk "scans" its tables, which moves them to the back of the staleness order.
        for candidate in assigned:
            _write_scan(session, source, candidate.table_name, NOW)
        session.commit()

    assert sorted(seen) == names
    assert len(seen) == len(set(seen))


def test_within_a_chunk_the_stalest_table_goes_first(session) -> None:
    source = _source(session)
    _write_scan(session, source, "fresh", NOW)
    _write_scan(session, source, "old", NOW - timedelta(days=9))
    session.commit()
    candidates = [
        ScanCandidate(source=source, catalog=_Catalog(), schema_name="s", table_name=name)
        for name in ("fresh", "old", "new")
    ]

    ordered = order_by_staleness(session, stable_chunk(candidates, chunk_id=0, num_chunks=1))

    assert [c.table_name for c in ordered] == ["new", "old", "fresh"]


def test_stable_chunk_rejects_bad_bounds() -> None:
    with pytest.raises(ValueError):
        stable_chunk([], chunk_id=0, num_chunks=0)
    with pytest.raises(ValueError):
        stable_chunk([], chunk_id=4, num_chunks=4)


# --- #162 ---------------------------------------------------------------------------------


def test_all_sources_scan_counts_a_failed_discovery(session) -> None:
    broken = _source(session, name="broken-hms")
    session.commit()

    summary = run_scan(
        session,
        source_id=None,
        chunk_id=0,
        num_chunks=1,
        source_factory=lambda source: _BrokenCatalog(),
        parallel_tables=0,
        now=NOW,
    )

    assert summary.discovered == 0
    assert summary.sources_failed == 1
    assert summary.discovery_errors[0].startswith(f"{broken.name}: ")
    assert "TSocket" in summary.discovery_errors[0]


def test_healthy_scan_reports_no_failed_sources(session) -> None:
    _source(session)
    session.commit()

    summary = run_scan(
        session,
        source_id=None,
        chunk_id=0,
        num_chunks=1,
        source_factory=lambda source: _Catalog(),
        parallel_tables=0,
        now=NOW,
    )

    assert summary.sources_failed == 0
    assert summary.discovery_errors == ()


@pytest.mark.parametrize(("failed", "expected"), [(0, 0), (2, 1)])
def test_main_exit_code_reflects_failed_sources(
    monkeypatch: pytest.MonkeyPatch, failed: int, expected: int
) -> None:
    class _Session:
        def __enter__(self) -> _Session:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(scanner_run, "configure_process_logging", lambda: None)
    monkeypatch.setattr(scanner_run, "SessionLocal", _Session)
    monkeypatch.setattr(
        scanner_run, "run_scan", lambda session, **kwargs: ScanSummary(sources_failed=failed)
    )

    assert scanner_run.main(["--chunk-id", "0", "--num-chunks", "1"]) == expected


# --- #163 ---------------------------------------------------------------------------------


def test_cli_exits_even_with_an_abandoned_catalog_call() -> None:
    # Without os._exit, interpreter shutdown joins the scan-analyze worker that is still
    # blocked in the "catalog" call, and this process would hang for the full 600s sleep.
    script = (
        "import time\n"
        "from cryo.scanner import run\n"
        "try:\n"
        "    run._run_bounded(lambda: time.sleep(600), timeout_seconds=0.2, description='hms')\n"
        "except TimeoutError:\n"
        "    pass\n"
        "run._exit_process(3)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=REPO, timeout=60, capture_output=True, text=True
    )
    assert result.returncode == 3, result.stderr


def test_scanner_cronjob_has_a_deadline_and_forbids_overlap() -> None:
    if shutil.which("helm") is None:
        pytest.skip("helm is required for chart rendering")
    rendered = subprocess.run(
        ["helm", "template", "cryo", str(REPO / "deploy/helm/cryo")],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    cronjob = next(
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc["kind"] == "CronJob" and doc["metadata"]["name"] == "cryo-scanner"
    )
    assert cronjob["spec"]["concurrencyPolicy"] == "Forbid"
    assert cronjob["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"] == 19800
