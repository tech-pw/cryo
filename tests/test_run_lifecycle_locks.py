"""Run-lifecycle regressions: deferred jobs keeping their table lock (#196), runs that never
finalize while a gate keeps re-deferring a job (#167), and API-triggered runs staying queued
(#153)."""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.api.schemas import MaintenanceRunRequest
from cryo.api.security import Principal, Role
from cryo.config import get_settings
from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Preset, Source, Table
from cryo.db.repositories.job_store import SQLJobStore
from cryo.db.repositories.run_finalizer import finalize_open_runs
from cryo.domain.enums import EngineType, JobStatus, Mode
from cryo.governor.admission import AdmissionConfig
from cryo.governor.governor import (
    Governor,
    _still_blocked_predicate,
    _still_blocked_reason,
    _WaveTally,
)
from cryo.governor.native_admit import NativeAdmitResult
from cryo.maintenance.run_service import start_maintenance_run
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.reconcile import reconcile_deferred_jobs
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import DestinationConfig, InMemoryJobStore, MaintenanceJobRecord

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
DEST = "dest"


# --- #196: deferring after the lock is taken must release it -------------------------------


def _record(job_id: str, *, status: JobStatus = JobStatus.QUEUED) -> MaintenanceJobRecord:
    return MaintenanceJobRecord(
        id=job_id,
        run_id="run-1",
        table_id=f"table-{job_id}",
        destination_id=DEST,
        mode=Mode.STANDARD,
        status=status,
    )


def _governor(
    jobs: list[MaintenanceJobRecord],
) -> tuple[Governor, InMemoryMaintenanceQueue, InMemoryJobStore, InMemoryLockStore]:
    queue = InMemoryMaintenanceQueue()
    store = InMemoryJobStore()
    locks = InMemoryLockStore()
    store.add_destination(
        DestinationConfig(
            id=DEST,
            engine=EngineType.SPARK_EMR,
            config={"replicas": ["r1"], "adaptive_budget": False},
        )
    )
    for job in jobs:
        store.add_job(job)
        queue.enqueue(job.id, DEST)
    governor = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(),
        session_factory=lambda: nullcontext(MagicMock()),
        enable_signal_triggers=False,
    )
    return governor, queue, store, locks


def test_native_fallback_without_replica_slots_releases_the_table_lock(monkeypatch) -> None:
    job = _record("job-1")
    governor, queue, store, locks = _governor([job])
    assert locks.acquire_table_lock(job.table_id, job.id, ttl_seconds=6 * 3600)
    monkeypatch.setattr(
        "cryo.governor.governor.admit_native_k8s_jobs",
        lambda **_kwargs: NativeAdmitResult(fallback_to_worker=1),
    )
    wave = MagicMock()
    wave.destination_id = DEST

    governor._dispatch_native_k8s(
        [job], wave, replicas=[], tally=_WaveTally(), dispatched_job_ids=set()
    )

    assert store.get_job(job.id).status is JobStatus.DEFERRED
    assert store.get_job(job.id).error == "no_replica_slots"
    assert not locks.is_table_locked(job.table_id)

    requeued = reconcile_deferred_jobs(
        store,
        queue,
        now=NOW,
        blocked_reason=_still_blocked_reason(None, locks, now=NOW),
    )

    assert requeued == 1
    assert store.get_job(job.id).status is JobStatus.QUEUED
    assert queue.pending(DEST) == [job.id]


def test_native_defer_callback_releases_the_jobs_lock(monkeypatch) -> None:
    job = _record("job-1")
    governor, _queue, store, locks = _governor([job])
    assert locks.acquire_table_lock(job.table_id, job.id, ttl_seconds=600)

    def _admit(**kwargs):
        kwargs["defer_job"](job.id, "metastore_capacity:full")
        return NativeAdmitResult(deferred=1)

    monkeypatch.setattr("cryo.governor.governor.admit_native_k8s_jobs", _admit)
    wave = MagicMock()
    wave.destination_id = DEST
    wave.destination.config = {}

    governor._dispatch_native_k8s(
        [job], wave, replicas=["r1"], tally=_WaveTally(), dispatched_job_ids=set()
    )

    assert store.get_job(job.id).status is JobStatus.DEFERRED
    assert not locks.is_table_locked(job.table_id)


def test_defer_leaves_another_jobs_lock_alone() -> None:
    job = _record("job-1")
    governor, _queue, store, locks = _governor([job])
    assert locks.acquire_table_lock(job.table_id, "other-job", ttl_seconds=600)

    governor._defer(job, "table_locked")

    assert store.get_job(job.id).status is JobStatus.DEFERRED
    assert locks.table_lock_holder(job.table_id) == "other-job"


def test_a_jobs_own_leftover_lock_does_not_keep_it_deferred() -> None:
    locks = InMemoryLockStore()
    own = _record("own", status=JobStatus.DEFERRED)
    foreign = _record("foreign", status=JobStatus.DEFERRED)
    assert locks.acquire_table_lock(own.table_id, own.id, ttl_seconds=600)
    assert locks.acquire_table_lock(foreign.table_id, "someone-else", ttl_seconds=600)

    predicate = _still_blocked_predicate(None, locks, now=NOW)

    assert predicate(own) is False
    assert predicate(foreign) is True


# --- #167: stale reasons on jobs reconcile leaves deferred --------------------------------


def test_reconcile_replaces_a_stale_defer_reason_with_the_real_blocker() -> None:
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(DestinationConfig(DEST, config={"replicas": ["r1"]}))
    queue = InMemoryMaintenanceQueue()
    job = _record("job-1", status=JobStatus.DEFERRED)
    job.error = "table_locked"
    store.add_job(job)
    assert locks.acquire_table_lock(job.table_id, "someone-else", ttl_seconds=600)
    job.intent_id = "intent-1"
    job.intent_status = "pending"

    reconcile_deferred_jobs(
        store, queue, now=NOW, blocked_reason=_still_blocked_reason(None, locks, now=NOW)
    )

    assert store.get_job(job.id).status is JobStatus.DEFERRED
    assert store.get_job(job.id).error == "Durable intent is not ready (status=pending)"


# --- #167: grace from the first deferral, not the latest re-defer --------------------------


def _seed_run_with_job(session: Session) -> tuple[MaintenanceRun, MaintenanceJob]:
    source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4()}", type="rest", config={})
    table = Table(id=uuid.uuid4(), source_id=source.id, schema_name="s", table_name="t")
    destination = Destination(
        id=uuid.uuid4(), name=f"dest-{uuid.uuid4()}", engine="native", config={}
    )
    run = MaintenanceRun(id=uuid.uuid4(), kind="scheduled", status="queued", trigger="cron")
    session.add_all([source, table, destination, run])
    session.flush()
    job = MaintenanceJob(
        id=uuid.uuid4(),
        run_id=run.id,
        table_id=table.id,
        destination_id=destination.id,
        mode="standard",
        status="queued",
    )
    session.add(job)
    session.commit()
    return run, job


def _job_row(session_factory, job_id: uuid.UUID) -> MaintenanceJob:
    with session_factory() as fresh:
        row = fresh.get(MaintenanceJob, job_id)
        assert row is not None
        fresh.expunge(row)
        return row


def test_redefer_keeps_the_first_deferral_time(session, session_factory) -> None:
    _run, job = _seed_run_with_job(session)
    store = SQLJobStore(session_factory)

    store.mark_status(str(job.id), JobStatus.DEFERRED, error="pii")
    first = _job_row(session_factory, job.id).deferred_since
    assert first is not None

    # The reconcile re-queue and the next refusal are the same deferral episode.
    store.mark_status(str(job.id), JobStatus.QUEUED)
    store.mark_status(str(job.id), JobStatus.DEFERRED, error="pii")
    row = _job_row(session_factory, job.id)
    assert row.deferred_since == first
    assert row.finished_at is not None and row.finished_at >= first

    store.mark_status(str(job.id), JobStatus.ADMITTED)
    assert _job_row(session_factory, job.id).deferred_since is None


def test_set_deferred_reason_does_not_restart_the_clock(session, session_factory) -> None:
    _run, job = _seed_run_with_job(session)
    store = SQLJobStore(session_factory)
    store.mark_status(str(job.id), JobStatus.DEFERRED, error="table_locked")
    before = _job_row(session_factory, job.id)

    store.set_deferred_reason(str(job.id), "cooldown")

    after = _job_row(session_factory, job.id)
    assert after.error == "cooldown"
    assert after.finished_at == before.finished_at
    assert after.deferred_since == before.deferred_since


def test_run_finalizes_when_a_gate_keeps_redeferring_the_same_job(session, session_factory):
    """Live: 14 cron runs open for up to 18 days, each held by an approval/PII job whose
    finished_at was minutes old because every wave re-deferred it."""
    run, job = _seed_run_with_job(session)
    job.status = "deferred"
    job.error = "Destructive operation requires approval"
    job.deferred_since = NOW - timedelta(days=3)
    job.finished_at = NOW - timedelta(minutes=1)
    session.commit()

    assert finalize_open_runs(session_factory, now=NOW) == 1

    session.refresh(run)
    assert run.status == "partial"
    assert run.finished_at is not None


def test_freshly_deferred_job_still_gets_its_grace(session, session_factory) -> None:
    run, job = _seed_run_with_job(session)
    job.status = "deferred"
    job.error = "Destructive operation requires approval"
    job.deferred_since = NOW - timedelta(minutes=2)
    job.finished_at = NOW - timedelta(minutes=1)
    session.commit()

    assert finalize_open_runs(session_factory, now=NOW) == 0
    session.refresh(run)
    assert run.status == "queued"


def test_capacity_deferral_still_measures_from_the_latest_redefer(session, session_factory):
    run, job = _seed_run_with_job(session)
    job.status = "deferred"
    job.error = "metastore_capacity: 150/150"
    job.deferred_since = NOW - timedelta(days=1)
    job.finished_at = NOW - timedelta(minutes=1)
    session.commit()

    assert finalize_open_runs(session_factory, now=NOW) == 0
    session.refresh(run)
    assert run.status == "queued"


# --- #153: an API-triggered run finalizes once its jobs are terminal -----------------------


def _api_seed(session: Session) -> dict[str, uuid.UUID]:
    source_id, destination_id, table_id, preset_id = (uuid.uuid4() for _ in range(4))
    session.add(Source(id=source_id, name=f"src-{source_id}", type="rest", config={}))
    session.add(
        Destination(id=destination_id, name=f"dest-{destination_id}", engine="trino", config={})
    )
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name="analytics",
            table_name="events",
            is_active=True,
        )
    )
    session.add(
        Preset(
            id=preset_id,
            name=f"preset-{preset_id}",
            destination_id=destination_id,
            mode="standard",
            operations=["optimize", "expire_snapshots"],
            params={},
            guardrails={},
            enabled=True,
        )
    )
    session.commit()
    return {
        "destination_id": destination_id,
        "table_id": table_id,
        "preset_id": preset_id,
    }


class _Wiring:
    class queue:  # noqa: N801 - stands in for an instance attribute
        @staticmethod
        def enqueue(*_args: object, **_kwargs: object) -> None:
            return None

    class run_events:  # noqa: N801
        @staticmethod
        def publish(*_args: object, **_kwargs: object) -> None:
            return None


@pytest.mark.parametrize("final_status", ["succeeded", "failed", "skipped"])
def test_api_started_run_finalizes_once_every_job_is_terminal(
    session: Session, session_factory, monkeypatch, final_status: str
) -> None:
    settings = get_settings().model_copy(
        update={
            "maintenance_writes_enabled": True,
            "plan_verifier_enabled": False,
            "durable_intents_enabled": False,
        }
    )
    monkeypatch.setattr("cryo.config.get_settings", lambda: settings)
    monkeypatch.setattr("cryo.api.routes.get_settings", lambda: settings)
    monkeypatch.setattr("cryo.api.routes.build_runtime_wiring", lambda: _Wiring())
    seed = _api_seed(session)

    created = start_maintenance_run(
        session,
        MaintenanceRunRequest(
            table_ids=[seed["table_id"]],
            destination_id=seed["destination_id"],
            preset_id=seed["preset_id"],
        ),
        Principal(subject="operator", roles=frozenset({Role.OPERATOR}), tenant_id=uuid.uuid4()),
    )
    session.commit()
    run = session.get(MaintenanceRun, created.run_id)
    assert run is not None and run.status == "queued"

    jobs = session.scalars(select(MaintenanceJob).where(MaintenanceJob.run_id == run.id)).all()
    assert jobs
    store = SQLJobStore(session_factory)
    for job in jobs:
        store.mark_status(str(job.id), JobStatus(final_status))

    assert finalize_open_runs(session_factory) == 1

    session.expire_all()
    run = session.get(MaintenanceRun, created.run_id)
    assert run is not None
    assert run.status in {"succeeded", "failed", "partial"}
    assert run.status != "queued"
    assert run.finished_at is not None
    assert run.summary is not None and run.summary["jobs_total"] == len(jobs)
