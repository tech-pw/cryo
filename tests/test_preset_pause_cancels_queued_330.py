"""#330: pausing a preset cancels its not-yet-admitted jobs; admitted and running jobs finish."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from cryo.api import routes_preset_lifecycle
from cryo.api.routes_preset_lifecycle import PRESET_PAUSED_JOB_ERROR
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    Source,
    Table,
)
from tests.auth_helpers import ADMIN_HEADERS
from tests.test_presets_api_db import ApiDb

CRON = "0 2 * * *"


@dataclass
class _Queue:
    cancelled: list[tuple[str, str | None]] = field(default_factory=list)

    def cancel_pending(self, job_id: str, destination_id: str | None = None) -> None:
        self.cancelled.append((job_id, destination_id))


@dataclass
class _Wiring:
    queue: _Queue = field(default_factory=_Queue)


@pytest.fixture()
def wiring(monkeypatch: pytest.MonkeyPatch) -> _Wiring:
    shared = _Wiring()
    monkeypatch.setattr(routes_preset_lifecycle, "build_runtime_wiring", lambda: shared)
    return shared


@dataclass(frozen=True)
class _Seed:
    preset_id: uuid.UUID
    other_preset_id: uuid.UUID
    destination_id: uuid.UUID
    jobs: dict[str, uuid.UUID]
    runs: dict[str, uuid.UUID]


def _seed(api_db: ApiDb) -> _Seed:
    destination_id, source_id = uuid.uuid4(), uuid.uuid4()
    preset_id, other_preset_id = uuid.uuid4(), uuid.uuid4()
    runs = {name: uuid.uuid4() for name in ("mixed", "waiting", "scheduled", "done", "other")}
    jobs: dict[str, uuid.UUID] = {}
    with api_db.session_factory() as session:
        session.add(Destination(id=destination_id, name="trino", engine="trino", config={}))
        session.add(Source(id=source_id, name="hms", type="rest", config={}))
        for pid, name in ((preset_id, "held"), (other_preset_id, "other")):
            session.add(
                Preset(
                    id=pid,
                    name=name,
                    destination_id=destination_id,
                    operations=["optimize"],
                    params={},
                    guardrails={},
                    schedule_cron=CRON,
                )
            )
        statuses = {
            "mixed": "running",
            "waiting": "queued",
            "scheduled": "scheduled",
            "done": "partial",
            "other": "queued",
        }
        for name, run_id in runs.items():
            session.add(
                MaintenanceRun(
                    id=run_id,
                    kind="maintenance",
                    preset_id=other_preset_id if name == "other" else preset_id,
                    trigger="cron",
                    status=statuses[name],
                    scheduled_at=(
                        datetime.now(UTC) + timedelta(hours=1) if name == "scheduled" else None
                    ),
                )
            )
        session.flush()

        def job(key: str, run: str, status: str) -> None:
            table_id = uuid.uuid4()
            session.add(
                Table(id=table_id, source_id=source_id, schema_name="sales", table_name=key)
            )
            session.flush()
            jobs[key] = uuid.uuid4()
            session.add(
                MaintenanceJob(
                    id=jobs[key],
                    run_id=runs[run],
                    table_id=table_id,
                    destination_id=destination_id,
                    mode="standard",
                    status=status,
                    deferred_since=datetime.now(UTC) if status == "deferred" else None,
                )
            )

        job("mixed_queued", "mixed", "queued")
        job("mixed_deferred", "mixed", "deferred")
        job("mixed_admitted", "mixed", "admitted")
        job("mixed_running", "mixed", "running")
        job("mixed_succeeded", "mixed", "succeeded")
        job("waiting_queued", "waiting", "queued")
        job("waiting_deferred", "waiting", "deferred")
        job("scheduled_queued", "scheduled", "queued")
        job("done_deferred", "done", "deferred")
        job("other_queued", "other", "queued")
        session.commit()
    return _Seed(preset_id, other_preset_id, destination_id, jobs, runs)


def _job_states(api_db: ApiDb, seed: _Seed) -> dict[str, tuple[str, str | None]]:
    with api_db.session_factory() as session:
        rows = {job.id: (job.status, job.error) for job in session.scalars(select(MaintenanceJob))}
    return {key: rows[job_id] for key, job_id in seed.jobs.items()}


def _run_states(api_db: ApiDb, seed: _Seed) -> dict[str, str]:
    with api_db.session_factory() as session:
        rows = {run.id: run.status for run in session.scalars(select(MaintenanceRun))}
    return {key: rows[run_id] for key, run_id in seed.runs.items()}


def _paused_audit(api_db: ApiDb) -> list[str]:
    with api_db.session_factory() as session:
        return [
            row.detail or ""
            for row in session.scalars(select(AuditLog).where(AuditLog.action == "preset_paused"))
        ]


def _assert_paused_outcome(api_db: ApiDb, seed: _Seed, wiring: _Wiring) -> None:
    cancelled = ("skipped", PRESET_PAUSED_JOB_ERROR)
    assert _job_states(api_db, seed) == {
        "mixed_queued": cancelled,
        "mixed_deferred": cancelled,
        "mixed_admitted": ("admitted", None),
        "mixed_running": ("running", None),
        "mixed_succeeded": ("succeeded", None),
        "waiting_queued": cancelled,
        "waiting_deferred": cancelled,
        "scheduled_queued": cancelled,
        "done_deferred": ("deferred", None),
        "other_queued": ("queued", None),
    }
    assert _run_states(api_db, seed) == {
        "mixed": "running",
        "waiting": "cancelled",
        "scheduled": "cancelled",
        "done": "partial",
        "other": "queued",
    }
    expected_queue = {
        (str(seed.jobs[key]), str(seed.destination_id))
        for key in (
            "mixed_queued",
            "mixed_deferred",
            "waiting_queued",
            "waiting_deferred",
            "scheduled_queued",
        )
    }
    assert set(wiring.queue.cancelled) == expected_queue
    (detail,) = _paused_audit(api_db)
    assert "cancelled_jobs=5 cancelled_runs=2" in detail


def test_pause_cancels_unadmitted_jobs_and_lets_admitted_and_running_finish(
    api_db: ApiDb, wiring: _Wiring
) -> None:
    seed = _seed(api_db)
    response = api_db.client.post(f"/api/v1/presets/{seed.preset_id}/pause", headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["paused"] is True
    _assert_paused_outcome(api_db, seed, wiring)
    with api_db.session_factory() as session:
        waiting = session.get(MaintenanceRun, seed.runs["waiting"])
        assert waiting is not None and waiting.finished_at is not None
        assert (waiting.summary or {}).get("cancelled_reason") == "preset_paused"
        cancelled_deferred = session.get(MaintenanceJob, seed.jobs["waiting_deferred"])
        assert cancelled_deferred is not None and cancelled_deferred.deferred_since is None
        untouched = session.get(MaintenanceJob, seed.jobs["done_deferred"])
        assert untouched is not None and untouched.deferred_since is not None


def test_patch_paused_true_cancels_the_same_jobs(api_db: ApiDb, wiring: _Wiring) -> None:
    seed = _seed(api_db)
    response = api_db.client.patch(
        f"/api/v1/presets/{seed.preset_id}", headers=ADMIN_HEADERS, json={"paused": True}
    )
    assert response.status_code == 200, response.text
    _assert_paused_outcome(api_db, seed, wiring)


def test_pausing_an_already_paused_preset_cancels_nothing(api_db: ApiDb, wiring: _Wiring) -> None:
    seed = _seed(api_db)
    with api_db.session_factory() as session:
        preset = session.get(Preset, seed.preset_id)
        assert preset is not None
        preset.paused = True
        session.commit()
    for call in (
        lambda: api_db.client.post(
            f"/api/v1/presets/{seed.preset_id}/pause", headers=ADMIN_HEADERS
        ),
        lambda: api_db.client.patch(
            f"/api/v1/presets/{seed.preset_id}", headers=ADMIN_HEADERS, json={"paused": True}
        ),
    ):
        assert call().status_code == 200
    assert _job_states(api_db, seed)["waiting_queued"] == ("queued", None)
    assert wiring.queue.cancelled == []
    assert _paused_audit(api_db) == []


def test_resume_and_other_patches_cancel_nothing(api_db: ApiDb, wiring: _Wiring) -> None:
    seed = _seed(api_db)
    assert (
        api_db.client.patch(
            f"/api/v1/presets/{seed.preset_id}", headers=ADMIN_HEADERS, json={"priority": 7}
        ).status_code
        == 200
    )
    assert (
        api_db.client.post(
            f"/api/v1/presets/{seed.preset_id}/resume", headers=ADMIN_HEADERS
        ).status_code
        == 200
    )
    assert _job_states(api_db, seed)["waiting_queued"] == ("queued", None)
    assert wiring.queue.cancelled == []


def test_a_queue_failure_does_not_undo_the_cancellation(
    api_db: ApiDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_redis() -> object:
        raise ConnectionError("redis down")

    monkeypatch.setattr(routes_preset_lifecycle, "build_runtime_wiring", _no_redis)
    seed = _seed(api_db)
    response = api_db.client.post(f"/api/v1/presets/{seed.preset_id}/pause", headers=ADMIN_HEADERS)
    assert response.status_code == 200
    assert _job_states(api_db, seed)["waiting_queued"] == ("skipped", PRESET_PAUSED_JOB_ERROR)


def test_pause_is_described_in_the_openapi_schema(api_db: ApiDb) -> None:
    schema = api_db.client.get("/openapi.json").json()
    pause = schema["paths"]["/api/v1/presets/{preset_id}/pause"]["post"]
    assert "not yet admitted" in pause["description"]
    patch_paused = schema["components"]["schemas"]["PresetPatch"]["properties"]["paused"]
    assert "cancels" in patch_paused["description"]


def test_scheduled_run_dispatch_enqueues_only_jobs_still_queued(
    api_db: ApiDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryo.queue.redis_queue import InMemoryMaintenanceQueue
    from cryo.scheduler import service as scheduler_service
    from cryo.scheduler.service import InMemorySchedulerStore, SchedulerService

    seed = _seed(api_db)
    with api_db.session_factory() as session:
        run = session.get(MaintenanceRun, seed.runs["scheduled"])
        assert run is not None
        run.scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
        skipped = session.get(MaintenanceJob, seed.jobs["scheduled_queued"])
        assert skipped is not None
        session.add(
            MaintenanceJob(
                id=uuid.uuid4(),
                run_id=run.id,
                table_id=skipped.table_id,
                destination_id=seed.destination_id,
                mode="standard",
                status="queued",
            )
        )
        skipped.status = "skipped"
        session.commit()
        live_id = next(
            job.id
            for job in session.scalars(
                select(MaintenanceJob).where(
                    MaintenanceJob.run_id == run.id, MaintenanceJob.status == "queued"
                )
            )
        )

    monkeypatch.setattr(scheduler_service, "SessionLocal", api_db.session_factory)
    queue = InMemoryMaintenanceQueue()
    SchedulerService(InMemorySchedulerStore(), queue)._dispatch_scheduled_runs(datetime.now(UTC))
    assert queue.pending_by_destination() == {str(seed.destination_id): [str(live_id)]}
