from __future__ import annotations

import threading
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from cryo.db.repositories.intents import set_pause_state
from cryo.domain.enums import EngineType, JobStatus, Mode
from cryo.domain.intents import IntentStatus
from cryo.governor.admission import AdmissionConfig
from cryo.governor.governor import Governor
from cryo.governor.native_admit import NativeAdmitResult
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.worker.worker import DestinationConfig, InMemoryJobStore, MaintenanceJobRecord

_WAVE_NOW = datetime(2026, 6, 29, 20, 0, tzinfo=UTC)


def _null_session_factory():
    return nullcontext(MagicMock())


def _patch_governor_db_side_effects(monkeypatch) -> None:
    monkeypatch.setattr("cryo.governor.governor.finalize_open_runs", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "cryo.governor.governor.prefetch_approved_operation_keys", lambda *_a, **_k: {}
    )
    monkeypatch.setattr("cryo.governor.governor.prefetch_table_stats", lambda *_a, **_k: {})
    monkeypatch.setattr("cryo.governor.governor.prefetch_paused_table_ids", lambda *_a, **_k: set())
    monkeypatch.setattr(
        "cryo.governor.governor.prefetch_paused_destination_ids", lambda *_a, **_k: set()
    )


def test_in_memory_queue_tracks_priority_admission_claim_and_inflight() -> None:
    queue = InMemoryMaintenanceQueue()

    queue.enqueue("low", "dest", priority=1)
    queue.enqueue("high", "dest", priority=10)

    assert queue.pending("dest") == ["high", "low"]
    queue.admit("high", "dest", "r1")
    assert queue.local_in_flight_by_replica("dest") == {"r1": 1}
    assert queue.claim_admitted() == "high"

    queue.mark_running("high")
    assert queue.status("high") is JobStatus.RUNNING
    queue.complete("high", JobStatus.SUCCEEDED)
    assert queue.local_in_flight("dest") == 0
    assert queue.status("high") is JobStatus.SUCCEEDED


def test_in_memory_queue_cancel_pending_removes_stale_entry_without_job_hash() -> None:
    queue = InMemoryMaintenanceQueue()
    queue._pending["missing-dest"].append((0, 1, "None"))

    queue.cancel_pending("None")

    assert queue.pending("missing-dest") == []


def test_governor_admits_wave_round_robin_across_replicas() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "dest",
            config={
                "replicas": ["r1", "r2"],
                "per_replica_cap": 1,
                "offpeak_start_ist": 0,
                "offpeak_end_ist": 7,
                "adaptive_budget": False,
            },
        )
    )
    for job_id, table_id in [("job-1", "table-1"), ("job-2", "table-2")]:
        store.add_job(
            MaintenanceJobRecord(
                id=job_id,
                run_id="run-1",
                table_id=table_id,
                destination_id="dest",
                mode=Mode.STANDARD,
            )
        )
        queue.enqueue(job_id, "dest")

    governor = Governor(queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False))
    result = governor.run_wave(now=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))

    assert result.admitted == 2
    assert store.get_job("job-1").status is JobStatus.ADMITTED
    assert store.get_job("job-2").status is JobStatus.ADMITTED
    assert [queue.entry("job-1").replica, queue.entry("job-2").replica] == ["r1", "r2"]
    assert queue.local_in_flight_by_replica("dest") == {"r1": 1, "r2": 1}
    assert locks.is_table_locked("table-1")
    assert locks.is_table_locked("table-2")


def test_governor_round_robins_bounded_candidate_window_by_tenant() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "dest",
            config={
                "replicas": ["r1", "r2"],
                "per_replica_cap": 1,
                "adaptive_budget": False,
            },
        )
    )
    jobs = [
        ("tenant-a-1", "tenant-a"),
        ("tenant-a-2", "tenant-a"),
        ("tenant-b-1", "tenant-b"),
        ("tenant-b-2", "tenant-b"),
    ]
    for job_id, tenant_id in jobs:
        store.add_job(
            MaintenanceJobRecord(
                id=job_id,
                run_id="run-1",
                table_id=f"table-{job_id}",
                destination_id="dest",
                mode=Mode.STANDARD,
                tenant_id=tenant_id,
            )
        )
        queue.enqueue(job_id, "dest")

    result = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(adaptive_budget=False),
    ).run_wave(now=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))

    assert result.admitted == 2
    assert store.get_job("tenant-a-1").status is JobStatus.ADMITTED
    assert store.get_job("tenant-b-1").status is JobStatus.ADMITTED
    assert store.get_job("tenant-a-2").status is not JobStatus.ADMITTED


def test_governor_defers_expired_durable_intent() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "dest",
            config={"replicas": ["r1"], "adaptive_budget": False},
        )
    )
    job = MaintenanceJobRecord(
        id="expired-job",
        run_id="run-1",
        table_id="table-1",
        destination_id="dest",
        mode=Mode.STANDARD,
        intent_id="intent-1",
        intent_status=IntentStatus.READY.value,
        intent_expires_at=datetime(2026, 6, 29, 19, 59, tzinfo=UTC),
    )
    store.add_job(job)
    queue.enqueue(job.id, "dest")

    result = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(adaptive_budget=False),
    ).run_wave(now=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))

    assert result.deferred == 1
    assert store.get_job(job.id).status is JobStatus.DEFERRED
    assert store.get_job(job.id).error == "Durable intent expired before admission"


def test_governor_defers_when_fallback_effect_destination_is_paused(
    session: Session,
) -> None:
    fallback_destination_id = str(uuid4())
    set_pause_state(
        session,
        scope_type="destination",
        scope_id=fallback_destination_id,
        paused=True,
        changed_by="test-admin",
        reason="fallback engine incident",
    )
    session.commit()
    session_factory = sessionmaker(
        bind=session.get_bind(),
        autoflush=False,
        expire_on_commit=False,
    )
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "native-dest",
            engine="native",
            config={"replicas": ["r1"], "adaptive_budget": False},
        )
    )
    job = MaintenanceJobRecord(
        id="fallback-job",
        run_id="run-1",
        table_id="table-1",
        destination_id="native-dest",
        mode=Mode.STANDARD,
        intent_id="intent-1",
        intent_status=IntentStatus.READY.value,
        intent_expires_at=datetime.now(UTC) + timedelta(hours=1),
        intent_effect_destination_ids=(fallback_destination_id,),
    )
    store.add_job(job)
    queue.enqueue(job.id, "native-dest")

    result = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(adaptive_budget=False),
        session_factory=session_factory,
    ).run_wave(now=datetime.now(UTC))

    assert result.deferred == 1
    assert store.get_job(job.id).status is JobStatus.DEFERRED
    assert store.get_job(job.id).error == "Maintenance execution is paused"


def test_governor_fleet_budget_admits_highest_moop_score_first() -> None:
    """AutoComp §A2 'Fleet Decide' wired into the real wave loop: enough replica slots for
    both jobs, but fleet_budget_gb only has room for one -- the higher moop_score table wins,
    the other is deferred with budget_exhausted, not silently admitted anyway."""
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "dest",
            config={
                "replicas": ["r1", "r2"],
                "per_replica_cap": 5,
                "adaptive_budget": False,
                "fleet_budget_gb": 10.0,
            },
        )
    )
    store.add_job(
        MaintenanceJobRecord(
            id="cold-job",
            run_id="run-1",
            table_id="cold-table",
            destination_id="dest",
            mode=Mode.LIGHT,
            params={"moop_score": 0.1, "size_gb": 10.0},
        )
    )
    store.add_job(
        MaintenanceJobRecord(
            id="hot-job",
            run_id="run-1",
            table_id="hot-table",
            destination_id="dest",
            mode=Mode.LIGHT,
            params={"moop_score": 0.9, "size_gb": 10.0},
        )
    )
    queue.enqueue("cold-job", "dest")
    queue.enqueue("hot-job", "dest")

    governor = Governor(queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False))
    result = governor.run_wave(now=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))

    assert result.admitted == 1
    assert result.deferred == 1
    assert store.get_job("hot-job").status is JobStatus.ADMITTED
    assert store.get_job("cold-job").status is JobStatus.DEFERRED
    assert store.get_job("cold-job").error == "budget_exhausted"


def test_governor_defers_heavy_jobs_outside_offpeak() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "dest",
            config={
                "replicas": ["r1"],
                "per_replica_cap": 1,
                "offpeak_start_ist": 0,
                "offpeak_end_ist": 7,
            },
        )
    )
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="dest",
            mode=Mode.STANDARD,
        )
    )
    queue.enqueue("job-1", "dest")

    result = Governor(queue, locks, store).run_wave(now=datetime(2026, 6, 30, 8, 0, tzinfo=UTC))

    assert result.deferred == 1
    assert store.get_job("job-1").status is JobStatus.DEFERRED
    assert store.get_job("job-1").error == "outside_offpeak"


def test_governor_bounds_native_k8s_scan_to_replica_budget_not_full_queue() -> None:
    """Regression, 2026-08-03: a cold-table fleet run queued 11,252 jobs against a single
    native k8s_dispatch destination. Neither of run_wave's two per-destination passes over
    job_ids/wave_jobs is naturally bounded for native jobs -- their own `if not replicas`
    throttle only trips when something pops `replicas`, which never happens on the native
    path (native jobs are packed on admit, not assigned a worker replica). So both passes
    walked the *entire* queue every 30s tick regardless of capacity: one store.get_job() call
    each in the first pass, plus a resource-predictor DB query each in the second. That
    OOMKilled the Governor at 11k+ depth and, even after a memory bump, still throttled
    admission to a fraction of what slot_cap should allow at 500 (deferred=18 of 20 in this
    exact scenario pre-fix -- every job got touched and marked deferred instead of just
    staying queued). The fix caps the first pass at the replica budget for native
    destinations, which naturally caps the second pass too since wave_jobs is built from it."""
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(
        DestinationConfig(
            "native-dest",
            engine="native",
            config={
                "k8s_dispatch": True,
                "replicas": ["r1", "r2"],
                "per_replica_cap": 1,
                "adaptive_budget": False,
            },
        )
    )
    total_jobs = 2000
    for i in range(total_jobs):
        job_id = f"job-{i}"
        store.add_job(
            MaintenanceJobRecord(
                id=job_id,
                run_id="run-1",
                table_id=f"table-{i}",
                destination_id="native-dest",
                mode=Mode.STANDARD,
            )
        )
        queue.enqueue(job_id, "native-dest")

    governor = Governor(queue, locks, store, default_config=AdmissionConfig(adaptive_budget=False))
    result = governor.run_wave(now=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))

    # Budget is 2 replica slots (per_replica_cap=1 x 2 replicas): exactly that many admitted,
    # the scan blocked (not deferred) once it hit budget, and the other ~1998 jobs were never
    # touched at all this tick -- not scanned, not deferred, just left queued for next time.
    assert result.admitted == 2
    assert result.blocked >= 1
    assert result.deferred == 0
    assert len(queue.pending("native-dest")) == total_jobs - result.admitted


def test_governor_purges_jobs_for_missing_destination() -> None:
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_job(
        MaintenanceJobRecord(
            id="job-1",
            run_id="run-1",
            table_id="table-1",
            destination_id="missing-dest",
            mode=Mode.STANDARD,
        )
    )
    queue.enqueue("job-1", "missing-dest")

    result = Governor(queue, locks, store).run_wave(now=datetime(2026, 6, 29, 20, 0, tzinfo=UTC))

    assert result.orphaned == 0
    assert store.get_job("job-1").status is JobStatus.FAILED
    assert store.get_job("job-1").error == "destination_not_found"
    assert queue.pending("missing-dest") == []


def _native_k8s_destination(dest_id: str = "native-dest") -> DestinationConfig:
    return DestinationConfig(
        dest_id,
        engine=EngineType.NATIVE,
        config={
            "k8s_dispatch": True,
            "replicas": ["r1", "r2"],
            "per_replica_cap": 1,
            "adaptive_budget": False,
            "sql_fallback_destination_id": "spark-1",
        },
    )


def _spark_fallback_destination(*, slot_cap: int) -> DestinationConfig:
    return DestinationConfig(
        id="spark-1",
        engine=EngineType.SPARK_EMR,
        config={"slot_cap": slot_cap},
    )


def _enqueue_job(
    store: InMemoryJobStore,
    queue: InMemoryMaintenanceQueue,
    *,
    job_id: str,
    table_id: str,
    destination_id: str,
) -> None:
    store.add_job(
        MaintenanceJobRecord(
            id=job_id,
            run_id="run-1",
            table_id=table_id,
            destination_id=destination_id,
            mode=Mode.STANDARD,
        )
    )
    queue.enqueue(job_id, destination_id)


def test_run_wave_caps_fallback_dispatches_to_one_slot_cap_batch(monkeypatch) -> None:
    """PDAE-8352: a cohort of optimize_manifests fallbacks must not hold run_wave()
    until every table is dispatched. slot_cap still bounds in-flight concurrency;
    the wave itself yields after one batch so the next tick can admit the async queue.
    """
    _patch_governor_db_side_effects(monkeypatch)
    slot_cap = 6
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(_native_k8s_destination())
    store.add_destination(_spark_fallback_destination(slot_cap=slot_cap))
    _enqueue_job(store, queue, job_id="job-1", table_id="table-1", destination_id="native-dest")

    ran: list[int] = []
    lock = threading.Lock()

    def make_dispatch(i: int):
        def _dispatch() -> None:
            with lock:
                ran.append(i)

        return _dispatch

    cohort = [make_dispatch(i) for i in range(49)]

    def fake_admit(*, jobs, cancel_pending, mark_status, destination, **_kwargs):
        for job in jobs:
            cancel_pending(job.id, destination.id)
            mark_status(job.id, JobStatus.RUNNING, engine_ref="native-job", replica="native-job")
        return NativeAdmitResult(
            admitted=len(jobs),
            pending_fallback_dispatches=tuple(cohort),
        )

    monkeypatch.setattr("cryo.governor.governor.admit_native_k8s_jobs", fake_admit)
    governor = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(adaptive_budget=False),
        session_factory=_null_session_factory,
    )

    result = governor.run_wave(now=_WAVE_NOW)

    assert result.admitted == 1
    assert sorted(ran) == list(range(slot_cap))
    assert len(governor._pending_fallback_dispatches) == 49 - slot_cap


def test_run_wave_drains_leftover_fallbacks_on_later_waves(monkeypatch) -> None:
    _patch_governor_db_side_effects(monkeypatch)
    slot_cap = 6
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(_native_k8s_destination())
    store.add_destination(_spark_fallback_destination(slot_cap=slot_cap))
    _enqueue_job(store, queue, job_id="job-1", table_id="table-1", destination_id="native-dest")

    ran: list[int] = []
    lock = threading.Lock()

    def make_dispatch(i: int):
        def _dispatch() -> None:
            with lock:
                ran.append(i)

        return _dispatch

    cohort = [make_dispatch(i) for i in range(49)]
    released_cohort = False

    def fake_admit(*, jobs, cancel_pending, mark_status, destination, **_kwargs):
        nonlocal released_cohort
        for job in jobs:
            cancel_pending(job.id, destination.id)
            mark_status(job.id, JobStatus.RUNNING, engine_ref="native-job", replica="native-job")
        pending = tuple(cohort) if not released_cohort else ()
        released_cohort = True
        return NativeAdmitResult(admitted=len(jobs), pending_fallback_dispatches=pending)

    monkeypatch.setattr("cryo.governor.governor.admit_native_k8s_jobs", fake_admit)
    governor = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(adaptive_budget=False),
        session_factory=_null_session_factory,
    )

    governor.run_wave(now=_WAVE_NOW)
    governor.run_wave(now=_WAVE_NOW)
    governor.run_wave(now=_WAVE_NOW)

    assert sorted(ran) == list(range(18))
    assert len(governor._pending_fallback_dispatches) == 49 - 18

    while governor._pending_fallback_dispatches:
        governor.run_wave(now=_WAVE_NOW)
    assert sorted(ran) == list(range(49))
    assert governor._pending_fallback_dispatches == []


def test_run_wave_admits_async_queue_in_same_tick_as_fallback_batch(monkeypatch) -> None:
    """Regular worker-queue destinations must still be admitted even when a native
    destination collected a large fallback cohort this wave."""
    _patch_governor_db_side_effects(monkeypatch)
    slot_cap = 3
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(_native_k8s_destination())
    store.add_destination(_spark_fallback_destination(slot_cap=slot_cap))
    store.add_destination(
        DestinationConfig(
            "trino-dest",
            config={
                "replicas": ["r1"],
                "per_replica_cap": 2,
                "adaptive_budget": False,
            },
        )
    )
    _enqueue_job(store, queue, job_id="native-1", table_id="table-n1", destination_id="native-dest")
    _enqueue_job(store, queue, job_id="trino-1", table_id="table-t1", destination_id="trino-dest")
    _enqueue_job(store, queue, job_id="trino-2", table_id="table-t2", destination_id="trino-dest")

    ran: list[int] = []

    def fake_admit(*, jobs, cancel_pending, mark_status, destination, **_kwargs):
        for job in jobs:
            cancel_pending(job.id, destination.id)
            mark_status(job.id, JobStatus.RUNNING, engine_ref="native-job", replica="native-job")
        return NativeAdmitResult(
            admitted=len(jobs),
            pending_fallback_dispatches=tuple(lambda i=i: ran.append(i) for i in range(12)),
        )

    monkeypatch.setattr("cryo.governor.governor.admit_native_k8s_jobs", fake_admit)
    governor = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(adaptive_budget=False),
        session_factory=_null_session_factory,
    )

    result = governor.run_wave(now=_WAVE_NOW)

    assert store.get_job("trino-1").status is JobStatus.ADMITTED
    assert store.get_job("trino-2").status is JobStatus.ADMITTED
    assert len(ran) == slot_cap
    assert result.admitted == 1 + 2


def test_run_wave_fallback_batch_still_respects_slot_cap_concurrency(monkeypatch) -> None:
    _patch_governor_db_side_effects(monkeypatch)
    slot_cap = 3
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(_native_k8s_destination())
    store.add_destination(_spark_fallback_destination(slot_cap=slot_cap))
    _enqueue_job(store, queue, job_id="job-1", table_id="table-1", destination_id="native-dest")

    active = 0
    max_active = 0
    lock = threading.Lock()

    def slow_dispatch() -> None:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1

    def fake_admit(*, jobs, cancel_pending, mark_status, destination, **_kwargs):
        for job in jobs:
            cancel_pending(job.id, destination.id)
            mark_status(job.id, JobStatus.RUNNING, engine_ref="native-job", replica="native-job")
        return NativeAdmitResult(
            admitted=len(jobs),
            pending_fallback_dispatches=tuple(slow_dispatch for _ in range(9)),
        )

    monkeypatch.setattr("cryo.governor.governor.admit_native_k8s_jobs", fake_admit)
    governor = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(adaptive_budget=False),
        session_factory=_null_session_factory,
    )

    start = time.perf_counter()
    governor.run_wave(now=_WAVE_NOW)
    elapsed = time.perf_counter() - start

    assert max_active > 1
    assert max_active <= slot_cap
    assert len(governor._pending_fallback_dispatches) == 6
    # One concurrent batch of ~50ms, not three serialized batches (~150ms+).
    assert elapsed < 0.14


def test_run_wave_fallback_flush_is_fifo_per_fallback_destination(monkeypatch) -> None:
    _patch_governor_db_side_effects(monkeypatch)
    queue = InMemoryMaintenanceQueue()
    locks = InMemoryLockStore()
    store = InMemoryJobStore()
    store.add_destination(_native_k8s_destination("native-a"))
    store.add_destination(
        DestinationConfig(
            "native-b",
            engine=EngineType.NATIVE,
            config={
                "k8s_dispatch": True,
                "replicas": ["r1"],
                "per_replica_cap": 1,
                "adaptive_budget": False,
                "sql_fallback_destination_id": "spark-b",
            },
        )
    )
    store.add_destination(
        DestinationConfig(id="spark-1", engine=EngineType.SPARK_EMR, config={"slot_cap": 2})
    )
    store.add_destination(
        DestinationConfig(id="spark-b", engine=EngineType.SPARK_EMR, config={"slot_cap": 2})
    )
    _enqueue_job(store, queue, job_id="a1", table_id="ta1", destination_id="native-a")
    _enqueue_job(store, queue, job_id="b1", table_id="tb1", destination_id="native-b")

    ran: list[str] = []

    def fake_admit(*, jobs, cancel_pending, mark_status, destination, **_kwargs):
        for job in jobs:
            cancel_pending(job.id, destination.id)
            mark_status(job.id, JobStatus.RUNNING, engine_ref="native-job", replica="native-job")
        tag = destination.id
        return NativeAdmitResult(
            admitted=len(jobs),
            pending_fallback_dispatches=(
                lambda t=tag: ran.append(f"{t}-1"),
                lambda t=tag: ran.append(f"{t}-2"),
                lambda t=tag: ran.append(f"{t}-3"),
            ),
        )

    monkeypatch.setattr("cryo.governor.governor.admit_native_k8s_jobs", fake_admit)
    governor = Governor(
        queue,
        locks,
        store,
        default_config=AdmissionConfig(adaptive_budget=False),
        session_factory=_null_session_factory,
    )

    governor.run_wave(now=_WAVE_NOW)
    # First destination in pending_by_destination order is native-a; only its slot_cap=2.
    assert ran == ["native-a-1", "native-a-2"]
    leftover_tags = [
        item.destination_config.get("sql_fallback_destination_id")
        for item in governor._pending_fallback_dispatches
    ]
    assert leftover_tags.count("spark-1") == 1
    assert leftover_tags.count("spark-b") == 3

    governor.run_wave(now=_WAVE_NOW)
    assert ran == ["native-a-1", "native-a-2", "native-a-3"]

    governor.run_wave(now=_WAVE_NOW)
    assert ran == [
        "native-a-1",
        "native-a-2",
        "native-a-3",
        "native-b-1",
        "native-b-2",
    ]
