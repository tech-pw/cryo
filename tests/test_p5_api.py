from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from cryo.api import routes
from cryo.api.app import create_app
from cryo.api.schemas import AssignmentSelection, MaintenanceRunRequest
from cryo.config import Settings
from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun
from cryo.db.repositories.p4 import SelectionResult
from cryo.db.session import get_session

AUTH_HEADERS = {"Authorization": "Bearer operator"}


class FakeSession:
    def expire_all(self) -> None:
        return None


def _client() -> TestClient:
    app = create_app(Settings(_env_file=None, environment="test"))
    app.dependency_overrides[get_session] = lambda: FakeSession()
    return TestClient(app)


def test_dashboard_endpoints_return_bounded_aggregates(monkeypatch: Any) -> None:
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 1, 1, tzinfo=UTC)

    def datalake(session: object, *, top_limit: int, drift_limit: int) -> dict[str, object]:
        assert top_limit == 5
        assert drift_limit == 7
        return {
            "totals": {
                "total_tables": 12,
                "active_tables": 10,
                "total_size_gb": 42.5,
                "total_files": 1000,
                "small_files_count": 300,
                "needs_compaction_count": 4,
                "estimated_monthly_s3_cost_usd": 9.5,
                "query_count_30d": 25,
            },
            "grade_distribution": [{"grade": "A", "count": 3}, {"grade": "C", "count": 7}],
            "top_offenders": [
                {
                    "table_id": table_id,
                    "schema_name": "analytics",
                    "table_name": "events",
                    "grade": "C",
                    "overall_score": 61,
                    "total_size_gb": 20.0,
                    "total_files": 500,
                    "small_files_count": 250,
                    "query_count_30d": 10,
                }
            ],
            "drift": [
                {
                    "scan_at": scan_at,
                    "total_size_gb": 42.5,
                    "total_files": 1000,
                    "small_files_count": 300,
                    "avg_overall_score": 72.5,
                }
            ],
        }

    def schema_cost(session: object, *, limit: int) -> dict[str, object]:
        assert limit == 3
        return {
            "items": [
                {
                    "schema_name": "analytics",
                    "table_count": 4,
                    "total_size_gb": 20.0,
                    "estimated_monthly_s3_cost_usd": 5.5,
                    "small_files_count": 250,
                    "query_count_30d": 10,
                }
            ],
            "limit": limit,
        }

    monkeypatch.setattr(routes.p5, "dashboard_datalake", datalake)
    monkeypatch.setattr(routes.p5, "dashboard_schema_cost", schema_cost)

    client = _client()
    datalake_response = client.get(
        "/api/v1/dashboards/datalake?top_limit=5&drift_limit=7",
        headers=AUTH_HEADERS,
    )
    schema_response = client.get(
        "/api/v1/dashboards/schema-cost?limit=3",
        headers=AUTH_HEADERS,
    )

    assert datalake_response.status_code == 200
    assert datalake_response.json()["totals"]["active_tables"] == 10
    assert datalake_response.json()["top_offenders"][0]["table_id"] == str(table_id)
    assert schema_response.status_code == 200
    assert schema_response.json()["items"][0]["schema_name"] == "analytics"


def test_table_drilldown_endpoints_return_ui_ready_rows(monkeypatch: Any) -> None:
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(
        routes.p5,
        "table_partitions",
        lambda *args, **kwargs: {
            "table_id": table_id,
            "scan_at": scan_at,
            "items": [
                {
                    "id": 1,
                    "table_id": table_id,
                    "scan_at": scan_at,
                    "partition_value": "2026-01-01",
                    "partition_values": {"event_date_day": "2026-01-01"},
                    "partition_fields": [{"transform": "day"}],
                    "files_per_partition": 120,
                    "small_files_in_partition_count": 90,
                    "partition_size_gb": 1.5,
                    "compaction_status": "candidate",
                }
            ],
            "total": 1,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
        },
    )
    monkeypatch.setattr(
        routes.p5,
        "table_audit",
        lambda *args, **kwargs: {
            "table_id": table_id,
            "items": [
                {
                    "id": 1,
                    "ts": scan_at,
                    "table_id": table_id,
                    "job_id": None,
                    "action": "optimize",
                    "status": "succeeded",
                }
            ],
            "total": 1,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
        },
    )
    monkeypatch.setattr(
        routes.p5,
        "table_history",
        lambda *args, **kwargs: {
            "table_id": table_id,
            "items": [
                {
                    "id": 1,
                    "table_id": table_id,
                    "scan_at": scan_at,
                    "small_files_count": 90,
                    "total_files": 120,
                    "total_size_gb": 1.5,
                    "grade": "B",
                    "overall_score": 82,
                }
            ],
            "limit": kwargs["limit"],
        },
    )

    client = _client()
    partitions = client.get(f"/api/v1/tables/{table_id}/partitions", headers=AUTH_HEADERS)
    audit = client.get(f"/api/v1/tables/{table_id}/audit", headers=AUTH_HEADERS)
    history = client.get(f"/api/v1/tables/{table_id}/history", headers=AUTH_HEADERS)

    assert partitions.status_code == 200
    assert partitions.json()["items"][0]["small_files_in_partition_count"] == 90
    assert audit.status_code == 200
    assert audit.json()["items"][0]["action"] == "optimize"
    assert history.status_code == 200
    assert history.json()["items"][0]["overall_score"] == 82


def test_scan_trigger_records_placeholder_status(monkeypatch: Any) -> None:
    run_id = uuid.uuid4()
    table_id = uuid.uuid4()

    def create_scan_trigger(session: object, payload: dict[str, object]) -> dict[str, object]:
        assert payload["scope"] == "tables"
        assert payload["table_ids"] == [table_id]
        return {
            "run_id": run_id,
            "status": "accepted",
            "scope": "tables",
            "queued": False,
            "message": "Scan request recorded; scanner queue trigger is not implemented yet.",
            "source_id": None,
            "table_ids": [table_id],
            "schema_names": [],
        }

    monkeypatch.setattr(routes.p5, "create_scan_trigger", create_scan_trigger)

    response = _client().post(
        "/api/v1/scans",
        headers=AUTH_HEADERS,
        json={"scope": "tables", "table_ids": [str(table_id)]},
    )

    assert response.status_code == 202
    assert response.json()["run_id"] == str(run_id)
    assert response.json()["queued"] is False


def test_run_jobs_endpoint_and_websocket_snapshot(monkeypatch: Any) -> None:
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()
    table_id = uuid.uuid4()
    destination_id = uuid.uuid4()
    created_at = datetime(2026, 1, 1, tzinfo=UTC)

    snapshot = {
        "run": {
            "id": run_id,
            "kind": "adhoc",
            "status": "running",
            "preset_id": None,
            "trigger": "api",
            "deadline_at": None,
            "created_at": created_at,
            "finished_at": None,
            "summary": {"tables": 1},
        },
        "jobs": [
            {
                "id": job_id,
                "run_id": run_id,
                "table_id": table_id,
                "destination_id": destination_id,
                "schema_name": "analytics",
                "table_name": "events",
                "destination_name": "trino",
                "mode": "standard",
                "status": "queued",
                "plan": None,
            }
        ],
    }

    monkeypatch.setattr(routes.p5, "run_jobs_snapshot", lambda *args, **kwargs: snapshot)

    client = _client()
    response = client.get(f"/api/v1/runs/{run_id}/jobs", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["jobs"][0]["table_name"] == "events"

    with client.websocket_connect(f"/api/v1/ws/runs/{run_id}?token=viewer&once=true") as ws:
        payload = ws.receive_json()

    assert payload["type"] == "run_snapshot"
    assert payload["run"]["id"] == str(run_id)
    assert payload["jobs"][0]["destination_name"] == "trino"


def test_maintenance_run_accepts_filter_selection(monkeypatch: Any) -> None:
    destination_id = uuid.uuid4()
    table_id = uuid.uuid4()
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()

    class RunSession:
        def __init__(self) -> None:
            self.jobs: list[MaintenanceJob] = []

        def get(self, model: object, item_id: uuid.UUID) -> object | None:
            if model is Destination and item_id == destination_id:
                return Destination(id=destination_id, name="trino", engine="trino", config={})
            return None

        def add(self, model: object) -> None:
            if isinstance(model, MaintenanceJob):
                self.jobs.append(model)

        def flush(self) -> None:
            return None

        def commit(self) -> None:
            return None

        def refresh(self, model: object) -> None:
            if isinstance(model, MaintenanceJob):
                model.id = job_id

    def resolve(session: object, selection: AssignmentSelection) -> SelectionResult:
        assert selection.mode == "filter"
        assert selection.filter == {"field": "grade", "op": "in", "value": ["C", "D", "F"]}
        return SelectionResult([table_id])

    monkeypatch.setattr(routes, "_resolve_selection", resolve)
    monkeypatch.setattr(
        routes,
        "MaintenanceRun",
        lambda **kwargs: MaintenanceRun(id=run_id, **kwargs),
    )

    response = routes.create_maintenance_run(
        MaintenanceRunRequest(
            selection=AssignmentSelection(
                mode="filter",
                filter={"field": "grade", "op": "in", "value": ["C", "D", "F"]},
            ),
            destination_id=destination_id,
            mode="standard",
            trigger="web",
        ),
        RunSession(),  # type: ignore[arg-type]
    )

    assert response.run_id == run_id
    assert response.job_ids == [job_id]
    assert response.status == "queued"
