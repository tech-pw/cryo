"""Real-Postgres integration tests for cryo/analysis/runner.py.

Same rationale as tests/test_analysis_tasks_postgres.py: the runner's correctness depends on
compare-and-set semantics (partial unique index, with_for_update(skip_locked=True)) that SQLite
does not enforce. These tests exercise the actual claim -> batch -> commit_progress -> finish_task
sequence end to end against real Postgres, seeding real Table/TablePartition rows so
update_all_benefit_scores_batch has real work to do.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from cryo.analysis.runner import _BENEFIT_SCORE_SWEEP, run_benefit_score_sweep_task
from cryo.db.models import Source, Table, TablePartition
from cryo.db.repositories.analysis_tasks import ensure_task_due, get_active_task
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
def _clean_up():
    created_table_ids: list[uuid.UUID] = []
    yield created_table_ids
    with SessionLocal() as session:
        session.execute(text("DELETE FROM cryo.analysis_tasks WHERE task_type LIKE 'test_%'"))
        for table_id in created_table_ids:
            session.execute(
                text("DELETE FROM cryo.table_partitions WHERE table_id = :id"), {"id": table_id}
            )
            session.execute(text("DELETE FROM cryo.table_stats WHERE table_id = :id"), {"id": table_id})
            session.execute(text("DELETE FROM cryo.tables WHERE id = :id"), {"id": table_id})
            session.execute(
                text(
                    "DELETE FROM cryo.sources WHERE id = "
                    "(SELECT source_id FROM cryo.tables WHERE id = :id)"
                ),
                {"id": table_id},
            )
        session.commit()


def _seed_table_with_partition(session, created_table_ids: list[uuid.UUID], *, name: str) -> uuid.UUID:
    source = Source(id=uuid.uuid4(), name=f"test-src-{uuid.uuid4().hex[:8]}", type="rest", config={})
    session.add(source)
    session.flush()
    table = Table(
        id=uuid.uuid4(), source_id=source.id, schema_name="test", table_name=name, is_active=True
    )
    session.add(table)
    session.flush()
    session.add(
        TablePartition(
            table_id=table.id,
            partition_values={"dt": "2024-01-01"},
            partition_fields=[],
            partition_value="2024-01-01",
            partition_size_gb=1.0,
            small_files_in_partition_count=10,
            scan_at=datetime.now(UTC),
        )
    )
    session.commit()
    created_table_ids.append(table.id)
    return table.id


def test_run_benefit_score_sweep_task_returns_zero_when_nothing_due(_clean_up):
    result = run_benefit_score_sweep_task()
    assert result == 0


def test_run_benefit_score_sweep_task_completes_a_small_fleet_and_marks_task_succeeded(_clean_up):
    task_type = f"test_{uuid.uuid4().hex[:12]}"
    with SessionLocal() as session:
        _seed_table_with_partition(session, _clean_up, name=f"runner_{uuid.uuid4().hex[:8]}")

    # Point the real task_type constant at a throwaway one for isolation would require changing
    # the module constant; instead exercise via the real constant but confirm no leftover active
    # task exists first, then clean it up via ensure_task_due's own idempotency.
    with SessionLocal() as session:
        existing = get_active_task(session, task_type=_BENEFIT_SCORE_SWEEP, scope="fleet")
        assert existing is None, (
            "a real active benefit_score_sweep task already exists in this DB -- "
            "skipping to avoid interfering with it"
        )
        ensure_task_due(session, task_type=_BENEFIT_SCORE_SWEEP, scope="fleet")

    result = run_benefit_score_sweep_task()
    assert result == 0

    with SessionLocal() as session:
        task = get_active_task(session, task_type=_BENEFIT_SCORE_SWEEP, scope="fleet")
        # Task should have reached a terminal status (succeeded) and no longer be "active".
        assert task is None
        session.execute(
            text("DELETE FROM cryo.analysis_tasks WHERE task_type = :t"),
            {"t": _BENEFIT_SCORE_SWEEP},
        )
        session.commit()


def test_run_benefit_score_sweep_task_yields_to_pending_on_bounded_exit(_clean_up, monkeypatch):
    """Review finding F03: a runner that exhausts its bounded batch-count limit without
    finishing the whole fleet must leave the task immediately re-claimable ('pending'), not
    stranded at 'running' with a lease that has to expire naturally first. Forces this by
    patching update_all_benefit_scores_batch itself to a 1-table-per-call stand-in (the real
    function's batch_size defaults are bound at def-time, so monkeypatching the module constant
    it defaults from has no effect -- patch the function the runner actually calls instead) and
    the batch-count limit down to 2, so 3 seeded tables guarantee the runner exhausts its
    invocation before the fleet finishes."""
    import cryo.analysis.runner as runner_module
    from cryo.db.repositories.analysis_tasks import get_active_task
    from cryo.planner.benefit_score import update_all_benefit_scores_batch as real_batch_fn

    monkeypatch.setattr(runner_module, "_MAX_BATCHES_PER_INVOCATION", 2)
    monkeypatch.setattr(
        runner_module,
        "update_all_benefit_scores_batch",
        lambda session, *, cursor=None, **kw: real_batch_fn(session, cursor=cursor, batch_size=1, **kw),
    )

    with SessionLocal() as session:
        for i in range(3):
            _seed_table_with_partition(session, _clean_up, name=f"yield_test_{uuid.uuid4().hex[:8]}_{i}")

    with SessionLocal() as session:
        existing = get_active_task(session, task_type=_BENEFIT_SCORE_SWEEP, scope="fleet")
        assert existing is None, "a real active benefit_score_sweep task already exists"
        ensure_task_due(session, task_type=_BENEFIT_SCORE_SWEEP, scope="fleet")

    try:
        result = run_benefit_score_sweep_task()
        assert result == 0

        with SessionLocal() as session:
            task = get_active_task(session, task_type=_BENEFIT_SCORE_SWEEP, scope="fleet")
            assert task is not None, "task must still be active (fleet not yet complete)"
            assert task.status == "pending", "bounded exit must yield back to pending, not strand at running"
            assert task.lease_owner is None
            assert task.processed_count == 2, "2 batches of 1 table each ran before the bound hit"

        # And it's immediately re-claimable without waiting for any lease to expire.
        second_result = run_benefit_score_sweep_task()
        assert second_result == 0
        with SessionLocal() as session:
            task = get_active_task(session, task_type=_BENEFIT_SCORE_SWEEP, scope="fleet")
            assert task is None, "the 3rd table's batch should have completed the fleet"
    finally:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM cryo.analysis_tasks WHERE task_type = :t"),
                {"t": _BENEFIT_SCORE_SWEEP},
            )
            session.commit()


def test_run_benefit_score_sweep_task_tracks_refresh_budget_across_invocations(_clean_up, monkeypatch):
    """Review finding F08, end-to-end through the real runner + persistence layer: the
    realized-value refresh budget must survive a process restart (this test calls
    run_benefit_score_sweep_task() twice, simulating two separate K8s Job pods for the same
    task) via AnalysisTask.task_state, not silently reset to full on the second invocation."""
    import cryo.analysis.runner as runner_module
    from cryo.db.repositories.analysis_tasks import get_active_task
    from cryo.planner.benefit_score import update_all_benefit_scores_batch as real_batch_fn

    monkeypatch.setattr(runner_module, "_MAX_BATCHES_PER_INVOCATION", 1)
    monkeypatch.setattr(
        runner_module,
        "update_all_benefit_scores_batch",
        lambda session, *, cursor=None, **kw: real_batch_fn(session, cursor=cursor, batch_size=1, **kw),
    )
    # Deliberately > 1: a bugged "reset to default every invocation" implementation would spend
    # up to 1 MORE on batch 2's still-eligible table, landing at remaining=1 (2-1) after the
    # second invocation instead of the fix's remaining=0 (0-0, correctly resumed already-
    # exhausted). Using default=1 here would make both the bug and the fix coincidentally end
    # at the same number (0-0 or 1-1), which couldn't actually distinguish them.
    monkeypatch.setattr(runner_module, "_DEFAULT_MAX_REALIZED_VALUE_REFRESHES_PER_SWEEP", 2)

    with SessionLocal() as session:
        for i in range(2):
            table_id = _seed_table_with_partition(
                session, _clean_up, name=f"budget_test_{uuid.uuid4().hex[:8]}_{i}"
            )
            from cryo.db.models import TableStats

            session.add(
                TableStats(
                    table_id=table_id,
                    scan_at=datetime.now(UTC),
                    needs_compaction=False,
                    compaction_priority="LOW",
                    grade="A",
                    overall_score=95,
                    total_files=10,
                    small_files_count=0,
                    total_size_gb=1.0,
                    raw={},
                )
            )
        session.commit()

    with SessionLocal() as session:
        existing = get_active_task(session, task_type=_BENEFIT_SCORE_SWEEP, scope="fleet")
        assert existing is None, "a real active benefit_score_sweep task already exists"
        ensure_task_due(session, task_type=_BENEFIT_SCORE_SWEEP, scope="fleet")

    try:
        # First invocation: 1 batch of 1 table. Budget starts at 2 but only 1 table is in this
        # batch, so only 1 refresh can be spent -- remaining becomes 2 - 1 = 1.
        result = run_benefit_score_sweep_task()
        assert result == 0

        with SessionLocal() as session:
            task = get_active_task(session, task_type=_BENEFIT_SCORE_SWEEP, scope="fleet")
            assert task is not None, "fleet has 2 tables, only 1 processed so far"
            assert task.task_state == {"refresh_budget_remaining": 1}, (
                "budget must be persisted as partially consumed (2-1=1), not reset, after the "
                "first invocation"
            )

        # Second invocation (simulating a fresh Job pod): must resume with budget_remaining=1,
        # spend at most 1 more on table 2 (remaining -> 0) -- NOT reset to the default of 2
        # (which would spend a full 1 again and land at remaining=1, the exact bug F08 fixes).
        second_result = run_benefit_score_sweep_task()
        assert second_result == 0

        with SessionLocal() as session:
            task = get_active_task(session, task_type=_BENEFIT_SCORE_SWEEP, scope="fleet")
            assert task is not None
            assert task.processed_count == 2, "both tables have now been processed"
            assert task.task_state == {"refresh_budget_remaining": 0}, (
                "budget must continue depleting from the persisted 1 (not reset to the default "
                "2), landing at 0 after the second table's refresh is spent"
            )
    finally:
        with SessionLocal() as session:
            session.execute(
                text("DELETE FROM cryo.analysis_tasks WHERE task_type = :t"),
                {"t": _BENEFIT_SCORE_SWEEP},
            )
            session.commit()


def test_coordinator_tick_dispatches_a_job_and_records_external_job_ref(monkeypatch):
    """cryo.cli.BenefitScoreUpdaterRunner._tick_via_k8s_job: the coordinator tick must ensure a
    due task exists and persist the dispatched Job's name onto it -- this is what lets the next
    tick recognize an outstanding Job and skip a redundant dispatch."""
    import cryo.cli as cli_module
    from cryo.analysis import dispatch as dispatch_module
    from cryo.db.repositories.analysis_tasks import get_active_task

    task_type = "benefit_score_sweep"
    with SessionLocal() as session:
        session.execute(text("DELETE FROM cryo.analysis_tasks WHERE task_type = :t"), {"t": task_type})
        session.commit()

    monkeypatch.setattr(dispatch_module, "k8s_dispatch_available", lambda: True)

    class _FakeResult:
        ok = True
        job_name = "cryo-analysis-benefit-score-sweep-testfake"
        error = None

    monkeypatch.setattr(dispatch_module, "dispatch_analysis_job", lambda *a, **kw: _FakeResult())

    runner = cli_module.BenefitScoreUpdaterRunner()
    runner._tick_via_k8s_job()

    with SessionLocal() as session:
        task = get_active_task(session, task_type=task_type, scope="fleet")
        assert task is not None
        assert task.external_job_ref == "cryo-analysis-benefit-score-sweep-testfake"

    # Second tick, lease still fresh (ensure_task_due's row has no lease yet, but claim hasn't
    # happened either) -- exercised mainly to confirm no exception; the skip-if-outstanding path
    # only engages once lease_until is set by an actual claim, which this test doesn't simulate.
    runner._tick_via_k8s_job()

    with SessionLocal() as session:
        session.execute(text("DELETE FROM cryo.analysis_tasks WHERE task_type = :t"), {"t": task_type})
        session.commit()
