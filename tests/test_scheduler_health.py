"""Tests for cryo/scheduler/health.py's silent-data-loss sweep.

Regression coverage for a real production incident: run_silent_data_loss_sweep() loaded
every active table (~13k in production) into one unbounded session every 5 minutes,
accumulating every Table/TableStats ORM object in the session's identity map simultaneously
-- cryo-scheduler OOMKilled roughly every 5 minutes, ~300 restarts over a week, and
auto-maintenance silently stopped firing entirely as a result. Also covers a second bug
found alongside it: the scheduler's sweep never committed its session, so any
SilentDataLossEvent rows it created were rolled back on session close instead of persisted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from cryo.db.models import SilentDataLossEvent, Source, Table, TableStats
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scanner.silent_data_loss import OrphanReport
from cryo.scheduler import health as health_module
from cryo.scheduler.health import run_silent_data_loss_sweep
from cryo.scheduler.service import SchedulerService


def _seed_source(session) -> Source:
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="rest", config={})
    session.add(source)
    session.flush()
    return source


def _seed_table(session, source: Source) -> Table:
    table_id = uuid.uuid4()
    session.add(
        Table(
            id=table_id,
            source_id=source.id,
            schema_name="analytics",
            table_name=f"t_{table_id.hex[:8]}",
            first_seen_at=datetime(2026, 1, 1, tzinfo=UTC),
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=datetime(2026, 1, 1, tzinfo=UTC),
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=1,
            total_size_gb=1.0,
            query_count_30d=0,
        )
    )
    session.flush()
    table = session.get(Table, table_id)
    assert table is not None
    return table


def _clean_report(table_id: uuid.UUID) -> OrphanReport:
    return OrphanReport(
        table_id=str(table_id),
        snapshot_id=None,
        orphan_data_files=[],
        orphan_size_bytes=0,
        missing_files=[],
        detected_at=datetime.now(UTC).isoformat(),
        root_cause="clean",
    )


def test_sweep_only_processes_up_to_the_batch_limit(session, monkeypatch) -> None:
    source = _seed_source(session)
    tables = sorted((_seed_table(session, source) for _ in range(5)), key=lambda t: t.id)
    session.commit()

    calls: list[uuid.UUID] = []

    def fake_check(sess, table_id, **kwargs):
        calls.append(table_id)
        return _clean_report(table_id)

    monkeypatch.setattr(health_module, "check_silent_data_loss", fake_check)

    results, next_cursor = run_silent_data_loss_sweep(session, limit=2)

    assert len(calls) == 2
    assert calls == [tables[0].id, tables[1].id]
    # A full batch was returned -- there's more fleet left, so the cursor must be set so the
    # next tick resumes rather than restarting from the top.
    assert next_cursor == tables[1].id
    assert results == []  # all reports were clean


def test_sweep_cursor_resumes_and_wraps_at_end_of_fleet(session, monkeypatch) -> None:
    source = _seed_source(session)
    tables = sorted((_seed_table(session, source) for _ in range(5)), key=lambda t: t.id)
    session.commit()

    monkeypatch.setattr(
        health_module,
        "check_silent_data_loss",
        lambda sess, table_id, **kwargs: _clean_report(table_id),
    )

    _, cursor = run_silent_data_loss_sweep(session, limit=3)
    assert cursor == tables[2].id

    _, next_cursor = run_silent_data_loss_sweep(session, after_id=cursor, limit=3)
    # Only 2 tables remain after the cursor -- fewer than the batch limit, so this is the
    # end of the fleet and the cursor must reset to None (wrap on the next tick).
    assert next_cursor is None


def test_sweep_reports_only_dirty_tables(session, monkeypatch) -> None:
    source = _seed_source(session)
    table = _seed_table(session, source)
    session.commit()

    dirty = OrphanReport(
        table_id=str(table.id),
        snapshot_id=None,
        orphan_data_files=["s3://bucket/orphan.parquet"],
        orphan_size_bytes=100,
        missing_files=[],
        detected_at=datetime.now(UTC).isoformat(),
        root_cause="orphan_files_on_storage",
    )
    monkeypatch.setattr(
        health_module, "check_silent_data_loss", lambda sess, table_id, **kwargs: dirty
    )

    results, _ = run_silent_data_loss_sweep(session, limit=10)

    assert len(results) == 1
    assert results[0].table_id == table.id
    assert results[0].report.root_cause == "orphan_files_on_storage"


def test_sweep_isolates_a_failure_on_one_table_from_the_rest(session, monkeypatch) -> None:
    source = _seed_source(session)
    tables = sorted((_seed_table(session, source) for _ in range(3)), key=lambda t: t.id)
    session.commit()

    def flaky_check(sess, table_id, **kwargs):
        if table_id == tables[1].id:
            raise RuntimeError("boom")
        return _clean_report(table_id)

    monkeypatch.setattr(health_module, "check_silent_data_loss", flaky_check)

    results, next_cursor = run_silent_data_loss_sweep(session, limit=10)

    assert results == []  # all clean/failed, nothing dirty
    assert next_cursor is None  # still swept the whole (small) batch despite the failure


def test_maybe_silent_data_loss_sweep_commits_persisted_events(session, monkeypatch) -> None:
    # Regression test for the swallowed-commit bug: the scheduler's own sweep call used to
    # never commit, so a SilentDataLossEvent row created mid-sweep was rolled back the
    # moment `with SessionLocal() as session` closed -- pure wasted work, no audit trail.
    source = _seed_source(session)
    table = _seed_table(session, source)
    session.commit()

    # The real code does `with SessionLocal() as session: ...`, which closes whatever it's
    # given on exit -- handing it our own test `session` fixture directly would close that
    # fixture mid-test. Bind a separate session to the same (StaticPool, shared in-memory)
    # engine instead, so commits made through it are visible to this test's assertions.
    from tests.conftest import _assign_missing_uuid_primary_keys

    scoped_factory = sessionmaker(bind=session.get_bind())
    event.listen(scoped_factory, "before_flush", _assign_missing_uuid_primary_keys)
    monkeypatch.setattr("cryo.scheduler.service.SessionLocal", scoped_factory)

    # Let the *real* detector run (not a mock) so its real persistence side-effect
    # (commit_orphan_event -> session.add()) actually fires: claim a committed file that
    # doesn't exist on local storage (no local dir was ever created for this table), which
    # the real detector reports as a genuine "missing_files" case -- not clean.
    stats = session.get(TableStats, table.id)
    assert stats is not None
    stats.raw = {"committed_files": ["warehouse/db/t/data/file1.parquet"]}
    session.commit()

    service = SchedulerService(_NullStore(), InMemoryMaintenanceQueue())
    service._maybe_silent_data_loss_sweep(datetime.now(UTC))

    # Same underlying (StaticPool) connection, but a separate Session/transaction wrote the
    # event -- end this session's own transaction so its next query starts a fresh one and
    # actually sees the other session's commit.
    session.commit()
    persisted = session.scalars(
        select(SilentDataLossEvent).where(SilentDataLossEvent.table_id == table.id)
    ).all()
    assert len(persisted) == 1
    assert persisted[0].root_cause == "missing_committed_files"
    assert persisted[0].missing_files == ["warehouse/db/t/data/file1.parquet"]


class _NullStore:
    def list_due_presets(self, now):
        return []

    def list_tables(self):
        return []

    def list_presets(self):
        return []

    def list_rules(self):
        return []

    def list_assignments(self):
        return []

    def list_destinations(self):
        return []

    def persist_run(self, run):
        pass

    def mark_preset_run(self, preset_id, run_at):
        pass
