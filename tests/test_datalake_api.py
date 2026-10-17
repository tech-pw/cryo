from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from cryo.api import routes
from cryo.api.app import create_app
from cryo.api.deps import get_tenant_session
from cryo.api.schemas import AssignmentSelection, MaintenanceRunRequest
from cryo.config import Settings
from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Preset, Table, TableStats
from cryo.db.repositories.presets import SelectionResult
from tests.auth_helpers import ADMIN_HEADERS, VIEWER_HEADERS, ensure_test_jwt_secret
from tests.auth_helpers import OPERATOR_HEADERS as AUTH_HEADERS


class FakeSession:
    def expire_all(self) -> None:
        return None

    def expunge_all(self) -> None:
        return None


def _client() -> TestClient:
    ensure_test_jwt_secret()
    app = create_app(Settings(_env_file=None, environment="test"))

    def fake_tenant_session() -> Iterator[FakeSession]:
        yield FakeSession()

    app.dependency_overrides[get_tenant_session] = fake_tenant_session
    return TestClient(app)


def test_dashboard_endpoints_return_bounded_aggregates(monkeypatch: Any) -> None:
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 1, 1, tzinfo=UTC)

    def datalake(
        session: object, *, top_limit: int, drift_limit: int, settings: object = None
    ) -> dict[str, object]:
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

    monkeypatch.setattr(routes.datalake, "dashboard_datalake", datalake)
    monkeypatch.setattr(routes.datalake, "dashboard_schema_cost", schema_cost)

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


def test_table_csv_export_uses_the_exact_server_filter(api_db: Any) -> None:
    source_response = api_db.client.post(
        "/api/v1/sources",
        headers=ADMIN_HEADERS,
        json={"name": "freshness-export", "type": "rest", "config": {}},
    )
    assert source_response.status_code == 201
    source_id = uuid.UUID(source_response.json()["id"])
    as_of = datetime(2026, 8, 4, 12, tzinfo=UTC)
    with api_db.session_factory() as session:
        for name, committed_at in [
            ("stale_orders", datetime(2026, 7, 30, 12, tzinfo=UTC)),
            ("fresh_orders", datetime(2026, 8, 4, 11, tzinfo=UTC)),
        ]:
            table = Table(
                source_id=source_id,
                schema_name="analytics",
                table_name=name,
                first_seen_at=as_of,
                is_active=True,
            )
            session.add(table)
            session.flush()
            session.add(
                TableStats(
                    table_id=table.id,
                    scan_at=as_of,
                    last_snapshot_committed_at=committed_at,
                    needs_compaction=False,
                    compaction_priority="LOW",
                    grade="B",
                    overall_score=80,
                    total_files=1,
                    small_files_count=0,
                    total_size_gb=1,
                    query_count_30d=0,
                )
            )
        session.commit()

    predicate = {
        "all": [
            {"field": "is_active", "op": "=", "value": True},
            {"field": "consecutive_scan_failures", "op": "<", "value": 2},
            {
                "field": "last_snapshot_committed_at",
                "op": "<=",
                "value": "2026-08-01T12:00:00Z",
            },
            {
                "field": "last_snapshot_committed_at",
                "op": ">",
                "value": "2026-07-28T12:00:00Z",
            },
        ]
    }
    response = api_db.client.get(
        "/api/v1/tables/export.csv",
        headers=VIEWER_HEADERS,
        params={"filter": json.dumps(predicate)},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "analytics.stale_orders" in response.text
    assert "analytics.fresh_orders" not in response.text


def test_table_list_omits_stats_raw_but_detail_keeps_it(api_db: Any) -> None:
    source_response = api_db.client.post(
        "/api/v1/sources",
        headers=ADMIN_HEADERS,
        json={"name": "raw-omit", "type": "rest", "config": {}},
    )
    source_id = uuid.UUID(source_response.json()["id"])
    as_of = datetime(2026, 8, 4, 12, tzinfo=UTC)
    with api_db.session_factory() as session:
        table = Table(
            source_id=source_id,
            schema_name="analytics",
            table_name="wide_partitions",
            first_seen_at=as_of,
            is_active=True,
        )
        session.add(table)
        session.flush()
        session.add(
            TableStats(
                table_id=table.id,
                scan_at=as_of,
                needs_compaction=True,
                compaction_priority="HIGH",
                grade="D",
                overall_score=40,
                total_files=10,
                small_files_count=9,
                total_size_gb=1,
                query_count_30d=0,
                raw={"partitions_to_rewrite": [{"partition_key": str(i)} for i in range(100)]},
            )
        )
        session.commit()
        table_id = str(table.id)

    listing = api_db.client.get("/api/v1/tables", headers=VIEWER_HEADERS)
    assert listing.status_code == 200
    [item] = [row for row in listing.json()["items"] if row["id"] == table_id]
    assert item["stats"]["small_files_count"] == 9
    assert item["stats"]["raw"] is None

    detail = api_db.client.get(f"/api/v1/tables/{table_id}", headers=VIEWER_HEADERS)
    assert len(detail.json()["stats"]["raw"]["partitions_to_rewrite"]) == 100


def test_cost_dashboard_and_table_cost_endpoints(monkeypatch: Any) -> None:
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 1, 1, tzinfo=UTC)

    def dashboard_cost(
        session: object, *, top_limit: int, trend_limit: int, settings: object
    ) -> dict[str, object]:
        assert top_limit == 4
        assert trend_limit == 6
        assert settings is not None
        return {
            "totals": {
                "current_monthly_cost_usd": 100.0,
                "projected_monthly_cost_usd": 55.0,
                "projected_savings_usd_month": 45.0,
                "current_storage_bytes": 1024**4,
                "reclaimable_bytes": 512**4,
                "precision_coverage": {"metadata": 2, "heuristic": 1},
                "orphan_scan_stale": False,
                "last_full_cost_scan_at": scan_at,
            },
            "savings_by_operation": [
                {
                    "operation": "expire_snapshots",
                    "savings_usd_month": 30.0,
                    "reclaimable_bytes": 1000,
                },
                {
                    "operation": "optimize",
                    "savings_usd_month": 15.0,
                    "reclaimable_bytes": 500,
                },
            ],
            "cost_by_schema": [
                {
                    "schema_name": "analytics",
                    "current_usd": 80.0,
                    "projected_usd": 40.0,
                    "savings_usd": 40.0,
                }
            ],
            "top_opportunities": [
                {
                    "id": table_id,
                    "source_id": uuid.uuid4(),
                    "schema_name": "analytics",
                    "table_name": "events",
                    "first_seen_at": scan_at,
                    "is_active": True,
                    "stats": {
                        "needs_compaction": True,
                        "compaction_priority": "HIGH",
                        "grade": "C",
                        "overall_score": 61,
                        "total_files": 500,
                        "small_files_count": 250,
                        "total_partitions": 10,
                        "total_size_gb": 20.0,
                        "estimated_monthly_s3_cost_usd": 80.0,
                        "projected_monthly_s3_cost_usd": 40.0,
                        "projected_savings_usd_month": 40.0,
                        "reclaimable_bytes": 1024**3,
                        "query_count_30d": 10,
                    },
                }
            ],
            "trend": [
                {
                    "scan_at": scan_at,
                    "current_monthly_cost_usd": 100.0,
                    "projected_savings_usd_month": 45.0,
                    "realized_usd_month": None,
                }
            ],
        }

    def table_cost(session: object, table_id_arg: uuid.UUID) -> dict[str, object] | None:
        assert table_id_arg == table_id
        return {
            "table_id": table_id,
            "precision": "metadata",
            "price_per_gb_month": 0.023,
            "current_monthly_cost_usd": 80.0,
            "projected_monthly_cost_usd": 40.0,
            "projected_savings_usd_month": 40.0,
            "query_request_savings_usd_month": 2.5,
            "live_bytes": 1000,
            "dead_bytes": 200,
            "metadata_bytes": 50,
            "orphan_bytes": 0,
            "listed_bytes": None,
            "reclaimable_bytes": 1024**3,
            "by_operation": [
                {
                    "operation": "expire_snapshots",
                    "savings_usd_month": 25.0,
                    "reclaimable_bytes": 700,
                }
            ],
            "last_full_cost_scan_at": scan_at,
            "scan_at": scan_at,
        }

    monkeypatch.setattr(routes.datalake, "dashboard_cost", dashboard_cost)
    monkeypatch.setattr(routes.datalake, "table_cost", table_cost)

    client = _client()
    cost_response = client.get(
        "/api/v1/dashboards/cost?top_limit=4&trend_limit=6",
        headers=AUTH_HEADERS,
    )
    table_cost_response = client.get(f"/api/v1/tables/{table_id}/cost", headers=AUTH_HEADERS)

    assert cost_response.status_code == 200
    assert cost_response.json()["totals"]["projected_savings_usd_month"] == 45.0
    assert cost_response.json()["savings_by_operation"][0]["operation"] == "expire_snapshots"
    assert table_cost_response.status_code == 200
    assert table_cost_response.json()["projected_savings_usd_month"] == 40.0


def test_table_cost_endpoint_returns_404_when_missing(monkeypatch: Any) -> None:
    monkeypatch.setattr(routes.datalake, "table_cost", lambda *args, **kwargs: None)
    response = _client().get(f"/api/v1/tables/{uuid.uuid4()}/cost", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_table_drilldown_endpoints_return_ui_ready_rows(monkeypatch: Any) -> None:
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(
        routes.datalake,
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
        routes.datalake,
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
        routes.datalake,
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


def test_scan_trigger_accepts_cost_full_scan(monkeypatch: Any) -> None:
    run_id = uuid.uuid4()
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 1, 1, tzinfo=UTC)

    def create_scan_trigger(session: object, payload: dict[str, object]) -> dict[str, object]:
        assert payload["scope"] == "tables"
        assert payload["table_ids"] == [table_id]
        assert payload["cost_full_scan"] is True
        return {
            "run_id": run_id,
            "status": "accepted",
            "scope": "tables",
            "queued": False,
            "message": "Scan request accepted.",
            "source_id": None,
            "table_ids": [table_id],
            "schema_names": [],
            "cost_full_scan": True,
        }

    monkeypatch.setattr(routes.datalake, "create_scan_trigger", create_scan_trigger)
    monkeypatch.setattr(
        routes.scanner_repo,
        "get_scanner_config",
        lambda session: SimpleNamespace(
            id=1,
            enabled=True,
            schedule_cron="0 2 * * *",
            timezone="UTC",
            num_chunks=4,
            full_scan_default=False,
            cost_full_scan_default=False,
            max_concurrent_runs=1,
            created_at=scan_at,
            updated_at=scan_at,
        ),
    )

    def _raise_redis() -> object:
        raise RuntimeError("no redis")

    monkeypatch.setattr("cryo.infra.wiring.build_runtime_wiring", _raise_redis)

    response = _client().post(
        "/api/v1/scans",
        headers=AUTH_HEADERS,
        json={"scope": "tables", "table_ids": [str(table_id)], "cost_full_scan": True},
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

    monkeypatch.setattr(routes.datalake, "run_jobs_snapshot", lambda *args, **kwargs: snapshot)

    client = _client()
    response = client.get(f"/api/v1/runs/{run_id}/jobs", headers=AUTH_HEADERS)

    assert response.status_code == 200
    assert response.json()["jobs"][0]["table_name"] == "events"

    with client.websocket_connect(
        f"/api/v1/ws/runs/{run_id}?once=true",
        headers=VIEWER_HEADERS,
    ) as ws:
        payload = ws.receive_json()

    assert payload["type"] == "run_snapshot"
    assert payload["run"]["id"] == str(run_id)
    assert payload["jobs"][0]["destination_name"] == "trino"


def test_send_run_snapshot_expunges_session_not_just_expires(monkeypatch: Any) -> None:
    """Real production incident (2026-07-14): watch_run's websocket polls _send_run_snapshot
    in a loop for as long as a browser tab stays open on a non-terminal run -- which, under
    Governor's admission pacing, can be a long time. run_jobs_snapshot's selectinload()s pull
    in the run's full job/table/destination graph on every poll. expire_all() only expires
    attribute state, it doesn't remove objects from the identity map, so that graph
    accumulated every poll instead of being replaced -- confirmed live: cryo-api climbed to
    1.7GB+ within 17 minutes and OOMKilled. Must call expunge_all(), not expire_all()."""
    import asyncio

    from cryo.api.routes import _send_run_snapshot

    run_id = uuid.uuid4()
    calls: list[str] = []

    class SpySession:
        def expire_all(self) -> None:
            calls.append("expire_all")

        def expunge_all(self) -> None:
            calls.append("expunge_all")

    fake_snapshot = {
        "run": {
            "id": run_id,
            "kind": "adhoc",
            "status": "running",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        },
        "jobs": [],
    }
    monkeypatch.setattr(routes.datalake, "run_jobs_snapshot", lambda *args, **kwargs: fake_snapshot)

    sent: list[object] = []

    class FakeWebSocket:
        async def send_json(self, payload: object) -> None:
            sent.append(payload)

    asyncio.run(_send_run_snapshot(FakeWebSocket(), SpySession(), run_id))  # type: ignore[arg-type]

    assert "expunge_all" in calls
    assert "expire_all" not in calls
    assert sent


def test_run_watch_closes_once_the_run_is_partial(monkeypatch: Any) -> None:
    """Regression, 2026-09-28: the batch path finishes nightly runs as "partial", which was not
    a terminal status, so the run websocket kept sending full snapshots until the tab closed."""
    import asyncio
    from types import SimpleNamespace

    run_id = uuid.uuid4()
    statuses = iter(["running"])

    def fake_snapshot(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "run": {
                "id": run_id,
                "kind": "adhoc",
                "status": next(statuses, "partial"),
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            },
            "jobs": [],
        }

    def no_event_stream() -> object:
        raise RuntimeError("run event stream unavailable")

    monkeypatch.setattr(routes.datalake, "run_jobs_snapshot", fake_snapshot)
    monkeypatch.setattr(routes, "build_runtime_wiring", no_event_stream)

    sent: list[dict[str, Any]] = []

    class FakeWebSocket:
        async def send_json(self, payload: dict[str, Any]) -> None:
            sent.append(payload)

    asyncio.run(
        asyncio.wait_for(
            routes._watch_run_with_session(
                FakeWebSocket(),  # type: ignore[arg-type]
                run_id,
                SimpleNamespace(),  # type: ignore[arg-type]
                once=False,
                poll_interval_sec=0.01,
            ),
            timeout=5,
        )
    )

    assert [payload["type"] for payload in sent] == ["run_snapshot", "log_line", "run_snapshot"]
    assert sent[-1]["run"]["status"] == "partial"


def test_websocket_replays_log_line_events(monkeypatch: Any) -> None:
    run_id = uuid.uuid4()
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    snapshot = {
        "run": {
            "id": run_id,
            "kind": "scan",
            "status": "running",
            "preset_id": None,
            "trigger": "api",
            "deadline_at": None,
            "created_at": created_at,
            "finished_at": None,
            "summary": {"assigned": 1},
        },
        "jobs": [],
    }
    calls = {"count": 0}

    def fake_snapshot(*args: Any, **kwargs: Any) -> dict[str, object]:
        calls["count"] += 1
        if calls["count"] > 1:
            snapshot["run"]["status"] = "succeeded"
        return snapshot

    class FakeRunEvents:
        def replay_with_ids(
            self, requested_run_id: uuid.UUID, *, count: int = 200
        ) -> list[tuple[str, dict[str, object]]]:
            assert requested_run_id == run_id
            return [
                (
                    "1-0",
                    {
                        "type": "log_line",
                        "ts": created_at.isoformat(),
                        "run_id": str(run_id),
                        "level": "info",
                        "message": "Scanning demo.events",
                    },
                )
            ]

        def read_blocking(
            self,
            requested_run_id: uuid.UUID,
            *,
            last_id: str = "0-0",
            block_ms: int = 1000,
            count: int = 50,
        ) -> list[tuple[str, dict[str, object]]]:
            assert requested_run_id == run_id
            return []

    class FakeWiring:
        run_events = FakeRunEvents()

    monkeypatch.setattr(routes.datalake, "run_jobs_snapshot", fake_snapshot)
    monkeypatch.setattr(routes, "build_runtime_wiring", lambda: FakeWiring())

    with _client().websocket_connect(
        f"/api/v1/ws/runs/{run_id}?poll_interval_sec=0.2",
        headers=VIEWER_HEADERS,
    ) as ws:
        first = ws.receive_json()
        second = ws.receive_json()
        third = ws.receive_json()

    assert first["type"] == "run_snapshot"
    assert second["type"] == "log_line"
    assert second["message"] == "Scanning demo.events"
    assert third["type"] == "run_snapshot"


def test_websocket_polling_fallback_when_event_bus_unavailable(monkeypatch: Any) -> None:
    run_id = uuid.uuid4()
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
            "summary": {},
        },
        "jobs": [],
    }

    monkeypatch.setattr(routes.datalake, "run_jobs_snapshot", lambda *args, **kwargs: snapshot)

    def _raise_no_redis() -> object:
        raise RuntimeError("no redis")

    monkeypatch.setattr(routes, "build_runtime_wiring", _raise_no_redis)

    with _client().websocket_connect(
        f"/api/v1/ws/runs/{run_id}?once=true",
        headers=VIEWER_HEADERS,
    ) as ws:
        payload = ws.receive_json()
    assert payload["type"] == "run_snapshot"


def test_maintenance_run_accepts_filter_selection(monkeypatch: Any) -> None:
    destination_id = uuid.uuid4()
    preset_id = uuid.uuid4()
    table_id = uuid.uuid4()
    run_id = uuid.uuid4()
    job_id = uuid.uuid4()
    published_events: list[dict[str, object]] = []
    enqueued_jobs: list[tuple[str, str]] = []

    class FakeRunEvents:
        def publish(self, requested_run_id: uuid.UUID, event: dict[str, object]) -> None:
            assert requested_run_id == run_id
            published_events.append(event)

    class FakeQueue:
        def enqueue(self, queued_job_id: str, queued_destination_id: str) -> None:
            enqueued_jobs.append((queued_job_id, queued_destination_id))

    class FakeWiring:
        queue = FakeQueue()
        run_events = FakeRunEvents()

    class RunSession:
        def __init__(self) -> None:
            self.jobs: list[MaintenanceJob] = []

        def get(self, model: object, item_id: uuid.UUID) -> object | None:
            if model is Destination and item_id == destination_id:
                return Destination(id=destination_id, name="trino", engine="trino", config={})
            if model is Preset and item_id == preset_id:
                return Preset(
                    id=preset_id,
                    name="standard",
                    destination_id=destination_id,
                    mode="standard",
                    operations=[],
                    params={},
                    guardrails={},
                    enabled=True,
                )
            return None

        def scalars(self, _statement: object) -> list[object]:
            # excluded_table_ids() -- no MaintenanceExclusion rows exist in this test,
            # so every selected table is unexcluded; this test asserts job_ids == [job_id]
            # with no exclusions involved.
            return []

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
    monkeypatch.setattr(routes, "build_runtime_wiring", lambda: FakeWiring())

    from cryo.api.security import Principal, Role

    response = routes.create_maintenance_run(
        MaintenanceRunRequest(
            selection=AssignmentSelection(
                mode="filter",
                filter={"field": "grade", "op": "in", "value": ["C", "D", "F"]},
            ),
            destination_id=destination_id,
            preset_id=preset_id,
            mode="standard",
            trigger="web",
        ),
        RunSession(),  # type: ignore[arg-type]
        Principal(subject="test-user", roles=frozenset({Role.OPERATOR}), tenant_id=None),
    )

    assert response.run_id == run_id
    assert response.job_ids == [job_id]
    assert response.status == "queued"
    assert enqueued_jobs == [(str(job_id), str(destination_id))]
    assert any(
        event["type"] == "log_line" and "Submitted maintenance run" in str(event["message"])
        for event in published_events
    )
    assert any(
        event["type"] == "job_status" and event["status"] == "queued" for event in published_events
    )
