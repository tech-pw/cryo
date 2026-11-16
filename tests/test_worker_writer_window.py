"""The worker's writer-window gate before data rewrites (2026-11-15 ingestion failures)."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from cryo.domain.enums import JobStatus, OperationType
from cryo.governor.governor import _still_blocked_reason
from cryo.planner.writer_window import WRITER_WINDOW_MARKER, WriterCommit, hold_retry_at
from cryo.queue.locks import InMemoryLockStore
from cryo.worker.worker import FakeMaintenanceEngine, Worker
from tests.test_worker import _worker_fixture

MIN = 60_000


class _Reader:
    def __init__(self, commits: list[WriterCommit] | Exception) -> None:
        self.commits = commits
        self.calls: list[Mapping[str, Any]] = []

    def __call__(self, stats: Mapping[str, Any]) -> list[WriterCommit]:
        self.calls.append(stats)
        if isinstance(self.commits, Exception):
            raise self.commits
        return self.commits


def _writer(*, every_min: int, last_ago_ms: int, runs: int = 12) -> list[WriterCommit]:
    last = int(time.time() * 1000) - last_ago_ms
    return [WriterCommit(last - i * every_min * MIN, "overwrite") for i in range(runs)]


def _executed(engine: FakeMaintenanceEngine) -> list[OperationType]:
    return [statement.action for statement in engine.executed]


def _run(reader: _Reader | None, *, params: dict[str, Any] | None = None):
    queue, locks, store = _worker_fixture()
    if params is not None:
        store.jobs["job-1"].params = {**store.jobs["job-1"].params, **params}
    engine = FakeMaintenanceEngine()
    result = Worker(
        queue, store, lambda _d: engine, locks, writer_commit_reader=reader
    ).process_next()
    assert result is not None
    return result, engine, store, locks


def test_a_writer_about_to_commit_defers_the_rewrite_and_releases_the_lock() -> None:
    reader = _Reader(_writer(every_min=60, last_ago_ms=50 * MIN))

    result, engine, store, locks = _run(reader)

    assert result.status is JobStatus.DEFERRED
    assert (result.error or "").startswith(f"{WRITER_WINDOW_MARKER}: ")
    retry_at = hold_retry_at(result.error)
    assert retry_at is not None and retry_at > datetime.now(UTC)
    assert OperationType.OPTIMIZE not in _executed(engine)
    assert not locks.is_table_locked("table-1")
    assert reader.calls and reader.calls[0]["table_name"] == "events"


def test_a_rewrite_starts_in_the_quiet_gap_after_a_writer_commit() -> None:
    result, engine, _store, _locks = _run(_Reader(_writer(every_min=60, last_ago_ms=5 * MIN)))

    assert result.status is JobStatus.SUCCEEDED
    assert OperationType.OPTIMIZE in _executed(engine)


def test_a_writer_with_no_gap_skips_the_rewrite_but_not_the_rest_of_the_plan() -> None:
    reader = _Reader(_writer(every_min=15, last_ago_ms=3 * MIN, runs=40))

    result, engine, store, _locks = _run(reader, params={"retention_days": 0})

    assert _executed(engine) == [OperationType.EXPIRE_SNAPSHOTS]
    skipped = [r for r in store.audit if r.action is OperationType.OPTIMIZE]
    assert [r.status for r in skipped] == ["SKIPPED"]
    assert (skipped[0].detail or "").startswith(WRITER_WINDOW_MARKER)
    assert result.status is JobStatus.PARTIAL
    assert (result.error or "").startswith("optimize: writer_window: writer commits every ~15 min")


def test_an_unreadable_catalog_fails_open() -> None:
    result, engine, _store, _locks = _run(_Reader(TimeoutError("load_table exceeded 10s")))

    assert result.status is JobStatus.SUCCEEDED
    assert OperationType.OPTIMIZE in _executed(engine)


def test_the_preset_can_turn_the_gate_off() -> None:
    reader = _Reader(_writer(every_min=60, last_ago_ms=50 * MIN))

    result, engine, _store, _locks = _run(reader, params={"writer_window": False})

    assert result.status is JobStatus.SUCCEEDED
    assert OperationType.OPTIMIZE in _executed(engine)


def test_without_a_reader_the_worker_behaves_as_before() -> None:
    result, engine, _store, _locks = _run(None)

    assert result.status is JobStatus.SUCCEEDED
    assert OperationType.OPTIMIZE in _executed(engine)


def test_operations_that_remove_no_data_files_are_not_gated() -> None:
    reader = _Reader(_writer(every_min=60, last_ago_ms=50 * MIN))

    result, engine, _store, _locks = _run(
        reader, params={"requested_operations": ["expire_snapshots"], "retention_days": 0}
    )

    assert OperationType.OPTIMIZE not in _executed(engine)
    assert reader.calls == []
    assert result.status is not JobStatus.DEFERRED


def test_the_worker_rewrites_only_files_below_the_scanner_threshold() -> None:
    _result, _engine, store, _locks = _run(None)

    optimize = next(op for op in store.jobs["job-1"].plan["operations"] if op["type"] == "optimize")
    assert optimize["params"]["min_file_size_mb"] == 89.6


def test_a_preset_min_file_size_still_wins() -> None:
    _result, _engine, store, _locks = _run(None, params={"min_file_size_mb": 32})

    optimize = next(op for op in store.jobs["job-1"].plan["operations"] if op["type"] == "optimize")
    assert optimize["params"]["min_file_size_mb"] == 32


def test_the_governor_keeps_a_writer_hold_deferred_until_retry_at() -> None:
    reader = _Reader(_writer(every_min=60, last_ago_ms=50 * MIN))
    result, _engine, _store, _locks = _run(reader)
    retry_at = hold_retry_at(result.error)
    assert retry_at is not None
    blocked = _still_blocked_reason(None, InMemoryLockStore(), now=retry_at - timedelta(minutes=1))
    released = _still_blocked_reason(None, InMemoryLockStore(), now=retry_at + timedelta(seconds=1))

    assert blocked(result) == result.error
    assert released(result) is None
