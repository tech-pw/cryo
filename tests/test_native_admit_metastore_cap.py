"""Regression coverage for a real incident, 2026-08-12: a Hive metastore OOM'd (heap
exhaustion, JvmPauseMonitor and pool-6 threads all throwing OutOfMemoryError) with 322
jobs started in the surrounding 15 minutes and ~70 concurrently failing with literal
"failed to connect to metastore". Root cause: native_datafusion's own slot_cap (e.g. 80)
has no relationship to the shared Hive metastore's real connection capacity, and nothing
else in the Governor's admission path checked it -- a direct, foreseeable consequence of
PDAE-8352's async fallback-dispatch fix raising per-wave admission throughput without
anyone checking whether the downstream metastore had headroom for the resulting
concurrency. These tests cover the guardrail that closes that gap: a cross-destination
cap on RUNNING jobs sharing a metastore URI, enforced at native-engine admission time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.domain.enums import Mode
from cryo.engines.native.packing import DispatchSpec, PackableJob
from cryo.engines.native.resources import TableResourceEstimate
from cryo.governor.native_admit import (
    _MetastoreHeadroom,
    _spec_metastore_uris,
    admit_native_k8s_jobs,
)
from cryo.worker.worker import (
    DestinationConfig,
    InMemoryJobStore,
    MaintenanceJobRecord,
    TableMaintenanceData,
)


def _job(job_id: str, table_id: str) -> MaintenanceJobRecord:
    return MaintenanceJobRecord(
        id=job_id,
        run_id="r1",
        table_id=table_id,
        destination_id="dest-1",
        mode=Mode.STANDARD,
        params={},
        operations=None,
    )


def _store_with_job(
    job: MaintenanceJobRecord, uri: str = "thrift://metastore:9083"
) -> InMemoryJobStore:
    store = InMemoryJobStore()
    store.add_job(job)
    store.add_table_data(
        job.table_id,
        TableMaintenanceData(
            stats={"total_files": 100, "total_size_gb": 10},
            partitions=(),
            source_config={"uri": uri, "warehouse": "s3://wh/"},
        ),
    )
    return store


class _FakeSettings:
    max_concurrent_jobs_per_metastore = 3
    # Generous enough that these metastore-cap-focused tests never trip the separate
    # fleet-wide rate limiter (test_native_admit_fleet_rate_cap.py covers that one).
    fleet_admission_rate_cap = 100_000
    fleet_admission_rate_window_minutes = 15


def test_metastore_headroom_treats_unseen_uri_as_fully_open(monkeypatch) -> None:
    """A metastore URI with zero currently-running jobs is absent from the underlying
    GROUP BY count query -- must be treated as fully open (cap slots), not zero."""
    monkeypatch.setattr(
        "cryo.governor.native_admit.get_settings", lambda: _FakeSettings()
    )
    store = InMemoryJobStore()
    store.count_running_jobs_by_metastore_uri = lambda: {}  # type: ignore[attr-defined]

    headroom = _MetastoreHeadroom(store)

    assert headroom.has_headroom("thrift://never-seen:9083") is True


def test_metastore_headroom_blocks_uri_at_cap(monkeypatch) -> None:
    monkeypatch.setattr(
        "cryo.governor.native_admit.get_settings", lambda: _FakeSettings()
    )
    store = InMemoryJobStore()
    store.count_running_jobs_by_metastore_uri = (  # type: ignore[attr-defined]
        lambda: {"thrift://metastore:9083": 3}
    )

    headroom = _MetastoreHeadroom(store)

    assert headroom.has_headroom("thrift://metastore:9083") is False


def test_metastore_headroom_reserve_decrements_and_can_exhaust(monkeypatch) -> None:
    monkeypatch.setattr(
        "cryo.governor.native_admit.get_settings", lambda: _FakeSettings()
    )
    store = InMemoryJobStore()
    store.count_running_jobs_by_metastore_uri = (  # type: ignore[attr-defined]
        lambda: {"thrift://metastore:9083": 1}
    )

    headroom = _MetastoreHeadroom(store)
    uri = "thrift://metastore:9083"
    assert headroom.has_headroom(uri) is True  # 3 cap - 1 running = 2 free
    headroom.reserve(uri)
    assert headroom.has_headroom(uri) is True  # 1 free
    headroom.reserve(uri)
    assert headroom.has_headroom(uri) is False  # 0 free


def test_metastore_headroom_missing_counter_method_means_no_cap_enforced() -> None:
    """A JobStore without count_running_jobs_by_metastore_uri (e.g. a hand-rolled test
    double that predates this feature) must not block admission -- duck-typed like
    list_running_jobs elsewhere in this same module."""
    store = InMemoryJobStore()  # no counter attribute at all

    headroom = _MetastoreHeadroom(store)

    assert headroom.has_headroom("thrift://anything:9083") is True


def test_spec_metastore_uris_collects_each_members_source_uri() -> None:
    job_a = _job("job-a", "table-a")
    job_b = _job("job-b", "table-b")
    store_a = _store_with_job(job_a, uri="thrift://ms-1:9083")
    store_a.add_table_data(
        job_b.table_id,
        TableMaintenanceData(stats={}, partitions=(), source_config={"uri": "thrift://ms-2:9083"}),
    )
    by_job_id = {}
    from cryo.governor.native_admit import _PreparedJob

    for job, store in ((job_a, store_a), (job_b, store_a)):
        by_job_id[job.id] = _PreparedJob(
            job=job,
            table_data=store.get_table_data(job.table_id),
            stats={},
            packable=PackableJob(
                job_id=job.id,
                table_id=job.table_id,
                run_id="r1",
                estimate=TableResourceEstimate(cpu_milli=1000, mem_mib=1024, hot=False),
            ),
            mode=Mode.STANDARD,
        )

    spec = DispatchSpec(
        run_id="r1",
        members=(by_job_id["job-a"].packable, by_job_id["job-b"].packable),
        cpu_milli=2000,
        mem_mib=2048,
    )

    uris = _spec_metastore_uris(spec, by_job_id)

    assert uris == {"thrift://ms-1:9083", "thrift://ms-2:9083"}


def test_admit_defers_when_metastore_at_capacity(monkeypatch) -> None:
    """Full integration through admit_native_k8s_jobs: a job whose table's metastore is
    already at the configured cap gets deferred with a metastore_capacity reason and its
    table lock released -- same safety contract as native_cluster_capacity deferrals."""
    monkeypatch.setattr(
        "cryo.governor.native_admit.get_settings", lambda: _FakeSettings()
    )
    job = _job("job-1", "table-1")
    store = _store_with_job(job, uri="thrift://saturated:9083")
    store.count_running_jobs_by_metastore_uri = (  # type: ignore[attr-defined]
        lambda: {"thrift://saturated:9083": 3}  # already at _FakeSettings' cap of 3
    )
    destination = DestinationConfig(id="dest-1", engine="native", config={"k8s_dispatch": True})

    deferred_calls: list[tuple[str, str]] = []
    released_calls: list[tuple[str, str]] = []

    with (
        patch("cryo.governor.native_admit.k8s_dispatch_available", return_value=True),
        patch(
            "cryo.governor.native_admit.cluster_headroom",
            return_value=(10, 100_000, 100_000),
        ),
    ):
        result = admit_native_k8s_jobs(
            jobs=[job],
            destination=destination,
            store=store,
            session_factory=lambda: None,
            live_capacity={},
            mark_status=store.mark_status,
            cancel_pending=lambda *_: None,
            defer_job=lambda jid, reason: deferred_calls.append((jid, reason)),
            release_lock=lambda tid, jid: released_calls.append((tid, jid)),
        )

    assert result.admitted == 0
    assert result.deferred == 1
    assert deferred_calls == [("job-1", "metastore_capacity:thrift://saturated:9083")]
    assert released_calls == [("table-1", "job-1")]


def test_admit_proceeds_when_metastore_has_headroom(monkeypatch) -> None:
    """The other direction: plenty of metastore headroom must not block an otherwise-
    admissible job. Confirms this isn't a blanket new blocker."""
    monkeypatch.setattr(
        "cryo.governor.native_admit.get_settings", lambda: _FakeSettings()
    )
    job = _job("job-1", "table-1")
    store = _store_with_job(job, uri="thrift://roomy:9083")
    store.count_running_jobs_by_metastore_uri = lambda: {}  # type: ignore[attr-defined]
    destination = DestinationConfig(id="dest-1", engine="native", config={"k8s_dispatch": True})

    deferred_calls: list[tuple[str, str]] = []

    with (
        patch("cryo.governor.native_admit.k8s_dispatch_available", return_value=True),
        patch(
            "cryo.governor.native_admit.cluster_headroom",
            return_value=(10, 100_000, 100_000),
        ),
    ):
        admit_native_k8s_jobs(
            jobs=[job],
            destination=destination,
            store=store,
            session_factory=lambda: None,
            live_capacity={},
            mark_status=store.mark_status,
            cancel_pending=lambda *_: None,
            defer_job=lambda jid, reason: deferred_calls.append((jid, reason)),
            release_lock=lambda *_: None,
        )

    # Not asserting admitted == 1 here -- the rest of the dispatch path (plan
    # build, K8s job creation) needs more infra mocking than this test cares about.
    # What matters: it must NOT have been deferred for metastore_capacity.
    assert not any(reason.startswith("metastore_capacity") for _, reason in deferred_calls)


def _seed_source(session, *, uri: str) -> Source:
    source = Source(
        id=uuid4(),
        name=f"source-{uuid4()}",
        type="rest",
        config={"catalog_name": "lake", "uri": uri},
    )
    session.add(source)
    session.flush()
    return source


def test_sql_job_store_counts_running_jobs_grouped_by_metastore_uri(session) -> None:
    from sqlalchemy.orm import sessionmaker

    from cryo.db.repositories.job_store import SQLJobStore

    source_a = _seed_source(session, uri="thrift://ms-a:9083")
    source_b = _seed_source(session, uri="thrift://ms-b:9083")
    table_a1 = Table(
        id=uuid4(), source_id=source_a.id, schema_name="s", table_name="a1", is_active=True
    )
    table_a2 = Table(
        id=uuid4(), source_id=source_a.id, schema_name="s", table_name="a2", is_active=True
    )
    table_b1 = Table(
        id=uuid4(), source_id=source_b.id, schema_name="s", table_name="b1", is_active=True
    )
    session.add_all([table_a1, table_a2, table_b1])
    session.flush()

    dest = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="native", config={})
    session.add(dest)
    session.flush()
    run = MaintenanceRun(id=uuid4(), kind="adhoc", status="running")
    session.add(run)
    session.flush()

    def _running_job(table_id):
        return MaintenanceJob(
            id=uuid4(),
            run_id=run.id,
            table_id=table_id,
            destination_id=dest.id,
            mode="standard",
            status="running",
            started_at=datetime.now(UTC),
        )

    def _queued_job(table_id):
        return MaintenanceJob(
            id=uuid4(),
            run_id=run.id,
            table_id=table_id,
            destination_id=dest.id,
            mode="standard",
            status="queued",
        )

    session.add_all(
        [
            _running_job(table_a1.id),
            _running_job(table_a1.id),
            _running_job(table_a2.id),
            _running_job(table_b1.id),
            _queued_job(table_a1.id),  # not RUNNING -- must not be counted
        ]
    )
    session.commit()

    store = SQLJobStore(sessionmaker(bind=session.get_bind(), expire_on_commit=False))
    counts = store.count_running_jobs_by_metastore_uri()

    assert counts == {"thrift://ms-a:9083": 3, "thrift://ms-b:9083": 1}
