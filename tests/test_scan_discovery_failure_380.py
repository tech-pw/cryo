"""#380: a fleet scan whose catalog discovery fails is not an empty success."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from cryo.config import get_settings
from cryo.db.models import MaintenanceRun, Source
from cryo.db.repositories import scanner as scanner_repo
from cryo.queue.run_events import scan_progress_message
from cryo.scanner import run as scanner_run
from cryo.scanner import worker as scanner_worker
from cryo.scanner.run import ScanSummary, _run_bounded, run_scan
from cryo.scanner.worker import ScanWorker
from tests.conftest import (
    _assign_missing_autoincrement_primary_keys,
    _assign_missing_uuid_primary_keys,
)

NOW = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)


class _Catalog:
    def __init__(self, tables: list[tuple[str, str]], *, delay: float = 0.0) -> None:
        self._tables = tables
        self._delay = delay

    def list_tables(self) -> list[tuple[str, str]]:
        time.sleep(self._delay)
        return list(self._tables)


class _BrokenCatalog:
    def list_tables(self) -> list[tuple[str, str]]:
        raise ConnectionError("TTransportException: TSocket read 0 bytes")


class _OneShotQueue:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload: dict[str, Any] | None = payload

    def claim(self) -> dict[str, Any] | None:
        payload, self._payload = self._payload, None
        return payload


def _factory(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    event.listen(factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    return factory


def _source(session, name: str) -> Source:
    source = Source(id=uuid4(), name=name, type="hive", config={}, enabled=True)
    session.add(source)
    session.commit()
    return source


def _fleet_run(session, *, num_chunks: int = 1) -> MaintenanceRun:
    run = MaintenanceRun(
        id=uuid4(),
        kind="scan",
        status="queued",
        trigger="schedule",
        summary={"scope": "all", "full_scan": True, "cost_full_scan": False},
    )
    session.add(run)
    session.commit()
    scanner_repo.initialize_scan_chunks(session, run.id, num_chunks=num_chunks, queued=True)
    return run


def _process(session, run: MaintenanceRun, *, chunk_id: int = 0, num_chunks: int = 1):
    worker = ScanWorker(
        _OneShotQueue({"run_id": str(run.id), "chunk_id": chunk_id, "num_chunks": num_chunks}),
        session_factory=_factory(session),
    )
    result = worker.process_next()
    session.expire_all()
    return result, session.get(MaintenanceRun, run.id)


def test_scan_progress_calls_the_heartbeat_per_table_event(session, monkeypatch) -> None:
    # #432: every per-table event resets the scanner-worker daemon's stall clock.
    run = _fleet_run(session)
    beats: list[str] = []

    def scan(session, *, progress_callback, **kwargs):
        for name in ("a", "b", "c"):
            ref = f"s.{name}"
            progress_callback({"event": "start_table", "table_ref": ref})
            progress_callback({"event": "table_result", "table_ref": ref, "outcome": "scanned"})
        return ScanSummary(discovered=3, assigned=3, scanned=3)

    monkeypatch.setattr(scanner_worker, "run_scan", scan)
    worker = ScanWorker(
        _OneShotQueue({"run_id": str(run.id), "chunk_id": 0, "num_chunks": 1}),
        session_factory=_factory(session),
        heartbeat=lambda: beats.append("beat"),
    )
    result = worker.process_next()

    assert result is not None
    assert len(beats) == 6


def test_fleet_chunk_that_discovers_nothing_because_listing_failed_is_failed(
    session, monkeypatch
) -> None:
    broken = _source(session, "broken-hms")
    run = _fleet_run(session)

    def scan(session, **kwargs):
        return run_scan(
            session,
            **kwargs,
            source_factory=lambda _source: _BrokenCatalog(),
            parallel_tables=0,
            now=NOW,
        )

    monkeypatch.setattr(scanner_worker, "run_scan", scan)

    result, refreshed = _process(session, run)

    assert result is not None and result.status == "failed"
    assert refreshed.status == "failed"
    chunk = refreshed.summary["chunks"]["0"]
    assert chunk["status"] == "failed"
    assert chunk["discovered"] == 0
    assert f"{broken.name}: " in chunk["error"]
    assert "TSocket" in refreshed.summary["last_error"]


def test_fleet_chunk_with_some_sources_failed_is_partial(session, monkeypatch) -> None:
    run = _fleet_run(session)
    monkeypatch.setattr(
        scanner_worker,
        "run_scan",
        lambda session, **kwargs: ScanSummary(
            discovered=4,
            assigned=4,
            scanned=4,
            sources_failed=1,
            discovery_errors=("other-hms: discover tables(other-hms) exceeded 1800s",),
        ),
    )

    result, refreshed = _process(session, run)

    assert result is not None and result.status == "partial_failed"
    assert refreshed.status == "partial_failed"
    assert refreshed.summary["chunks"]["0"]["status"] == "partial_failed"
    assert refreshed.summary["failed"] == 0
    assert "other-hms" in refreshed.summary["last_error"]


def test_clean_fleet_chunk_still_succeeds(session, monkeypatch) -> None:
    run = _fleet_run(session)
    monkeypatch.setattr(
        scanner_worker,
        "run_scan",
        lambda session, **kwargs: ScanSummary(discovered=4, assigned=4, scanned=4),
    )

    result, refreshed = _process(session, run)

    assert result is not None and result.status == "succeeded"
    assert refreshed.status == "succeeded"
    assert "last_error" not in refreshed.summary


def test_run_with_a_recorded_error_is_not_reported_succeeded(session) -> None:
    """A table failure recorded by an attempt that was later re-queued leaves last_error behind
    after the retry resets the chunk counters. The run must not then finish as succeeded."""
    run = _fleet_run(session)
    factory = _factory(session)
    assert scanner_repo.try_claim_scan_chunk(factory, run.id, chunk_id=0, num_chunks=1)
    scanner_repo.record_scan_progress(
        factory, run.id, chunk_id=0, num_chunks=1, event={"event": "assigned", "assigned": 2}
    )
    scanner_repo.record_scan_progress(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=1,
        event={"event": "table_result", "table_ref": "a.b", "outcome": "failed", "error": "x"},
    )
    scanner_repo.mark_chunk_requeued(factory, run.id, chunk_id=0, num_chunks=1)
    assert scanner_repo.try_claim_scan_chunk(factory, run.id, chunk_id=0, num_chunks=1)
    scanner_repo.record_scan_chunk_finished(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=1,
        status="succeeded",
        summary_update={"discovered": 2, "assigned": 2, "scanned": 2, "skipped": 0, "failed": 0},
    )

    session.expire_all()
    refreshed = session.get(MaintenanceRun, run.id)
    assert refreshed.summary["last_error"] == "x"
    assert refreshed.status == "partial_failed"


def test_discovery_has_its_own_timeout(session) -> None:
    """Listing took longer than one table's scan budget; it used to be cut off at that."""
    _source(session, "slow-hms")
    settings = get_settings().model_copy(
        update={"scan_table_timeout_seconds": 0.05, "scan_discovery_timeout_seconds": 5.0}
    )

    discovered: list[int] = []

    def factory(_source: Source) -> _Catalog:
        return _Catalog([("s", "t")], delay=0.3)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scanner_run, "get_settings", lambda: settings)
        patch.setattr(scanner_run, "_run_assigned", lambda *_a, **k: ScanSummary())
        summary = run_scan(
            session,
            settings=settings,
            chunk_id=0,
            num_chunks=1,
            source_factory=factory,
            parallel_tables=0,
            progress_callback=lambda e: discovered.append(int(e.get("discovered") or 0)),
            now=NOW,
        )

    assert summary.discovery_errors == ()
    assert summary.sources_failed == 0
    assert max(discovered) == 1


def test_run_scan_bounds_discovery_by_the_discovery_setting(session) -> None:
    _source(session, "hung-hms")
    settings = get_settings().model_copy(
        update={"scan_table_timeout_seconds": 60.0, "scan_discovery_timeout_seconds": 0.1}
    )

    summary = run_scan(
        session,
        settings=settings,
        chunk_id=0,
        num_chunks=1,
        source_factory=lambda _source: _Catalog([("s", "t")], delay=1.0),
        parallel_tables=0,
        now=NOW,
    )

    assert summary.discovered == 0
    assert summary.sources_failed == 1
    assert "discover tables(hung-hms) exceeded" in summary.discovery_errors[0]


def test_slow_discovery_heartbeats_the_chunk(session) -> None:
    _source(session, "slow-hms")
    settings = get_settings().model_copy(update={"scan_discovery_timeout_seconds": 5.0})
    events: list[dict[str, Any]] = []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scanner_run, "_DISCOVERY_HEARTBEAT_SECONDS", 0.05)
        run_scan(
            session,
            settings=settings,
            chunk_id=0,
            num_chunks=1,
            source_factory=lambda _source: _Catalog([], delay=0.3),
            parallel_tables=0,
            progress_callback=events.append,
            now=NOW,
        )

    beats = [e for e in events if e["event"] == "discovering"]
    assert len(beats) >= 2
    assert beats[0]["source"] == "slow-hms"
    assert scan_progress_message(beats[0], chunk_id=0, num_chunks=1).startswith(
        "Still listing tables in slow-hms"
    )


def test_heartbeat_does_not_extend_the_deadline() -> None:
    beats: list[float] = []
    started = time.monotonic()

    with pytest.raises(TimeoutError, match="exceeded"):
        _run_bounded(
            lambda: time.sleep(2),
            timeout_seconds=0.25,
            description="hms",
            heartbeat=beats.append,
            heartbeat_interval_seconds=0.05,
        )

    assert time.monotonic() - started < 1.0
    assert len(beats) >= 2


def test_a_timeout_raised_by_the_call_itself_is_not_waited_out() -> None:
    def fails() -> None:
        raise TimeoutError("socket read timed out")

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="socket read timed out"):
        _run_bounded(
            fails,
            timeout_seconds=5,
            description="hms",
            heartbeat=lambda _e: None,
            heartbeat_interval_seconds=0.05,
        )
    assert time.monotonic() - started < 1.0
