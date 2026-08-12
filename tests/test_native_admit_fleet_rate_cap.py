"""Regression coverage for a real incident, 2026-08-11 evening: 11,824 native jobs ran
in one day (100+ admitted in single-minute windows), with 31% of that day's jobs failing
-- a Hive metastore OOM and a concurrent native/fallback write race both traced back to
that same admission burst. The metastore-concurrency cap (see
test_native_admit_metastore_cap.py) and the concurrent-write-race fix
(native_admit.py's snapshot-mutation defer) each close one specific mechanism that burst
triggered, but neither bounds the burst itself -- a different downstream bottleneck could
still choke on the same admission volume. These tests cover the backstop: a blunt,
mechanism-agnostic ceiling on fleet-wide admission rate, checked at native-engine
admission time alongside (not instead of) the more targeted guardrails.
"""

from __future__ import annotations

from unittest.mock import patch

from cryo.domain.enums import Mode
from cryo.governor.native_admit import _FleetAdmissionRateLimiter, admit_native_k8s_jobs
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


def _store_with_job(job: MaintenanceJobRecord) -> InMemoryJobStore:
    store = InMemoryJobStore()
    store.add_job(job)
    store.add_table_data(
        job.table_id,
        TableMaintenanceData(
            stats={"total_files": 100, "total_size_gb": 10},
            partitions=(),
            source_config={"uri": "thrift://metastore:9083", "warehouse": "s3://wh/"},
        ),
    )
    return store


class _FakeSettings:
    fleet_admission_rate_cap = 5
    fleet_admission_rate_window_minutes = 15
    max_concurrent_jobs_per_metastore = 150


def test_rate_limiter_treats_no_recent_admissions_as_fully_open(monkeypatch) -> None:
    monkeypatch.setattr("cryo.governor.native_admit.get_settings", lambda: _FakeSettings())
    store = InMemoryJobStore()
    store.count_jobs_started_since = lambda cutoff: 0  # type: ignore[attr-defined]

    limiter = _FleetAdmissionRateLimiter(store)

    assert limiter.has_headroom() is True


def test_rate_limiter_blocks_when_at_cap(monkeypatch) -> None:
    monkeypatch.setattr("cryo.governor.native_admit.get_settings", lambda: _FakeSettings())
    store = InMemoryJobStore()
    store.count_jobs_started_since = lambda cutoff: 5  # type: ignore[attr-defined]  # == cap

    limiter = _FleetAdmissionRateLimiter(store)

    assert limiter.has_headroom() is False


def test_rate_limiter_blocks_when_already_over_cap(monkeypatch) -> None:
    """A burst that already exceeded the cap before this check even ran (e.g. a spike
    that happened between ticks) must not wrap negative or read as having headroom."""
    monkeypatch.setattr("cryo.governor.native_admit.get_settings", lambda: _FakeSettings())
    store = InMemoryJobStore()
    store.count_jobs_started_since = lambda cutoff: 500  # type: ignore[attr-defined]

    limiter = _FleetAdmissionRateLimiter(store)

    assert limiter.has_headroom() is False


def test_rate_limiter_reserve_decrements_and_can_exhaust(monkeypatch) -> None:
    monkeypatch.setattr("cryo.governor.native_admit.get_settings", lambda: _FakeSettings())
    store = InMemoryJobStore()
    store.count_jobs_started_since = lambda cutoff: 2  # type: ignore[attr-defined]  # 3 free

    limiter = _FleetAdmissionRateLimiter(store)
    assert limiter.has_headroom() is True
    limiter.reserve(2)
    assert limiter.has_headroom() is True  # 1 free
    limiter.reserve(1)
    assert limiter.has_headroom() is False  # 0 free


def test_rate_limiter_reserve_handles_multi_member_specs(monkeypatch) -> None:
    """A shared-pod dispatch spec can admit several jobs at once -- reserve(n) must
    consume n slots in one call, not just decrement by one regardless of batch size."""
    monkeypatch.setattr("cryo.governor.native_admit.get_settings", lambda: _FakeSettings())
    store = InMemoryJobStore()
    store.count_jobs_started_since = lambda cutoff: 0  # type: ignore[attr-defined]  # 5 free

    limiter = _FleetAdmissionRateLimiter(store)
    limiter.reserve(4)
    assert limiter.has_headroom() is True  # 1 free
    limiter.reserve(2)  # would go to -1
    assert limiter.has_headroom() is False  # clamped at 0, not negative


def test_rate_limiter_missing_counter_method_means_no_cap_enforced() -> None:
    """A JobStore without count_jobs_started_since (e.g. a hand-rolled test double that
    predates this feature) must not block admission -- duck-typed like
    count_running_jobs_by_metastore_uri elsewhere in this same module."""
    store = InMemoryJobStore()  # no counter attribute at all

    limiter = _FleetAdmissionRateLimiter(store)

    assert limiter.has_headroom() is True


def test_admit_defers_when_fleet_rate_cap_exhausted(monkeypatch) -> None:
    """Full integration through admit_native_k8s_jobs: with the fleet already at its
    admission-rate cap, a job that would otherwise be perfectly admissible (plenty of
    cluster and metastore headroom) still gets deferred, with its table lock released --
    same safety contract as every other deferral reason in this module."""
    monkeypatch.setattr("cryo.governor.native_admit.get_settings", lambda: _FakeSettings())
    job = _job("job-1", "table-1")
    store = _store_with_job(job)
    store.count_jobs_started_since = lambda cutoff: 5  # type: ignore[attr-defined]  # at cap
    store.count_running_jobs_by_metastore_uri = lambda: {}  # type: ignore[attr-defined]
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
    assert deferred_calls == [("job-1", "fleet_admission_rate_cap")]
    assert released_calls == [("table-1", "job-1")]


def test_admit_proceeds_when_fleet_rate_cap_has_headroom(monkeypatch) -> None:
    """The other direction: plenty of fleet-wide headroom must not block an otherwise-
    admissible job. Confirms this isn't a blanket new blocker."""
    monkeypatch.setattr("cryo.governor.native_admit.get_settings", lambda: _FakeSettings())
    job = _job("job-1", "table-1")
    store = _store_with_job(job)
    store.count_jobs_started_since = lambda cutoff: 0  # type: ignore[attr-defined]
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

    assert not any(reason == "fleet_admission_rate_cap" for _, reason in deferred_calls)


def _seed_jobs_for_rate_count(session):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table

    source = Source(
        id=uuid4(), name=f"source-{uuid4()}", type="rest", config={"catalog_name": "lake"}
    )
    session.add(source)
    session.flush()
    table = Table(
        id=uuid4(), source_id=source.id, schema_name="s", table_name="t", is_active=True
    )
    session.add(table)
    session.flush()
    dest = Destination(id=uuid4(), name=f"dest-{uuid4()}", engine="native", config={})
    session.add(dest)
    session.flush()
    run = MaintenanceRun(id=uuid4(), kind="adhoc", status="running")
    session.add(run)
    session.flush()

    now = datetime.now(UTC)

    def _job_at(started_at, status="succeeded"):
        return MaintenanceJob(
            id=uuid4(),
            run_id=run.id,
            table_id=table.id,
            destination_id=dest.id,
            mode="standard",
            status=status,
            started_at=started_at,
        )

    session.add_all(
        [
            _job_at(now - timedelta(minutes=2)),
            _job_at(now - timedelta(minutes=5)),
            _job_at(now - timedelta(minutes=10)),
            _job_at(now - timedelta(minutes=20)),  # outside a 15-min window
            _job_at(None, status="queued"),  # never started -- must not be counted
        ]
    )
    session.commit()
    return now


def test_sql_job_store_counts_jobs_started_since_cutoff(session) -> None:
    from datetime import timedelta

    from sqlalchemy.orm import sessionmaker

    from cryo.db.repositories.job_store import SQLJobStore

    now = _seed_jobs_for_rate_count(session)

    store = SQLJobStore(sessionmaker(bind=session.get_bind(), expire_on_commit=False))

    assert store.count_jobs_started_since(now - timedelta(minutes=15)) == 3
    assert store.count_jobs_started_since(now - timedelta(minutes=30)) == 4
