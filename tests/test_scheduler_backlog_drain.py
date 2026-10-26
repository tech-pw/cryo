from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

from cryo.api.table_maintenance import nightly_run_ids
from cryo.config import get_settings
from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Preset, Source, Table
from cryo.db.repositories.scheduler_store import SQLSchedulerStore
from cryo.domain.enums import JobStatus
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler.rules import PresetConfig, PresetRule, TableCandidate
from cryo.scheduler.scheduler import BACKLOG_TRIGGER
from cryo.scheduler.service import InMemorySchedulerStore, SchedulerService
from tests.conftest import (
    _assign_missing_autoincrement_primary_keys,
    _assign_missing_uuid_primary_keys,
)

NOW = datetime(2026, 10, 26, 2, 0, tzinfo=UTC)
_RULE = {"field": "total_size_gb", "op": ">=", "value": 0}


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "max_jobs_per_scheduled_run": 2,
            "scheduler_backlog_drain_enabled": True,
            "scheduler_backlog_drain_low_water": 0,
            "scheduler_backlog_drain_interval_seconds": 60,
        }
    )
    monkeypatch.setattr("cryo.config.get_settings", lambda: settings)
    return settings


def _preset(**guardrails: object) -> PresetConfig:
    return PresetConfig(
        id="p1",
        name="p1",
        destination_id="dest-1",
        mode="standard",
        guardrails=guardrails,
        schedule_cron="0 2 * * *",
    )


def _service(preset: PresetConfig, table_count: int = 5):
    store = InMemorySchedulerStore()
    store.presets = [preset]
    store.rules = [PresetRule("r1", preset.id, _RULE)]
    store.tables = [
        TableCandidate(f"t{i}", "demo", f"t{i}", {"total_size_gb": 5}) for i in range(table_count)
    ]
    store.clock = lambda: NOW
    return SchedulerService(store, InMemoryMaintenanceQueue()), store


def _finish(store: InMemorySchedulerStore, status: JobStatus = JobStatus.SUCCEEDED) -> None:
    store.runs = [
        replace(run, jobs=tuple(replace(job, status=status) for job in run.jobs))
        for run in store.runs
    ]


def _tables_in(run) -> set[str]:
    return {job.table_id for job in run.jobs}


def test_drained_batches_reach_every_candidate_once_per_cron_period() -> None:
    service, store = _service(_preset())
    assert service._dispatch_due_presets(NOW).jobs_enqueued == 2

    batches = []
    at = NOW
    for _ in range(4):
        _finish(store)
        at += timedelta(minutes=2)
        store.clock = lambda at=at: at
        batches.append(service._drain_backlog(at).jobs_enqueued)

    assert batches == [2, 1, 0, 0]
    seen = [table for run in store.runs for table in _tables_in(run)]
    assert sorted(seen) == ["t0", "t1", "t2", "t3", "t4"]
    assert [run.trigger for run in store.runs] == ["cron", BACKLOG_TRIGGER, BACKLOG_TRIGGER]


def test_waits_while_the_previous_batch_is_still_in_flight() -> None:
    service, store = _service(_preset())
    service._dispatch_due_presets(NOW)

    assert service._drain_backlog(NOW + timedelta(minutes=2)).runs_created == 0


def test_deferred_jobs_do_not_hold_back_the_next_batch() -> None:
    service, store = _service(_preset())
    service._dispatch_due_presets(NOW)
    _finish(store, JobStatus.DEFERRED)

    assert service._drain_backlog(NOW + timedelta(minutes=2)).jobs_enqueued == 2


def test_failed_tables_are_not_retried_in_the_same_period() -> None:
    service, store = _service(_preset(), table_count=3)
    service._dispatch_due_presets(NOW)
    failed = _tables_in(store.runs[0])
    _finish(store, JobStatus.FAILED)

    service._drain_backlog(NOW + timedelta(minutes=2))

    assert not _tables_in(store.runs[-1]) & failed


def test_batches_are_spaced_by_the_interval(_settings) -> None:
    _settings.scheduler_backlog_drain_interval_seconds = 900
    service, store = _service(_preset(), table_count=10)
    service._dispatch_due_presets(NOW)
    _finish(store)
    assert service._drain_backlog(NOW + timedelta(minutes=1)).runs_created == 1
    _finish(store)

    assert service._drain_backlog(NOW + timedelta(minutes=5)).runs_created == 0
    assert service._drain_backlog(NOW + timedelta(minutes=17)).runs_created == 1


def test_preset_can_opt_out() -> None:
    service, store = _service(_preset(backlog_drain=False))
    service._dispatch_due_presets(NOW)
    _finish(store)

    assert service._drain_backlog(NOW + timedelta(minutes=2)).runs_created == 0


def test_disabled_setting_turns_drain_off(_settings) -> None:
    _settings.scheduler_backlog_drain_enabled = False
    service, store = _service(_preset())
    service._dispatch_due_presets(NOW)
    _finish(store)

    assert service._drain_backlog(NOW + timedelta(minutes=2)).runs_created == 0


def test_no_drain_before_the_first_cron_fire() -> None:
    service, store = _service(_preset())

    assert service._drain_backlog(NOW).runs_created == 0
    assert store.runs == []


def test_uncapped_preset_has_nothing_to_drain() -> None:
    service, store = _service(_preset(max_jobs_per_run=0))
    service._dispatch_due_presets(NOW)
    _finish(store)

    assert len(store.runs[0].jobs) == 5
    assert service._drain_backlog(NOW + timedelta(minutes=2)).runs_created == 0


def _session_factory(session):
    factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
    event.listen(factory, "before_flush", _assign_missing_uuid_primary_keys)
    event.listen(factory, "before_flush", _assign_missing_autoincrement_primary_keys)
    return factory


def _seed(session):
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    destination = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="trino", config={})
    tables = [
        Table(id=uuid4(), source_id=source.id, schema_name="s", table_name=f"t{i}")
        for i in range(3)
    ]
    session.add_all([source, destination, *tables])
    session.flush()
    preset = Preset(
        id=uuid4(),
        name=f"p-{uuid4()}",
        destination_id=destination.id,
        operations=[],
        params={},
        guardrails={},
    )
    session.add(preset)
    session.flush()
    return preset, destination, tables


def _run(session, preset, destination, jobs, *, created_at, trigger="cron"):
    run = MaintenanceRun(
        id=uuid4(),
        kind="scheduled",
        preset_id=preset.id,
        trigger=trigger,
        status="queued",
        created_at=created_at,
    )
    session.add(run)
    session.flush()
    for table, status in jobs:
        session.add(
            MaintenanceJob(
                id=uuid4(),
                run_id=run.id,
                table_id=table.id,
                destination_id=destination.id,
                mode="standard",
                status=status,
            )
        )
    session.flush()
    return run


def test_sql_backlog_state_counts_in_flight_and_attempted_since(session) -> None:
    preset, destination, (t0, t1, t2) = _seed(session)
    _run(session, preset, destination, [(t0, "running")], created_at=NOW - timedelta(days=1))
    _run(
        session,
        preset,
        destination,
        [(t1, "failed"), (t2, "deferred")],
        created_at=NOW + timedelta(minutes=1),
    )
    session.commit()

    store = SQLSchedulerStore(_session_factory(session), redis_client=None)
    in_flight, attempted = store.preset_backlog_state(str(preset.id), NOW)

    assert in_flight == 1
    assert attempted == {str(t1.id), str(t2.id)}


def test_nightly_funnel_includes_the_backlog_batches(session) -> None:
    preset, destination, (t0, t1, t2) = _seed(session)
    cron = _run(session, preset, destination, [(t0, "succeeded")], created_at=NOW)
    backlog = _run(
        session,
        preset,
        destination,
        [(t1, "succeeded")],
        created_at=NOW + timedelta(hours=3),
        trigger=BACKLOG_TRIGGER,
    )
    _run(
        session,
        preset,
        destination,
        [(t2, "succeeded")],
        created_at=NOW - timedelta(hours=20),
        trigger=BACKLOG_TRIGGER,
    )
    session.commit()

    assert nightly_run_ids(session, cron) == [cron.id, backlog.id]
