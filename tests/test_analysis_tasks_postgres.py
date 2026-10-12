"""Real-Postgres tests for cryo/db/repositories/analysis_tasks.py.

Recovery plan §3, 2026-09-20: this module's entire purpose is compare-and-set semantics
(partial unique index, `with_for_update(skip_locked=True)`) that Postgres enforces and SQLite
does not -- the standard `session` fixture (tests/conftest.py) builds an in-memory SQLite
schema via `Base.metadata.create_all()`, which silently drops `postgresql_where` partial
indexes entirely. These tests run against the real local Postgres (same instance
`deploy/deploy.sh`'s test gate and this session's manual incident investigation used) rather
than mock out the exact behavior under test.

Requires a local Postgres with the analysis_tasks migration applied (`alembic upgrade head`) --
skips cleanly if unreachable rather than failing the whole suite in an environment without one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from cryo.db.repositories.analysis_tasks import (
    LeaseConflict,
    claim_due_task,
    commit_progress,
    ensure_task_due,
    finish_task,
    get_active_task,
    heartbeat,
    start_new_generation,
    yield_task,
)
from cryo.db.session import SessionLocal


def _postgres_available() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(), reason="requires a reachable local Postgres with migrations applied"
)


@pytest.fixture(autouse=True)
def _clean_analysis_tasks():
    """Isolate each test's rows by task_type suffix instead of truncating the whole table --
    safer if this is ever pointed at a shared dev DB with other rows already present."""
    yield
    with SessionLocal() as session:
        session.execute(text("DELETE FROM cryo.analysis_tasks WHERE task_type LIKE 'test_%'"))
        session.commit()


def _unique_type() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


def test_ensure_task_due_is_idempotent_for_the_same_scope():
    task_type = _unique_type()
    with SessionLocal() as session:
        first = ensure_task_due(session, task_type=task_type, scope="fleet")
        second = ensure_task_due(session, task_type=task_type, scope="fleet")
        assert first.id == second.id


def test_ensure_task_due_allows_a_new_row_after_the_prior_one_is_terminal():
    """A terminal (succeeded/failed/cancelled) row must not block the next due task for the
    same (task_type, scope) -- only pending/leased/running rows count as 'active' under the
    partial unique index."""
    task_type = _unique_type()
    with SessionLocal() as session:
        first = ensure_task_due(session, task_type=task_type, scope="fleet")
        claimed = claim_due_task(session, task_type=task_type, owner="test-owner")
        assert claimed is not None
        finish_task(
            session, first.id, lease_generation=claimed.lease_generation, status="succeeded"
        )

        second = ensure_task_due(session, task_type=task_type, scope="fleet")
        assert second.id != first.id


def test_claim_due_task_returns_none_when_nothing_is_due_yet():
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(
            session,
            task_type=task_type,
            scope="fleet",
            due_at=datetime.now(UTC) + timedelta(hours=1),
        )
        claimed = claim_due_task(session, task_type=task_type, owner="test-owner")
        assert claimed is None


def test_claim_due_task_returns_none_when_already_leased_and_not_expired():
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(session, task_type=task_type, scope="fleet")
        first_claim = claim_due_task(
            session, task_type=task_type, owner="owner-a", lease_seconds=300
        )
        assert first_claim is not None

        second_claim = claim_due_task(session, task_type=task_type, owner="owner-b")
        assert second_claim is None


def test_claim_due_task_reclaims_after_lease_expiry():
    """The actual crash-recovery property: a lease that expired (owner died without heartbeat
    or finish) must become claimable again by a different owner."""
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(session, task_type=task_type, scope="fleet")
        first_claim = claim_due_task(session, task_type=task_type, owner="owner-a", lease_seconds=1)
        assert first_claim is not None

        # Simulate lease expiry via the `now` parameter rather than sleeping in a test.
        later = datetime.now(UTC) + timedelta(seconds=5)
        second_claim = claim_due_task(session, task_type=task_type, owner="owner-b", now=later)
        assert second_claim is not None
        assert second_claim.id == first_claim.id
        assert second_claim.lease_generation == first_claim.lease_generation + 1


def test_commit_progress_advances_cursor_and_processed_count():
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(session, task_type=task_type, scope="fleet")
        claimed = claim_due_task(session, task_type=task_type, owner="owner-a")
        assert claimed is not None

        commit_progress(
            session,
            claimed.id,
            lease_generation=claimed.lease_generation,
            cursor="table-42",
            processed_delta=17,
        )
        task = get_active_task(session, task_type=task_type, scope="fleet")
        assert task is not None
        assert task.cursor == "table-42"
        assert task.processed_count == 17
        assert task.status == "running"


def test_commit_progress_raises_lease_conflict_when_superseded():
    """The core safety property: a stale attempt (lost its lease to a reclaim) must not be
    able to silently keep writing progress for the current generation."""
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(session, task_type=task_type, scope="fleet")
        stale_claim = claim_due_task(session, task_type=task_type, owner="owner-a", lease_seconds=1)
        assert stale_claim is not None

        later = datetime.now(UTC) + timedelta(seconds=5)
        fresh_claim = claim_due_task(session, task_type=task_type, owner="owner-b", now=later)
        assert fresh_claim is not None

        with pytest.raises(LeaseConflict):
            commit_progress(
                session,
                stale_claim.id,
                lease_generation=stale_claim.lease_generation,
                cursor="table-1",
                processed_delta=1,
            )


def test_heartbeat_returns_none_when_superseded_rather_than_raising():
    """Heartbeat losing its lease is expected steady-state (someone else reclaimed it), not
    exceptional -- must not raise the way commit_progress/finish_task do for a write."""
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(session, task_type=task_type, scope="fleet")
        stale_claim = claim_due_task(session, task_type=task_type, owner="owner-a", lease_seconds=1)
        assert stale_claim is not None
        later = datetime.now(UTC) + timedelta(seconds=5)
        claim_due_task(session, task_type=task_type, owner="owner-b", now=later)

        result = heartbeat(session, stale_claim.id, lease_generation=stale_claim.lease_generation)
        assert result is None


def test_heartbeat_advances_lease_generation_so_a_duplicate_call_is_rejected():
    """Review finding F04: a successful heartbeat must also advance the fence -- otherwise a
    second call presenting the same (already-used) lease_generation would incorrectly succeed
    again instead of being rejected as stale."""
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(session, task_type=task_type, scope="fleet")
        claimed = claim_due_task(session, task_type=task_type, owner="owner-a")
        assert claimed is not None

        new_generation = heartbeat(session, claimed.id, lease_generation=claimed.lease_generation)
        assert new_generation is not None
        assert new_generation > claimed.lease_generation

        # Reusing the now-stale original token must fail.
        stale_retry = heartbeat(session, claimed.id, lease_generation=claimed.lease_generation)
        assert stale_retry is None

        # The new token works.
        assert heartbeat(session, claimed.id, lease_generation=new_generation) is not None


def test_finish_task_requires_terminal_status():
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(session, task_type=task_type, scope="fleet")
        claimed = claim_due_task(session, task_type=task_type, owner="owner-a")
        assert claimed is not None
        with pytest.raises(ValueError):
            finish_task(
                session, claimed.id, lease_generation=claimed.lease_generation, status="running"
            )


def test_start_new_generation_bumps_generation_and_invalidates_the_old_lease():
    """Recovery plan §3's 'never mix generations silently': a still-in-flight child from the
    old generation must be detected and rejected the next time it tries to commit_progress."""
    task_type = _unique_type()
    with SessionLocal() as session:
        task = ensure_task_due(
            session, task_type=task_type, scope="fleet", input_watermark="scan-v1"
        )
        old_claim = claim_due_task(session, task_type=task_type, owner="owner-a")
        assert old_claim is not None
        commit_progress(
            session,
            old_claim.id,
            lease_generation=old_claim.lease_generation,
            cursor="table-5",
            processed_delta=5,
        )

        refreshed = start_new_generation(session, task.id, input_watermark="scan-v2")
        assert refreshed.generation == old_claim.generation + 1
        assert refreshed.cursor is None
        assert refreshed.processed_count == 0
        assert refreshed.input_watermark == "scan-v2"

        # The old child's held lease_generation must no longer be valid.
        with pytest.raises(LeaseConflict):
            commit_progress(
                session,
                old_claim.id,
                lease_generation=old_claim.lease_generation,
                cursor="table-6",
                processed_delta=1,
            )


def test_two_concurrent_claimers_never_both_win(monkeypatch=None):
    """Direct simulation of two coordinator replicas racing the same due task in the same
    instant -- with_for_update(skip_locked=True) must ensure exactly one wins, not both."""
    task_type = _unique_type()
    with SessionLocal() as setup_session:
        ensure_task_due(setup_session, task_type=task_type, scope="fleet")

    results: list = []
    with SessionLocal() as session_a, SessionLocal() as session_b:
        claim_a = claim_due_task(session_a, task_type=task_type, owner="replica-a")
        claim_b = claim_due_task(session_b, task_type=task_type, owner="replica-b")
        results = [claim_a, claim_b]

    winners = [c for c in results if c is not None]
    assert len(winners) == 1, "exactly one of two concurrent claimers must win the lease"


def test_two_concurrent_commit_progress_calls_never_both_succeed():
    """Review finding F04: the pre-fix version compared lease_generation in Python after a
    plain session.get() read -- two sessions could both read the same generation, both pass the
    check, and both commit, with no database-enforced fence. This directly simulates that race:
    two separate sessions, same held (id, lease_generation), both call commit_progress. Exactly
    one must succeed; the other must raise LeaseConflict, not silently also succeed."""
    task_type = _unique_type()
    with SessionLocal() as setup_session:
        ensure_task_due(setup_session, task_type=task_type, scope="fleet")
        claimed = claim_due_task(setup_session, task_type=task_type, owner="owner-a")
        assert claimed is not None

    outcomes: list[str] = []
    with SessionLocal() as session_a, SessionLocal() as session_b:
        for session, cursor in ((session_a, "a-cursor"), (session_b, "b-cursor")):
            try:
                commit_progress(
                    session,
                    claimed.id,
                    lease_generation=claimed.lease_generation,
                    cursor=cursor,
                    processed_delta=1,
                )
                outcomes.append("ok")
            except LeaseConflict:
                outcomes.append("conflict")

    assert outcomes.count("ok") == 1
    assert outcomes.count("conflict") == 1

    with SessionLocal() as session:
        task = get_active_task(session, task_type=task_type, scope="fleet")
        assert task is not None
        assert task.processed_count == 1, "the losing write must not have applied its delta too"


def test_heartbeat_cannot_revive_a_task_finished_concurrently():
    """Review finding F04: heartbeat never checked terminal status at all -- a heartbeat racing
    a legitimate finish_task call (plausible in the real runner: a heartbeat thread and the main
    batch-completion path) could move an already-terminal row back to 'running'."""
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(session, task_type=task_type, scope="fleet")
        claimed = claim_due_task(session, task_type=task_type, owner="owner-a")
        assert claimed is not None

        finish_task(
            session, claimed.id, lease_generation=claimed.lease_generation, status="succeeded"
        )

        result = heartbeat(session, claimed.id, lease_generation=claimed.lease_generation)
        assert result is None

        from cryo.db.models import AnalysisTask

        refreshed = session.get(AnalysisTask, claimed.id)
        assert refreshed.status == "succeeded"


def test_finish_task_cannot_be_called_twice():
    """A second finish_task call against an already-terminal row (e.g. a retry after a network
    timeout on the first call's response) must raise LeaseConflict, not silently re-apply."""
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(session, task_type=task_type, scope="fleet")
        claimed = claim_due_task(session, task_type=task_type, owner="owner-a")
        assert claimed is not None

        finish_task(
            session, claimed.id, lease_generation=claimed.lease_generation, status="succeeded"
        )

        with pytest.raises(LeaseConflict):
            finish_task(
                session,
                claimed.id,
                lease_generation=claimed.lease_generation,
                status="failed",
                failure_reason="should never apply",
            )

        from cryo.db.models import AnalysisTask

        refreshed = session.get(AnalysisTask, claimed.id)
        assert refreshed.status == "succeeded"


def test_start_new_generation_advances_lease_generation_rather_than_resetting_it():
    """Review finding F04 ('generation reuse'): resetting lease_generation to 0 on every new
    generation meant the next claim always produced lease_generation=1 -- the same token an
    owner from a PRIOR generation's first-ever claim could still hold. Confirms the fence is now
    monotonic across generations, not reset to a reusable value."""
    task_type = _unique_type()
    with SessionLocal() as session:
        task = ensure_task_due(session, task_type=task_type, scope="fleet")
        first_gen_claim = claim_due_task(session, task_type=task_type, owner="owner-a")
        assert first_gen_claim is not None
        assert first_gen_claim.lease_generation == 1

        start_new_generation(session, task.id, input_watermark="scan-v2")

        second_gen_claim = claim_due_task(session, task_type=task_type, owner="owner-b")
        assert second_gen_claim is not None
        # Must NOT be 1 again -- that would be the exact reused-token bug.
        assert second_gen_claim.lease_generation > first_gen_claim.lease_generation

        # And the first generation's old token must still be rejected.
        with pytest.raises(LeaseConflict):
            commit_progress(
                session,
                first_gen_claim.id,
                lease_generation=first_gen_claim.lease_generation,
                cursor="stale",
                processed_delta=1,
            )


def test_yield_task_returns_a_bounded_exit_to_pending_and_preserves_cursor():
    """Review finding F03: a runner that stops after its own bounded batch-count limit (not a
    crash) previously left the row at status='running' with a lease -- unclaimable by
    claim_due_task's WHERE clause (which only matches 'pending'/'leased') until the lease
    naturally expired, even though the exit was clean and immediate re-dispatch was safe.
    yield_task must return it to 'pending' (claimable again right away) while preserving the
    cursor/processed_count exactly as the last commit_progress left them."""
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(session, task_type=task_type, scope="fleet")
        claimed = claim_due_task(session, task_type=task_type, owner="owner-a")
        assert claimed is not None
        new_generation = commit_progress(
            session,
            claimed.id,
            lease_generation=claimed.lease_generation,
            cursor="table-42",
            processed_delta=7,
        )

        ok = yield_task(session, claimed.id, lease_generation=new_generation)
        assert ok is True

        task = get_active_task(session, task_type=task_type, scope="fleet")
        assert task is not None
        assert task.status == "pending"
        assert task.cursor == "table-42"
        assert task.processed_count == 7
        assert task.lease_owner is None

        # And it's immediately re-claimable -- no need to wait for the old lease to expire.
        reclaimed = claim_due_task(session, task_type=task_type, owner="owner-b")
        assert reclaimed is not None
        assert reclaimed.cursor == "table-42"


def test_yield_task_rejects_a_stale_lease_generation():
    task_type = _unique_type()
    with SessionLocal() as session:
        ensure_task_due(session, task_type=task_type, scope="fleet")
        stale_claim = claim_due_task(session, task_type=task_type, owner="owner-a", lease_seconds=1)
        assert stale_claim is not None
        later = datetime.now(UTC) + timedelta(seconds=5)
        claim_due_task(session, task_type=task_type, owner="owner-b", now=later)

        ok = yield_task(session, stale_claim.id, lease_generation=stale_claim.lease_generation)
        assert ok is False
