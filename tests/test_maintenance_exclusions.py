from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from cryo.config import Settings
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceExclusion,
    MaintenanceRun,
    Source,
    Table,
    TableStats,
)
from cryo.db.repositories.exclusions import (
    create_exclusion,
    delete_exclusion,
    exclusions_covering,
    set_table_hold,
)
from cryo.db.repositories.intents import set_pause_state
from cryo.db.repositories.scheduler_store import SQLSchedulerStore
from cryo.maintenance.coordinator import BatchMaintenance
from cryo.maintenance.selection import load_candidates
from cryo.queue.locks import InMemoryLockStore
from tests.auth_helpers import ADMIN_HEADERS, OPERATOR_HEADERS, VIEWER_HEADERS
from tests.test_presets_api_db import (
    ApiDb,
    _create_preset,
    _seed_source_destination,
    _seed_tables,
)

NOW = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)


def _seed(session) -> tuple[Source, dict[str, Table]]:
    source = Source(id=uuid.uuid4(), name="lake", type="hive", config={}, enabled=True)
    session.add(source)
    session.flush()
    tables = {
        name: Table(
            id=uuid.uuid4(),
            source_id=source.id,
            schema_name=schema,
            table_name=name,
            is_active=True,
        )
        for schema, name in [
            ("sales", "orders"),
            ("sales", "refunds"),
            ("staging", "raw_orders"),
        ]
    }
    session.add_all(tables.values())
    session.flush()
    for table in tables.values():
        session.add(
            TableStats(
                table_id=table.id,
                scan_at=NOW - timedelta(hours=1),
                needs_compaction=True,
                compaction_priority="HIGH",
                grade="D",
                overall_score=40,
                total_files=100,
                small_files_count=100,
                total_size_gb=1.0,
                delete_file_pct=0.0,
                query_count_30d=1,
            )
        )
    session.commit()
    return source, tables


def _candidate_names(session) -> set[str]:
    return {
        c.table_name
        for c in load_candidates(session, now=NOW, cooldown_hours=20, max_stats_age_days=3)
    }


def test_table_and_schema_exclusions_drop_out_of_nightly_selection(session) -> None:
    source, _ = _seed(session)
    assert _candidate_names(session) == {"orders", "refunds", "raw_orders"}

    table_rule = create_exclusion(
        session,
        source_id=source.id,
        schema_name="sales",
        table_name="orders",
        reason="owner asked",
        actor="op",
    )
    schema_rule = create_exclusion(
        session,
        source_id=source.id,
        schema_name="staging",
        table_name=None,
        reason="scratch schema",
        actor="op",
    )
    session.commit()
    assert _candidate_names(session) == {"refunds"}

    delete_exclusion(session, table_rule, actor="op")
    delete_exclusion(session, schema_rule, actor="op")
    session.commit()
    assert _candidate_names(session) == {"orders", "refunds", "raw_orders"}

    actions = list(
        session.scalars(
            select(AuditLog.action)
            .where(AuditLog.action.in_(["maintenance_exclude", "maintenance_include"]))
            .order_by(AuditLog.id)
        )
    )
    assert actions == [
        "maintenance_exclude",
        "maintenance_exclude",
        "maintenance_include",
        "maintenance_include",
    ]


def test_exclusion_api_lifecycle_and_roles(api_db: ApiDb) -> None:
    source_id, _ = _seed_source_destination(api_db.session_factory)
    _seed_tables(api_db.session_factory, source_id)
    client = api_db.client
    body = {"source_id": str(source_id), "schema_name": "analytics", "reason": "freeze window"}

    assert (
        client.post("/api/v1/maintenance/exclusions", headers=VIEWER_HEADERS, json=body)
    ).status_code == 403

    created = client.post("/api/v1/maintenance/exclusions", headers=OPERATOR_HEADERS, json=body)
    assert created.status_code == 201
    assert created.json()["table_name"] is None
    assert created.json()["table_count"] == 2

    duplicate = client.post("/api/v1/maintenance/exclusions", headers=OPERATOR_HEADERS, json=body)
    assert duplicate.status_code == 409

    listed = client.get("/api/v1/maintenance/exclusions", headers=VIEWER_HEADERS)
    assert listed.status_code == 200
    assert [row["schema_name"] for row in listed.json()] == ["analytics"]
    assert listed.json()[0]["source_name"].startswith("source-")

    removed = client.delete(
        f"/api/v1/maintenance/exclusions/{created.json()['id']}", headers=OPERATOR_HEADERS
    )
    assert removed.status_code == 204
    assert client.get("/api/v1/maintenance/exclusions", headers=VIEWER_HEADERS).json() == []


def test_adhoc_run_refuses_when_every_selected_table_is_excluded(api_db: ApiDb) -> None:
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    hot_table_id, _ = _seed_tables(api_db.session_factory, source_id)
    client = api_db.client
    preset_id = _create_preset(client, "standard", destination_id, priority=100)
    excluded = client.post(
        "/api/v1/maintenance/exclusions",
        headers=OPERATOR_HEADERS,
        json={
            "source_id": str(source_id),
            "schema_name": "analytics",
            "table_name": "hot_events",
            "reason": "owner freeze",
        },
    )
    assert excluded.status_code == 201

    response = client.post(
        "/api/v1/maintenance/run",
        headers=OPERATOR_HEADERS,
        json={
            "destination_id": str(destination_id),
            "preset_id": preset_id,
            "mode": "standard",
            "table_ids": [str(hot_table_id)],
        },
    )
    assert response.status_code == 409
    assert "excluded" in response.json()["detail"]


def test_paused_batch_records_a_skipped_run_and_does_no_work(session_factory) -> None:
    with session_factory() as session:
        _seed(session)
        session.add(
            Destination(
                id=uuid.uuid4(),
                name="spark",
                engine="spark_emr",
                enabled=True,
                config={"catalog": "lake"},
            )
        )
        set_pause_state(
            session,
            scope_type="global",
            scope_id="global",
            paused=True,
            changed_by="admin",
            reason="incident",
        )
        session.commit()

    coordinator = BatchMaintenance(
        session_factory=session_factory,
        settings=Settings(maintenance_poll_seconds=5),
        lock_store=InMemoryLockStore(),
        clock=lambda: NOW,
    )
    report = coordinator.run(trigger="batch-cron")

    assert report.paused is True
    assert report.selected == 0
    with session_factory() as session:
        run = session.scalar(select(MaintenanceRun))
        assert run is not None
        assert run.status == "skipped"
        assert run.summary["paused"] is True


def test_scheduler_fires_no_presets_while_globally_paused(session_factory) -> None:
    store = SQLSchedulerStore(session_factory, redis_client=None)
    with session_factory() as session:
        set_pause_state(
            session,
            scope_type="global",
            scope_id="global",
            paused=True,
            changed_by="admin",
            reason="incident",
        )
        session.commit()
    assert store.list_due_presets(NOW) == []


def test_exclusion_model_allows_one_rule_per_target(session) -> None:
    source, _ = _seed(session)
    create_exclusion(
        session,
        source_id=source.id,
        schema_name="sales",
        table_name=None,
        reason="schema hold",
        actor="op",
    )
    create_exclusion(
        session,
        source_id=source.id,
        schema_name="sales",
        table_name="orders",
        reason="table hold",
        actor="op",
    )
    session.commit()
    assert len(list(session.scalars(select(MaintenanceExclusion)))) == 2


def test_table_hold_blocks_selection_until_it_expires(session) -> None:
    _source, tables = _seed(session)
    set_table_hold(
        session,
        tables["orders"],
        reason="backfill in progress",
        expires_at=NOW + timedelta(days=2),
        actor="op",
    )
    session.commit()
    assert "orders" not in _candidate_names(session)

    later = load_candidates(
        session, now=NOW + timedelta(days=3), cooldown_hours=20, max_stats_age_days=30
    )
    assert "orders" in {candidate.table_name for candidate in later}
    assert exclusions_covering(session, tables["orders"], now=NOW + timedelta(days=3)) == []

    actions = list(
        session.scalars(select(AuditLog.detail).where(AuditLog.table_id == tables["orders"].id))
    )
    assert any("until=" in (detail or "") for detail in actions)


def test_held_table_is_recorded_as_held_not_generic_excluded(session) -> None:
    from cryo.maintenance.selection import _exclusion_rows
    from cryo.maintenance.skip_history import REASON_HELD

    _source, tables = _seed(session)
    set_table_hold(
        session,
        tables["orders"],
        reason="backfill",
        expires_at=NOW + timedelta(days=1),
        actor="op",
    )
    session.commit()
    rows = _exclusion_rows(
        session,
        now=NOW,
        cooldown_hours=20,
        max_stats_age_days=3,
        table_ids=None,
        candidate_ids=set(),
    )
    reasons = {table_id: reason for table_id, reason, _work in rows}
    assert reasons[tables["orders"].id] == REASON_HELD


def test_expired_hold_does_not_block_a_new_exclusion(session) -> None:
    source, tables = _seed(session)
    hold = set_table_hold(
        session,
        tables["orders"],
        reason="short freeze",
        expires_at=NOW + timedelta(hours=1),
        actor="op",
    )
    hold.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()
    create_exclusion(
        session,
        source_id=source.id,
        schema_name="sales",
        table_name="orders",
        reason="permanent opt-out",
        actor="op",
    )
    session.commit()
    rows = list(session.scalars(select(MaintenanceExclusion)))
    assert [(row.reason, row.expires_at) for row in rows] == [("permanent opt-out", None)]


def test_table_hold_api_requires_operator_and_bounded_expiry(api_db: ApiDb) -> None:
    source_id, _ = _seed_source_destination(api_db.session_factory)
    hot_table_id, _ = _seed_tables(api_db.session_factory, source_id)
    client = api_db.client
    url = f"/api/v1/tables/{hot_table_id}/maintenance-hold"
    soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()

    assert client.get(url, headers=VIEWER_HEADERS).json()["held"] is False
    body = {"reason": "vendor backfill", "expires_at": soon}
    assert client.put(url, headers=VIEWER_HEADERS, json=body).status_code == 403
    past = {"reason": "vendor backfill", "expires_at": "2020-01-01T00:00:00Z"}
    assert client.put(url, headers=OPERATOR_HEADERS, json=past).status_code == 422
    far = (datetime.now(UTC) + timedelta(days=120)).isoformat()
    too_long = {"reason": "vendor backfill", "expires_at": far}
    assert client.put(url, headers=OPERATOR_HEADERS, json=too_long).status_code == 422

    held = client.put(url, headers=OPERATOR_HEADERS, json=body)
    assert held.status_code == 200
    assert held.json()["held"] is True
    assert held.json()["scope"] == "table"
    assert held.json()["reason"] == "vendor backfill"

    listed = client.get("/api/v1/maintenance/exclusions", headers=VIEWER_HEADERS).json()
    assert listed[0]["expires_at"] is not None

    released = client.delete(url, headers=OPERATOR_HEADERS)
    assert released.status_code == 200
    assert released.json()["held"] is False
    assert client.delete(url, headers=OPERATOR_HEADERS).status_code == 404


def test_admin_headers_can_also_manage_exclusions(api_db: ApiDb) -> None:
    source_id, _ = _seed_source_destination(api_db.session_factory)
    response = api_db.client.post(
        "/api/v1/maintenance/exclusions",
        headers=ADMIN_HEADERS,
        json={"source_id": str(source_id), "schema_name": " raw ", "reason": "  scratch  "},
    )
    assert response.status_code == 201
    assert response.json()["schema_name"] == "raw"
    assert response.json()["reason"] == "scratch"
