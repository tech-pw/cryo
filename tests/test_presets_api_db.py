from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cryo.api.app import create_app
from cryo.api.deps import get_tenant_session
from cryo.config import Settings
from cryo.db.base import Base
from cryo.db.models import (
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    PresetRule,
    Source,
    Table,
    TableAlertDispatch,
    TableAlertSubscription,
    TableStats,
)
from tests.auth_helpers import (
    ADMIN_HEADERS,
    OPERATOR_HEADERS,
    VIEWER_HEADERS,
    ensure_test_jwt_secret,
)

UUID_ID_MODELS = (
    Source,
    Destination,
    Preset,
    PresetRule,
    Table,
    MaintenanceRun,
    MaintenanceJob,
    TableAlertSubscription,
    TableAlertDispatch,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_: JSONB, compiler: object, **kwargs: object) -> str:
    return "JSON"


@dataclass(frozen=True)
class ApiDb:
    client: TestClient
    session_factory: sessionmaker[Session]


@pytest.fixture()
def api_db() -> Iterator[ApiDb]:
    ensure_test_jwt_secret()
    _patch_sqlite_incompatible_defaults()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(text("ATTACH DATABASE ':memory:' AS cryo"))
        Base.metadata.create_all(connection)

    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    event.listen(session_factory, "before_flush", _assign_missing_uuid_primary_keys)

    def override_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app = create_app(Settings(_env_file=None, environment="test"))
    app.dependency_overrides[get_tenant_session] = override_session
    with TestClient(app) as client:
        yield ApiDb(client=client, session_factory=session_factory)

    event.remove(session_factory, "before_flush", _assign_missing_uuid_primary_keys)
    engine.dispose()


def test_source_destination_preset_crud_and_rules(api_db: ApiDb) -> None:
    client = api_db.client

    source_response = client.post(
        "/api/v1/sources",
        headers=ADMIN_HEADERS,
        json={"name": "local", "type": "rest", "config": {"uri": "http://iceberg:8181"}},
    )
    assert source_response.status_code == 201
    source_id = source_response.json()["id"]

    patch_source_response = client.patch(
        f"/api/v1/sources/{source_id}",
        headers=ADMIN_HEADERS,
        json={"enabled": False},
    )
    assert patch_source_response.status_code == 200
    assert patch_source_response.json()["enabled"] is False

    destination_response = client.post(
        "/api/v1/destinations",
        headers=ADMIN_HEADERS,
        json={"name": "trino-main", "engine": "trino", "config": {"host": "trino"}},
    )
    assert destination_response.status_code == 201
    destination_id = destination_response.json()["id"]

    preset_response = client.post(
        "/api/v1/presets",
        headers=ADMIN_HEADERS,
        json={
            "name": "standard",
            "destination_id": destination_id,
            "operations": ["expire_snapshots", "optimize"],
            "params": {"min_small_files": 10},
        },
    )
    assert preset_response.status_code == 201
    preset_id = preset_response.json()["id"]

    rule_response = client.post(
        f"/api/v1/presets/{preset_id}/rules",
        headers=ADMIN_HEADERS,
        json={"predicate": {"field": "small_files_count", "op": ">", "value": 50}},
    )
    assert rule_response.status_code == 201
    rule_id = rule_response.json()["id"]

    rules_response = client.get(f"/api/v1/presets/{preset_id}/rules", headers=VIEWER_HEADERS)
    assert rules_response.status_code == 200
    assert [item["id"] for item in rules_response.json()] == [rule_id]

    delete_rule_response = client.delete(
        f"/api/v1/presets/{preset_id}/rules/{rule_id}",
        headers=ADMIN_HEADERS,
    )
    assert delete_rule_response.status_code == 204

    delete_destination_response = client.delete(
        f"/api/v1/destinations/{destination_id}",
        headers=ADMIN_HEADERS,
    )
    assert delete_destination_response.status_code == 204
    disabled_destination = client.get(
        f"/api/v1/destinations/{destination_id}", headers=VIEWER_HEADERS
    )
    assert disabled_destination.status_code == 200
    assert disabled_destination.json()["enabled"] is False


def test_cron_write_endpoints_reject_invalid_expressions(api_db: ApiDb) -> None:
    client = api_db.client

    scanner_response = client.patch(
        "/api/v1/scanner/config",
        headers=ADMIN_HEADERS,
        json={"schedule_cron": "0 2 * * *; DROP TABLE cryo.tables"},
    )
    assert scanner_response.status_code == 400

    destination_response = client.post(
        "/api/v1/destinations",
        headers=ADMIN_HEADERS,
        json={"name": "trino-main", "engine": "trino", "config": {"host": "trino"}},
    )
    assert destination_response.status_code == 201

    preset_response = client.post(
        "/api/v1/presets",
        headers=ADMIN_HEADERS,
        json={
            "name": "unsafe",
            "destination_id": destination_response.json()["id"],
            "schedule_cron": "0 99 * * *",
        },
    )
    assert preset_response.status_code == 400

    slack_response = client.post(
        "/api/v1/slack/configs",
        headers=ADMIN_HEADERS,
        json={
            "name": "daily",
            "webhook_url": "https://hooks.slack.com/services/T/B/C",
            "daily_report_cron": "0 9 * * *\n* * * * *",
        },
    )
    assert slack_response.status_code == 400


def test_simulate_effective_preset_assignment_and_sorting(api_db: ApiDb) -> None:
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    hot_table_id, cold_table_id = _seed_tables(api_db.session_factory, source_id)
    client = api_db.client

    standard_id = _create_preset(client, "standard", destination_id, priority=10)
    urgent_id = _create_preset(client, "urgent", destination_id, priority=100)
    rule_response = client.post(
        f"/api/v1/presets/{urgent_id}/rules",
        headers=ADMIN_HEADERS,
        json={"predicate": {"field": "small_files_count", "op": ">=", "value": 50}},
    )
    assert rule_response.status_code == 201

    effective_by_rule = client.get(
        f"/api/v1/tables/{hot_table_id}/effective-preset",
        headers=VIEWER_HEADERS,
    )
    assert effective_by_rule.status_code == 200
    assert effective_by_rule.json()["preset_id"] == urgent_id
    assert effective_by_rule.json()["provenance"] == "rule"

    simulate_response = client.post(
        f"/api/v1/presets/{urgent_id}/simulate",
        headers=OPERATOR_HEADERS,
    )
    assert simulate_response.status_code == 200
    assert simulate_response.json()["matched_count"] == 1
    assert simulate_response.json()["total_size_gb"] == 25.0

    assign_response = client.post(
        "/api/v1/assignments",
        headers=OPERATOR_HEADERS,
        json={
            "preset_id": standard_id,
            "selection": {"mode": "ids", "ids": [str(hot_table_id)]},
            "assigned_by": "pytest",
        },
    )
    assert assign_response.status_code == 200
    assert assign_response.json()["affected"] == 1

    effective_by_assignment = client.get(
        f"/api/v1/tables/{hot_table_id}/effective-preset",
        headers=VIEWER_HEADERS,
    )
    assert effective_by_assignment.status_code == 200
    assert effective_by_assignment.json()["preset_id"] == standard_id
    assert effective_by_assignment.json()["provenance"] == "assignment"

    filter_assign_response = client.post(
        "/api/v1/assignments",
        headers=OPERATOR_HEADERS,
        json={
            "preset_id": urgent_id,
            "selection": {
                "mode": "filter",
                "filter": {"field": "grade", "op": "in", "value": ["A", "F"]},
            },
        },
    )
    assert filter_assign_response.status_code == 200
    assert filter_assign_response.json()["affected"] == 2

    unassign_response = client.request(
        "DELETE",
        "/api/v1/assignments",
        headers=OPERATOR_HEADERS,
        json={
            "selection": {
                "mode": "filter",
                "filter": {"field": "grade", "op": "=", "value": "A"},
            }
        },
    )
    assert unassign_response.status_code == 200
    assert unassign_response.json()["affected"] == 1

    sorted_tables_response = client.get(
        "/api/v1/tables?sort=-small_files_count&limit=1&offset=0",
        headers=VIEWER_HEADERS,
    )
    assert sorted_tables_response.status_code == 200
    assert sorted_tables_response.json()["total"] == 2
    assert sorted_tables_response.json()["items"][0]["id"] == str(hot_table_id)
    assert cold_table_id != hot_table_id


def test_maintenance_run_detail_job_detail_and_cancel(api_db: ApiDb) -> None:
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    hot_table_id, _ = _seed_tables(api_db.session_factory, source_id)
    client = api_db.client

    preset_id = _create_preset(client, "standard", destination_id, priority=100)

    run_response = client.post(
        "/api/v1/maintenance/run",
        headers=OPERATOR_HEADERS,
        json={
            "destination_id": str(destination_id),
            "preset_id": preset_id,
            "mode": "standard",
            "selection": {
                "mode": "filter",
                "filter": {"field": "small_files_count", "op": ">", "value": 50},
            },
        },
    )
    assert run_response.status_code == 201
    run_body = run_response.json()
    assert len(run_body["job_ids"]) == 1

    run_id = run_body["run_id"]
    job_id = run_body["job_ids"][0]

    run_detail_response = client.get(f"/api/v1/runs/{run_id}", headers=VIEWER_HEADERS)
    assert run_detail_response.status_code == 200
    assert run_detail_response.json()["status"] == "queued"

    job_detail_response = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER_HEADERS)
    assert job_detail_response.status_code == 200
    assert job_detail_response.json()["table_id"] == str(hot_table_id)
    assert job_detail_response.json()["table_name"] == "hot_events"

    cancel_response = client.post(f"/api/v1/runs/{run_id}/cancel", headers=OPERATOR_HEADERS)
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    cancelled_job_response = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER_HEADERS)
    assert cancelled_job_response.status_code == 200
    assert cancelled_job_response.json()["status"] == "skipped"
    assert cancelled_job_response.json()["error"] == "cancelled"


def test_maintenance_run_with_scheduled_at_creates_scheduled_status(api_db: ApiDb) -> None:
    """A future scheduled_at creates a run in "scheduled" status rather than "queued" --
    cryo/scheduler/service.py's dispatch tick is what actually enqueues it once the time
    arrives, not this endpoint."""
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    _seed_tables(api_db.session_factory, source_id)
    client = api_db.client

    preset_id = _create_preset(client, "standard", destination_id, priority=100)
    scheduled_at = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

    run_response = client.post(
        "/api/v1/maintenance/run",
        headers=OPERATOR_HEADERS,
        json={
            "destination_id": str(destination_id),
            "preset_id": preset_id,
            "mode": "standard",
            "scheduled_at": scheduled_at,
            "selection": {
                "mode": "filter",
                "filter": {"field": "small_files_count", "op": ">", "value": 50},
            },
        },
    )
    assert run_response.status_code == 201
    assert run_response.json()["status"] == "scheduled"

    run_id = run_response.json()["run_id"]
    run_detail = client.get(f"/api/v1/runs/{run_id}", headers=VIEWER_HEADERS).json()
    assert run_detail["status"] == "scheduled"
    assert run_detail["scheduled_at"] is not None


def test_maintenance_run_rejects_scheduled_at_in_the_past(api_db: ApiDb) -> None:
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    _seed_tables(api_db.session_factory, source_id)
    client = api_db.client

    preset_id = _create_preset(client, "standard", destination_id, priority=100)
    scheduled_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    run_response = client.post(
        "/api/v1/maintenance/run",
        headers=OPERATOR_HEADERS,
        json={
            "destination_id": str(destination_id),
            "preset_id": preset_id,
            "mode": "standard",
            "scheduled_at": scheduled_at,
            "selection": {
                "mode": "filter",
                "filter": {"field": "small_files_count", "op": ">", "value": 50},
            },
        },
    )
    assert run_response.status_code == 400


def test_scheduled_run_is_cancellable_before_it_fires(api_db: ApiDb) -> None:
    """Cancel is purely status-based (see cryo/api/routes.py's cancel_run) -- it must work
    identically on a not-yet-dispatched scheduled run with zero special-casing."""
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    _seed_tables(api_db.session_factory, source_id)
    client = api_db.client

    preset_id = _create_preset(client, "standard", destination_id, priority=100)
    scheduled_at = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

    run_response = client.post(
        "/api/v1/maintenance/run",
        headers=OPERATOR_HEADERS,
        json={
            "destination_id": str(destination_id),
            "preset_id": preset_id,
            "mode": "standard",
            "scheduled_at": scheduled_at,
            "selection": {
                "mode": "filter",
                "filter": {"field": "small_files_count", "op": ">", "value": 50},
            },
        },
    )
    run_id = run_response.json()["run_id"]
    job_id = run_response.json()["job_ids"][0]

    cancel_response = client.post(f"/api/v1/runs/{run_id}/cancel", headers=OPERATOR_HEADERS)
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    job_response = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER_HEADERS)
    assert job_response.json()["status"] == "skipped"


def test_maintenance_run_rejects_request_without_preset_id(api_db: ApiDb) -> None:
    """An explicit preset selection is required for every maintenance run; the API
    returns 422 when preset_id is missing."""
    _, destination_id = _seed_source_destination(api_db.session_factory)
    client = api_db.client

    run_response = client.post(
        "/api/v1/maintenance/run",
        headers=OPERATOR_HEADERS,
        json={
            "destination_id": str(destination_id),
            "mode": "standard",
            "table_ids": [],
        },
    )
    assert run_response.status_code == 422


def test_list_runs_filters_by_source_id(api_db: ApiDb) -> None:
    """A lakehouse's own "Scans" panel (Connections page) only wants runs scoped to it --
    fleet-wide "all" scans have no source_id in their summary and must not leak in."""
    source_id, _ = _seed_source_destination(api_db.session_factory)
    other_source_id, _ = _seed_source_destination(api_db.session_factory)
    client = api_db.client

    with api_db.session_factory() as session:
        matching_run = MaintenanceRun(
            id=uuid.uuid4(),
            kind="scan",
            status="succeeded",
            summary={"scope": "source", "source_id": str(source_id)},
        )
        other_run = MaintenanceRun(
            id=uuid.uuid4(),
            kind="scan",
            status="succeeded",
            summary={"scope": "source", "source_id": str(other_source_id)},
        )
        fleet_run = MaintenanceRun(
            id=uuid.uuid4(),
            kind="scan",
            status="succeeded",
            summary={"scope": "all"},
        )
        session.add_all([matching_run, other_run, fleet_run])
        session.commit()
        matching_run_id = matching_run.id

    response = client.get(f"/api/v1/runs?source_id={source_id}", headers=VIEWER_HEADERS)
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [str(matching_run_id)]


def test_compatible_presets_and_assign_requires_catalog_binding(api_db: ApiDb) -> None:
    client = api_db.client
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    other_source_id = uuid.uuid4()
    with api_db.session_factory() as session:
        session.add(
            Source(
                id=other_source_id,
                name=f"other-{uuid.uuid4()}",
                type="rest",
                config={},
            )
        )
        # Bind only the first source to the Trino destination.
        destination = session.get(Destination, destination_id)
        assert destination is not None
        destination.config = {
            **dict(destination.config or {}),
            "catalog_bindings": [{"source_id": str(source_id), "trino_catalog": "iceberg"}],
        }
        session.commit()

    hot_table_id, _ = _seed_tables(api_db.session_factory, source_id)
    other_table_id = uuid.uuid4()
    with api_db.session_factory() as session:
        session.add(
            Table(
                id=other_table_id,
                source_id=other_source_id,
                schema_name="analytics",
                table_name="other_events",
                is_active=True,
            )
        )
        session.commit()

    native_dest = client.post(
        "/api/v1/destinations",
        headers=ADMIN_HEADERS,
        json={
            "name": f"native-{uuid.uuid4()}",
            "engine": "native",
            "config": {"engine_impl": "go", "slot_cap": 2},
        },
    )
    assert native_dest.status_code == 201
    native_destination_id = native_dest.json()["id"]

    trino_preset = _create_preset(client, "trino-bound", destination_id, priority=100)
    native_preset = _create_preset(client, "native-any", native_destination_id, priority=100)

    compatible = client.get(
        f"/api/v1/tables/{hot_table_id}/compatible-presets",
        headers=VIEWER_HEADERS,
    )
    assert compatible.status_code == 200
    compatible_ids = {item["id"] for item in compatible.json()}
    assert trino_preset in compatible_ids
    assert native_preset in compatible_ids

    incompatible_list = client.get(
        f"/api/v1/tables/{other_table_id}/compatible-presets",
        headers=VIEWER_HEADERS,
    )
    assert incompatible_list.status_code == 200
    other_ids = {item["id"] for item in incompatible_list.json()}
    assert trino_preset not in other_ids
    assert native_preset in other_ids

    ok_assign = client.post(
        "/api/v1/assignments",
        headers=OPERATOR_HEADERS,
        json={
            "selection": {"mode": "ids", "ids": [str(hot_table_id)]},
            "preset_id": trino_preset,
        },
    )
    assert ok_assign.status_code == 200

    bad_assign = client.post(
        "/api/v1/assignments",
        headers=OPERATOR_HEADERS,
        json={
            "selection": {"mode": "ids", "ids": [str(other_table_id)]},
            "preset_id": trino_preset,
        },
    )
    assert bad_assign.status_code == 400
    assert "catalog" in bad_assign.json()["detail"].lower()


def _seed_source_destination(
    session_factory: sessionmaker[Session],
) -> tuple[uuid.UUID, uuid.UUID]:
    with session_factory() as session:
        source = Source(
            id=uuid.uuid4(),
            name=f"source-{uuid.uuid4()}",
            type="rest",
            config={},
        )
        destination = Destination(
            id=uuid.uuid4(),
            name=f"destination-{uuid.uuid4()}",
            engine="trino",
            # destination_serves_source() treats a Trino destination with no
            # catalog_bindings as serving no source at all -- bind this fixture's own
            # source so tests exercising assignment/run creation still work.
            config={"host": "trino", "catalog_bindings": [{"source_id": str(source.id)}]},
        )
        session.add_all([source, destination])
        session.commit()
        return source.id, destination.id


def _seed_tables(
    session_factory: sessionmaker[Session],
    source_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    hot_table_id = uuid.uuid4()
    cold_table_id = uuid.uuid4()
    scan_at = datetime(2026, 1, 1, tzinfo=UTC)
    with session_factory() as session:
        hot_table = Table(
            id=hot_table_id,
            source_id=source_id,
            schema_name="analytics",
            table_name="hot_events",
            is_active=True,
        )
        cold_table = Table(
            id=cold_table_id,
            source_id=source_id,
            schema_name="analytics",
            table_name="cold_events",
            is_active=True,
        )
        session.add_all(
            [
                hot_table,
                cold_table,
                TableStats(
                    table_id=hot_table_id,
                    scan_at=scan_at,
                    needs_compaction=True,
                    compaction_priority="HIGH",
                    grade="F",
                    overall_score=45,
                    total_files=500,
                    small_files_count=200,
                    total_partitions=10,
                    total_size_gb=25.0,
                ),
                TableStats(
                    table_id=cold_table_id,
                    scan_at=scan_at,
                    needs_compaction=False,
                    compaction_priority="LOW",
                    grade="A",
                    overall_score=95,
                    total_files=20,
                    small_files_count=2,
                    total_partitions=2,
                    total_size_gb=1.5,
                ),
            ]
        )
        session.commit()
    return hot_table_id, cold_table_id


def _create_preset(
    client: TestClient,
    name: str,
    destination_id: uuid.UUID,
    *,
    priority: int,
) -> str:
    response = client.post(
        "/api/v1/presets",
        headers=ADMIN_HEADERS,
        json={
            "name": name,
            "destination_id": str(destination_id),
            "operations": ["expire_snapshots", "optimize"],
            "params": {"min_small_files": 10},
            "priority": priority,
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _assign_missing_uuid_primary_keys(
    session: Session,
    flush_context: object,
    instances: object,
) -> None:
    for item in session.new:
        if isinstance(item, UUID_ID_MODELS) and item.id is None:
            item.id = uuid.uuid4()


def _patch_sqlite_incompatible_defaults() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.server_default is None:
                continue
            default = str(column.server_default.arg)
            if "::jsonb" in default or "gen_random_uuid" in default:
                column.server_default = None
