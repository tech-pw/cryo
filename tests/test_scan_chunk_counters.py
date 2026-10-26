"""Regression tests for scan chunk claim + scanned/chunk counter accounting.

These cover the failure mode where a chunk could be claimed twice
(Redis + DB fallback), progress counters accumulated across attempts, and the
run stayed at 100% with chunks_remaining=1 while scanned exceeded discovered.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from cryo.db.models import MaintenanceRun
from cryo.db.repositories import scanner as scanner_repo
from tests.conftest import (
    _assign_missing_autoincrement_primary_keys,
    _assign_missing_uuid_primary_keys,
)


def _bound_session_factory(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    event.listen(factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    return factory


def _make_queued_scan(session, *, num_chunks: int = 4) -> MaintenanceRun:
    run = MaintenanceRun(
        id=uuid4(),
        kind="scan",
        status="queued",
        trigger="test",
        summary={
            "scope": "source",
            "source_id": str(uuid4()),
            "full_scan": True,
            "cost_full_scan": False,
        },
    )
    session.add(run)
    session.commit()
    scanner_repo.initialize_scan_chunks(session, run.id, num_chunks=num_chunks, queued=True)
    session.refresh(run)
    return run


def test_try_claim_resets_counters_and_rejects_double_claim(session) -> None:
    run = _make_queued_scan(session, num_chunks=2)
    factory = _bound_session_factory(session)

    assert scanner_repo.try_claim_scan_chunk(factory, run.id, chunk_id=0, num_chunks=2) is True
    session.refresh(run)
    chunk = run.summary["chunks"]["0"]
    assert chunk["status"] == "running"
    assert chunk["scanned"] == 0
    assert run.summary["chunks_running"] == 1
    assert run.summary["chunks_remaining"] == 2  # 1 running + 1 pending

    # Second owner must be refused while the chunk is still running.
    assert scanner_repo.try_claim_scan_chunk(factory, run.id, chunk_id=0, num_chunks=2) is False
    session.refresh(run)
    assert run.summary["chunks"]["0"]["status"] == "running"


def test_progress_ignored_after_chunk_finished_and_capped_at_assigned(session) -> None:
    run = _make_queued_scan(session, num_chunks=1)
    factory = _bound_session_factory(session)
    assert scanner_repo.try_claim_scan_chunk(factory, run.id, chunk_id=0, num_chunks=1)

    scanner_repo.record_scan_progress(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=1,
        event={"event": "assigned", "discovered": 10, "assigned": 3},
    )
    for index in range(5):
        scanner_repo.record_scan_progress(
            factory,
            run.id,
            chunk_id=0,
            num_chunks=1,
            event={"event": "table_result", "outcome": "scanned", "table_ref": f"t.{index}"},
        )
    session.refresh(run)
    assert run.summary["chunks"]["0"]["scanned"] == 3
    assert run.summary["scanned"] == 3

    scanner_repo.record_scan_chunk_finished(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=1,
        status="succeeded",
        summary_update={"discovered": 10, "assigned": 3, "scanned": 3, "skipped": 0, "failed": 0},
    )
    # Late duplicate worker must not inflate counters.
    scanner_repo.record_scan_progress(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=1,
        event={"event": "table_result", "outcome": "scanned", "table_ref": "late.table"},
    )
    session.refresh(run)
    assert run.summary["chunks"]["0"]["status"] == "succeeded"
    assert run.summary["scanned"] == 3
    assert run.summary["chunks_remaining"] == 0


def test_finished_trims_over_reported_counters(session) -> None:
    run = _make_queued_scan(session, num_chunks=1)
    factory = _bound_session_factory(session)
    assert scanner_repo.try_claim_scan_chunk(factory, run.id, chunk_id=0, num_chunks=1)

    scanner_repo.record_scan_chunk_finished(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=1,
        status="partial_failed",
        summary_update={
            "discovered": 100,
            "assigned": 25,
            "scanned": 40,
            "skipped": 0,
            "failed": 5,
        },
    )
    session.refresh(run)
    chunk = run.summary["chunks"]["0"]
    assert chunk["assigned"] == 25
    assert chunk["scanned"] + chunk["skipped"] + chunk["failed"] == 25
    assert run.summary["scanned"] + run.summary["failed"] == 25


def test_unreadable_tables_roll_up_apart_from_failures(session) -> None:
    """A table whose metadata is gone from object storage must not land in the failure count
    or the run's error surface -- a retry produces the identical error, so reporting it as a
    scan failure makes the failure rate a number that can never improve."""
    run = _make_queued_scan(session, num_chunks=1)
    factory = _bound_session_factory(session)
    assert scanner_repo.try_claim_scan_chunk(factory, run.id, chunk_id=0, num_chunks=1)

    scanner_repo.record_scan_progress(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=1,
        event={"event": "assigned", "discovered": 3, "assigned": 3},
    )
    for outcome, table_ref in (
        ("scanned", "s.healthy"),
        ("unreadable", "s.gold_polls_evolving"),
        ("failed", "s.transient"),
    ):
        scanner_repo.record_scan_progress(
            factory,
            run.id,
            chunk_id=0,
            num_chunks=1,
            event={
                "event": "table_result",
                "outcome": outcome,
                "table_ref": table_ref,
                "error": None if outcome == "scanned" else f"{outcome} error",
            },
        )

    session.refresh(run)
    summary = run.summary
    assert (summary["scanned"], summary["failed"], summary["unreadable"]) == (1, 1, 1)
    assert [entry["table"] for entry in summary["failed_tables"]] == ["s.transient"]
    assert [entry["table"] for entry in summary["unreadable_tables"]] == ["s.gold_polls_evolving"]
    assert summary["last_error"] == "failed error"


def test_a_run_of_only_unreadable_tables_succeeds(session) -> None:
    """385 of demo.events's 585 tables scan fine and 201 have lost their manifests. The run
    reports what it accomplished; the unreadable list is the thing to act on."""
    run = _make_queued_scan(session, num_chunks=1)
    factory = _bound_session_factory(session)
    assert scanner_repo.try_claim_scan_chunk(factory, run.id, chunk_id=0, num_chunks=1)

    scanner_repo.record_scan_chunk_finished(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=1,
        status="succeeded",
        summary_update={
            "discovered": 585,
            "assigned": 585,
            "scanned": 384,
            "skipped": 0,
            "failed": 0,
            "unreadable": 201,
        },
    )
    session.refresh(run)
    assert run.status == "succeeded"
    assert run.summary["unreadable"] == 201
    assert run.summary["failed"] == 0


def test_finished_trims_unreadable_within_assigned(session) -> None:
    run = _make_queued_scan(session, num_chunks=1)
    factory = _bound_session_factory(session)
    assert scanner_repo.try_claim_scan_chunk(factory, run.id, chunk_id=0, num_chunks=1)

    scanner_repo.record_scan_chunk_finished(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=1,
        status="succeeded",
        summary_update={
            "discovered": 20,
            "assigned": 10,
            "scanned": 8,
            "skipped": 0,
            "failed": 0,
            "unreadable": 8,
        },
    )
    session.refresh(run)
    chunk = run.summary["chunks"]["0"]
    outcomes = chunk["scanned"] + chunk["skipped"] + chunk["failed"] + chunk["unreadable"]
    assert outcomes == 10


def test_chunk_summary_does_not_invent_phantom_chunks(session) -> None:
    run = _make_queued_scan(session, num_chunks=2)
    factory = _bound_session_factory(session)
    assert scanner_repo.try_claim_scan_chunk(factory, run.id, chunk_id=0, num_chunks=2)

    # Wrong num_chunks from a stale payload must not create chunk "2" as queued.
    scanner_repo.record_scan_progress(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=99,
        event={"event": "assigned", "discovered": 4, "assigned": 2},
    )
    session.refresh(run)
    assert run.summary["chunks_total"] == 2
    assert "2" not in run.summary["chunks"]
    assert run.summary["chunks_remaining"] == 2


def test_cancel_clears_running_and_pending_chunk_counters(session) -> None:
    run = _make_queued_scan(session, num_chunks=3)
    factory = _bound_session_factory(session)
    assert scanner_repo.try_claim_scan_chunk(factory, run.id, chunk_id=0, num_chunks=3)
    scanner_repo.record_scan_progress(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=3,
        event={"event": "assigned", "discovered": 9, "assigned": 3},
    )
    scanner_repo.record_scan_progress(
        factory,
        run.id,
        chunk_id=0,
        num_chunks=3,
        event={"event": "table_result", "outcome": "scanned", "table_ref": "a.b"},
    )

    scanner_repo.request_scan_cancel(session, run)
    session.commit()
    session.refresh(run)

    assert run.status == "cancelled"
    assert run.summary["chunks_running"] == 0
    assert run.summary["chunks_pending"] == 0
    assert run.summary["chunks_remaining"] == 0
    assert all(chunk["status"] == "cancelled" for chunk in run.summary["chunks"].values())


def test_claim_next_stranded_is_exclusive(session) -> None:
    run = _make_queued_scan(session, num_chunks=2)
    factory = _bound_session_factory(session)

    first = scanner_repo.claim_next_stranded_scan_chunk(factory)
    second = scanner_repo.claim_next_stranded_scan_chunk(factory)
    third = scanner_repo.claim_next_stranded_scan_chunk(factory)

    assert first is not None and first["chunk_id"] == 0 and first["_already_claimed"] is True
    assert second is not None and second["chunk_id"] == 1
    assert third is None

    session.refresh(run)
    assert run.summary["chunks"]["0"]["status"] == "running"
    assert run.summary["chunks"]["1"]["status"] == "running"
    assert run.summary["chunks_remaining"] == 2
