from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.scheduler_store import SQLSchedulerStore
from cryo.domain.enums import Mode
from cryo.planner.job_coverage import ActiveJob
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler.rules import PresetConfig, PresetRule, TableCandidate
from cryo.scheduler.scheduler import ScheduledJobDraft, ScheduledRunDraft, build_scheduled_run
from cryo.scheduler.service import InMemorySchedulerStore, SchedulerService
from tests.conftest import (
    _assign_missing_autoincrement_primary_keys,
    _assign_missing_uuid_primary_keys,
)

NOW = datetime(2026, 10, 21, 1, 0, tzinfo=UTC)
_RULE = {"field": "total_size_gb", "op": ">=", "value": 0}


def _preset(preset_id: str = "p1", mode: str = "standard", **guardrails: object) -> PresetConfig:
    return PresetConfig(
        id=preset_id,
        name=preset_id,
        destination_id="dest-1",
        mode=mode,
        guardrails=guardrails,
        schedule_cron="* * * * *",
    )


def _tables(*ids: str) -> list[TableCandidate]:
    return [
        TableCandidate(table_id, "demo", f"t_{table_id}", {"total_size_gb": 5}) for table_id in ids
    ]


def _build(preset, tables, coverage=None, **kwargs):
    return build_scheduled_run(
        preset,
        tables,
        [preset],
        [PresetRule("r1", preset.id, _RULE)],
        now=NOW,
        active_coverage=coverage,
        **kwargs,
    )


def test_fully_covered_table_is_dropped_before_the_budget_cut() -> None:
    covered = {
        "t1": [
            ActiveJob(
                "j",
                frozenset(
                    {
                        "optimize",
                        "expire_snapshots",
                        "expire_data",
                        "convert_equality_deletes",
                        "convert_deletes",
                        "rewrite_position_deletes",
                        "compute_statistics",
                    }
                ),
            )
        ]
    }

    run = _build(_preset(), _tables("t1", "t2", "t3"), covered, budget_hint=2)

    assert sorted(job.table_id for job in run.jobs) == ["t2", "t3"]
    assert run.dedupe_tables_skipped == 1


def test_partially_covered_table_gets_a_trimmed_job() -> None:
    covered = {"t1": [ActiveJob("j", frozenset({"expire_snapshots", "expire_data"}))]}

    run = _build(_preset(), _tables("t1", "t2"), covered)

    by_table = {job.table_id: job for job in run.jobs}
    assert by_table["t2"].operations is None
    assert by_table["t1"].operations is not None
    assert "optimize" in by_table["t1"].operations
    assert "expire_snapshots" not in by_table["t1"].operations
    assert run.dedupe_steps_skipped == 2
    assert run.dedupe_tables_skipped == 0


def test_no_coverage_mapping_means_no_dedupe() -> None:
    run = _build(_preset(), _tables("t1", "t2"), None)

    assert len(run.jobs) == 2
    assert all(job.operations is None for job in run.jobs)


def _service(
    *presets: PresetConfig, tables: list[TableCandidate]
) -> tuple[SchedulerService, InMemorySchedulerStore]:
    store = InMemorySchedulerStore()
    store.presets = list(presets)
    store.rules = [PresetRule(f"r-{p.id}", p.id, _RULE) for p in presets]
    store.tables = tables
    return SchedulerService(store, InMemoryMaintenanceQueue()), store


def test_second_preset_in_the_same_tick_sees_the_first_presets_jobs() -> None:
    service, store = _service(_preset("p1"), _preset("p2"), tables=_tables("t1", "t2"))
    # Both presets match both tables via their own rule; the second must not duplicate.
    result = service._dispatch_due_presets(NOW)

    assert result.runs_created == 1
    assert len(store.runs) == 1
    assert len(store.runs[0].jobs) == 2


def test_next_tick_does_not_requeue_tables_whose_jobs_are_still_active() -> None:
    service, store = _service(_preset("p1"), tables=_tables("t1", "t2"))

    first = service._dispatch_due_presets(NOW)
    second = service._dispatch_due_presets(NOW + timedelta(minutes=5))

    assert first.jobs_enqueued == 2
    assert second.jobs_enqueued == 0
    assert len(store.runs) == 1


def test_store_without_active_coverage_still_schedules() -> None:
    class _Bare(InMemorySchedulerStore):
        active_coverage = None  # type: ignore[assignment]

    store = _Bare()
    store.presets = [_preset("p1")]
    store.rules = [PresetRule("r1", "p1", _RULE)]
    store.tables = _tables("t1")
    service = SchedulerService(store, InMemoryMaintenanceQueue())

    assert service._dispatch_due_presets(NOW).jobs_enqueued == 1


def test_coverage_read_failure_falls_back_to_scheduling() -> None:
    class _Broken(InMemorySchedulerStore):
        def active_coverage(self):
            raise RuntimeError("db down")

    store = _Broken()
    store.presets = [_preset("p1")]
    store.rules = [PresetRule("r1", "p1", _RULE)]
    store.tables = _tables("t1")
    service = SchedulerService(store, InMemoryMaintenanceQueue())

    assert service._dispatch_due_presets(NOW).jobs_enqueued == 1


def _session_factory(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    event.listen(factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    return factory


def _seed_db(session):
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={})
    table = Table(id=uuid4(), source_id=source.id, schema_name="s", table_name="t")
    session.add_all([source, destination, table])
    session.flush()
    from cryo.db.models import Preset

    preset = Preset(
        id=uuid4(),
        name=f"p-{uuid4()}",
        destination_id=destination.id,
        operations=[],
        params={},
        guardrails={},
    )
    session.add(preset)
    session.commit()
    return preset, destination, table


def _draft(preset, destination, table, *, operations=None) -> ScheduledRunDraft:
    run_id = str(uuid4())
    return ScheduledRunDraft(
        id=run_id,
        preset_id=str(preset.id),
        trigger="cron",
        deadline_at=None,
        jobs=(
            ScheduledJobDraft(
                id=str(uuid4()),
                run_id=run_id,
                table_id=str(table.id),
                destination_id=str(destination.id),
                mode=Mode.STANDARD,
                operations=operations,
            ),
        ),
        dedupe_steps_skipped=3 if operations else 0,
    )


def test_persist_run_writes_operations_dedupe_key_and_summary(session) -> None:
    preset, destination, table = _seed_db(session)
    store = SQLSchedulerStore(_session_factory(session), redis_client=None)
    draft = _draft(preset, destination, table, operations=("optimize", "expire_snapshots"))

    dropped = store.persist_run(draft)

    assert dropped == set()
    session.expire_all()
    job = session.get(MaintenanceJob, UUID(draft.jobs[0].id))
    assert job.operations == ["optimize", "expire_snapshots"]
    assert job.dedupe_key == "optimize,expire_snapshots"
    run = session.get(MaintenanceRun, UUID(draft.id))
    assert run.summary["dedupe"]["steps_skipped"] == 3


def test_persist_run_drops_a_job_that_loses_the_dedupe_race(session) -> None:
    preset, destination, table = _seed_db(session)
    store = SQLSchedulerStore(_session_factory(session), redis_client=None)
    first = _draft(preset, destination, table)
    second = _draft(preset, destination, table)

    assert store.persist_run(first) == set()
    dropped = store.persist_run(second)

    assert dropped == {second.jobs[0].id}
    session.expire_all()
    assert session.query(MaintenanceRun).count() == 1
    assert session.query(MaintenanceJob).count() == 1


def test_sql_active_coverage_reports_persisted_jobs(session) -> None:
    preset, destination, table = _seed_db(session)
    store = SQLSchedulerStore(_session_factory(session), redis_client=None)
    draft = _draft(preset, destination, table)
    store.persist_run(draft)

    coverage = store.active_coverage()

    assert [job.job_id for job in coverage[str(table.id)]] == [draft.jobs[0].id]
