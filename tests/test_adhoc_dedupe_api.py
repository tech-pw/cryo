from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from cryo.api import routes
from cryo.db.models import MaintenanceJob, MaintenanceRun
from cryo.queue.locks import InMemoryLockStore
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from tests.auth_helpers import OPERATOR_HEADERS
from tests.test_presets_api_db import (
    ApiDb,
    _create_preset,
    _seed_source_destination,
    _seed_tables,
)


class _Wiring:
    def __init__(self) -> None:
        self.queue = InMemoryMaintenanceQueue()
        self.lock_store = InMemoryLockStore()
        self.run_events = None


@pytest.fixture()
def wiring(monkeypatch: pytest.MonkeyPatch) -> _Wiring:
    shared = _Wiring()
    monkeypatch.setattr(routes, "build_runtime_wiring", lambda: shared)
    return shared


def _submit(api_db: ApiDb, destination_id, preset_id, table_ids, **extra):
    return api_db.client.post(
        "/api/v1/maintenance/run",
        headers=OPERATOR_HEADERS,
        json={
            "destination_id": str(destination_id),
            "preset_id": preset_id,
            "table_ids": [str(table_id) for table_id in table_ids],
            **extra,
        },
    )


def _setup(api_db: ApiDb):
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    hot, cold = _seed_tables(api_db.session_factory, source_id)
    preset_id = _create_preset(api_db.client, "standard", destination_id, priority=100)
    return destination_id, preset_id, hot, cold


def test_second_identical_run_is_refused_with_the_covering_job(
    api_db: ApiDb, wiring: _Wiring
) -> None:
    destination_id, preset_id, hot, _ = _setup(api_db)

    first = _submit(api_db, destination_id, preset_id, [hot], mode="standard")
    second = _submit(api_db, destination_id, preset_id, [hot], mode="standard")

    assert first.status_code == 200 or first.status_code == 201
    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["code"] == "already_queued"
    assert detail["skipped"][0]["covering_job_ids"] == first.json()["job_ids"]


def test_run_skips_covered_tables_and_reports_them_in_response_and_summary(
    api_db: ApiDb, wiring: _Wiring
) -> None:
    destination_id, preset_id, hot, cold = _setup(api_db)
    _submit(api_db, destination_id, preset_id, [hot], mode="standard")

    response = _submit(api_db, destination_id, preset_id, [hot, cold], mode="standard")

    assert response.status_code == 201
    body = response.json()
    assert len(body["job_ids"]) == 1
    assert [entry["table_id"] for entry in body["skipped"]] == [str(hot)]
    assert body["skipped"][0]["fully_skipped"] is True
    with api_db.session_factory() as session:
        run = session.get(MaintenanceRun, uuid.UUID(body["run_id"]))
        assert run is not None
        assert run.summary["dedupe"]["tables_skipped"] == 1
        assert run.summary["dedupe"]["details"][0]["table_id"] == str(hot)


def test_partially_covered_table_gets_a_trimmed_job(api_db: ApiDb, wiring: _Wiring) -> None:
    destination_id, preset_id, hot, _ = _setup(api_db)
    _submit(api_db, destination_id, preset_id, [hot], mode="light")

    response = _submit(api_db, destination_id, preset_id, [hot], mode="standard")

    assert response.status_code == 201
    body = response.json()
    assert body["skipped"][0]["fully_skipped"] is False
    assert set(body["skipped"][0]["operations"]) == {"expire_snapshots", "expire_data"}
    with api_db.session_factory() as session:
        job = session.get(MaintenanceJob, uuid.UUID(body["job_ids"][0]))
        assert job is not None
        assert "optimize" in job.operations
        assert "expire_snapshots" not in job.operations
        assert job.dedupe_key is not None


def test_force_creates_the_job_anyway_without_a_dedupe_key(api_db: ApiDb, wiring: _Wiring) -> None:
    destination_id, preset_id, hot, _ = _setup(api_db)
    _submit(api_db, destination_id, preset_id, [hot], mode="standard")

    response = _submit(api_db, destination_id, preset_id, [hot], mode="standard", force=True)

    assert response.status_code == 201
    assert response.json()["skipped"] == []
    with api_db.session_factory() as session:
        job = session.get(MaintenanceJob, uuid.UUID(response.json()["job_ids"][0]))
        assert job is not None
        assert job.dedupe_key is None
        assert job.operations is None


def test_finished_jobs_do_not_block_a_new_run(api_db: ApiDb, wiring: _Wiring) -> None:
    destination_id, preset_id, hot, _ = _setup(api_db)
    first = _submit(api_db, destination_id, preset_id, [hot], mode="standard")
    with api_db.session_factory() as session:
        job = session.scalar(select(MaintenanceJob).where(MaintenanceJob.table_id == hot))
        assert job is not None
        job.status = "succeeded"
        session.commit()

    second = _submit(api_db, destination_id, preset_id, [hot], mode="standard")

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["skipped"] == []


def test_scheduled_runs_are_not_deduped(api_db: ApiDb, wiring: _Wiring) -> None:
    from datetime import UTC, datetime, timedelta

    destination_id, preset_id, hot, _ = _setup(api_db)
    _submit(api_db, destination_id, preset_id, [hot], mode="standard")

    response = _submit(
        api_db,
        destination_id,
        preset_id,
        [hot],
        mode="standard",
        scheduled_at=(datetime.now(UTC) + timedelta(hours=2)).isoformat(),
    )

    assert response.status_code == 201
    assert response.json()["skipped"] == []


def test_table_operation_endpoint_honours_force(api_db: ApiDb, wiring: _Wiring) -> None:
    destination_id, preset_id, hot, _ = _setup(api_db)
    client = api_db.client
    assignment = client.post(
        "/api/v1/assignments",
        headers=OPERATOR_HEADERS,
        json={
            "preset_id": preset_id,
            "selection": {"mode": "ids", "ids": [str(hot)]},
            "assigned_by": "pytest",
        },
    )
    assert assignment.status_code == 200
    url = f"/api/v1/tables/{hot}/maintenance/operations"
    body = {"operations": ["expire_snapshots"]}

    first = client.post(url, headers=OPERATOR_HEADERS, json=body)
    second = client.post(url, headers=OPERATOR_HEADERS, json=body)
    forced = client.post(url, headers=OPERATOR_HEADERS, json={**body, "force": True})

    assert first.status_code == 201
    assert second.status_code == 409
    assert forced.status_code == 201
