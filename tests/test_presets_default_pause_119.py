"""#119: the default preset covers new tables; pause holds tables, disable retires a preset."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from cryo.config import get_settings
from cryo.db.models import AuditLog, Destination, Preset, Source, Table, TableAssignment
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from cryo.scheduler.rules import (
    PresetConfig,
    PresetRule,
    TableCandidate,
    effective_preset,
    tables_for_preset,
)
from cryo.scheduler.rules import (
    TableAssignment as AssignmentConfig,
)
from cryo.scheduler.scheduler import build_scheduled_run
from cryo.scheduler.service import InMemorySchedulerStore, SchedulerService
from tests.auth_helpers import ADMIN_HEADERS, OPERATOR_HEADERS, VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb

NOW = datetime(2026, 10, 26, 2, 0, tzinfo=UTC)
CRON = "0 2 * * *"


def _default(**overrides: object) -> PresetConfig:
    values: dict[str, object] = {
        "id": "default",
        "name": "default-spark-compact",
        "destination_id": "spark",
        "priority": 0,
        "is_default": True,
        "schedule_cron": CRON,
    }
    values.update(overrides)
    return PresetConfig(**values)  # type: ignore[arg-type]


def _named(preset_id: str, **overrides: object) -> PresetConfig:
    values: dict[str, object] = {
        "id": preset_id,
        "name": preset_id,
        "destination_id": "trino",
        "schedule_cron": CRON,
    }
    values.update(overrides)
    return PresetConfig(**values)  # type: ignore[arg-type]


TABLE = TableCandidate("t1", "sales", "orders", {"total_size_gb": 5})


# --- resolution ---------------------------------------------------------------------------


def test_unassigned_table_resolves_to_the_default_preset() -> None:
    effective = effective_preset(TABLE, [_default()], rules=())
    assert effective is not None
    assert (effective.preset.id, effective.provenance) == ("default", "default")


def test_paused_default_still_wins_resolution_and_does_not_reroute() -> None:
    other = _named("other")
    effective = effective_preset(TABLE, [_default(paused=True), other], rules=())
    assert effective is not None
    assert effective.preset.id == "default"
    assert effective.preset.paused is True
    assert effective.provenance == "default"


def test_table_pinned_to_a_paused_preset_is_held_not_rerouted_to_the_default() -> None:
    pinned = _named("pinned", paused=True)
    effective = effective_preset(
        TABLE,
        [_default(), pinned],
        rules=(),
        assignments=[AssignmentConfig("t1", "pinned")],
    )
    assert effective is not None
    assert (effective.preset.id, effective.provenance) == ("pinned", "assignment")
    assert (
        tables_for_preset(
            _default(), [TABLE], [_default(), pinned], (), [AssignmentConfig("t1", "pinned")]
        )
        == []
    )


def test_table_pinned_to_a_disabled_preset_still_falls_through() -> None:
    retired = _named("retired", enabled=False)
    effective = effective_preset(
        TABLE,
        [_default(), retired],
        rules=(),
        assignments=[AssignmentConfig("t1", "retired")],
    )
    assert effective is not None
    assert (effective.preset.id, effective.provenance) == ("default", "default")


def test_unset_default_with_no_replacement_leaves_the_table_unmaintained() -> None:
    assert effective_preset(TABLE, [_default(is_default=False)], rules=()) is None


def test_rule_or_pin_moves_a_table_off_the_default_and_unpinning_returns_it() -> None:
    ruled = _named("ruled")
    rule = PresetRule("r1", "ruled", {"field": "schema_name", "op": "=", "value": "sales"})
    by_rule = effective_preset(TABLE, [_default(), ruled], [rule])
    assert by_rule is not None and by_rule.preset.id == "ruled"

    pinned = _named("pinned")
    by_pin = effective_preset(TABLE, [_default(), pinned], (), [AssignmentConfig("t1", "pinned")])
    assert by_pin is not None and by_pin.preset.id == "pinned"

    unpinned = effective_preset(TABLE, [_default(), pinned], (), ())
    assert unpinned is not None and unpinned.provenance == "default"


def test_ephemeral_tables_never_land_on_the_default() -> None:
    tmp = TableCandidate("t2", "sales", "orders__dbt_tmp")
    assert effective_preset(tmp, [_default()], rules=()) is None


# --- scheduling ---------------------------------------------------------------------------


def test_build_scheduled_run_creates_no_jobs_for_a_paused_preset() -> None:
    paused = _default(paused=True)
    run = build_scheduled_run(paused, [TABLE], [paused], (), now=NOW)
    assert run.jobs == ()


def test_in_memory_store_never_returns_a_paused_preset_as_due() -> None:
    store = InMemorySchedulerStore()
    store.presets = [_default(paused=True), _named("live")]
    assert [preset.id for preset, _ in store.list_due_presets(NOW)] == ["live"]


@pytest.fixture()
def _budget(monkeypatch):
    settings = get_settings().model_copy(update={"max_jobs_per_scheduled_run": 2})
    monkeypatch.setattr("cryo.config.get_settings", lambda: settings)
    return settings


def test_default_preset_fires_on_its_cron_bounded_by_the_run_budget(_budget) -> None:
    store = InMemorySchedulerStore()
    store.presets = [_default()]
    store.tables = [
        TableCandidate(f"t{i}", "sales", f"orders_{i}", {"total_size_gb": 5}) for i in range(5)
    ]
    store.clock = lambda: NOW
    service = SchedulerService(store, InMemoryMaintenanceQueue())
    result = service._dispatch_due_presets(NOW)
    assert result.preset_ids == ("default",)
    assert result.jobs_enqueued == 2


def test_paused_default_preset_never_fires(_budget) -> None:
    store = InMemorySchedulerStore()
    store.presets = [_default(paused=True)]
    store.tables = [TABLE]
    store.clock = lambda: NOW
    service = SchedulerService(store, InMemoryMaintenanceQueue())
    assert service._dispatch_due_presets(NOW).jobs_enqueued == 0
    assert store.runs == []


# --- API ----------------------------------------------------------------------------------


def _destination(api_db: ApiDb) -> str:
    response = api_db.client.post(
        "/api/v1/destinations",
        headers=ADMIN_HEADERS,
        json={"name": "spark", "engine": "trino", "config": {"host": "trino"}},
    )
    assert response.status_code == 201
    return str(response.json()["id"])


def _preset(api_db: ApiDb, destination_id: str, name: str, **extra: object) -> dict:
    body: dict[str, object] = {
        "name": name,
        "destination_id": destination_id,
        "operations": ["optimize"],
        "schedule_cron": CRON,
    }
    body.update(extra)
    response = api_db.client.post("/api/v1/presets", headers=ADMIN_HEADERS, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _audit(api_db: ApiDb, action: str) -> list[AuditLog]:
    with api_db.session_factory() as session:
        return list(session.scalars(select(AuditLog).where(AuditLog.action == action)))


def test_default_preset_requires_a_schedule(api_db: ApiDb) -> None:
    destination_id = _destination(api_db)
    rejected = api_db.client.post(
        "/api/v1/presets",
        headers=ADMIN_HEADERS,
        json={
            "name": "nocron",
            "destination_id": destination_id,
            "operations": ["optimize"],
            "is_default": True,
        },
    )
    assert rejected.status_code == 400
    assert "schedule" in rejected.text

    plain = _preset(api_db, destination_id, "plain", schedule_cron=None)
    patch = api_db.client.patch(
        f"/api/v1/presets/{plain['id']}", headers=ADMIN_HEADERS, json={"is_default": True}
    )
    assert patch.status_code == 400

    default = _preset(api_db, destination_id, "default", is_default=True)
    clear_cron = api_db.client.patch(
        f"/api/v1/presets/{default['id']}", headers=ADMIN_HEADERS, json={"schedule_cron": None}
    )
    assert clear_cron.status_code == 400

    unrelated = api_db.client.patch(
        f"/api/v1/presets/{plain['id']}", headers=ADMIN_HEADERS, json={"priority": 5}
    )
    assert unrelated.status_code == 200


def test_paused_is_exposed_and_defaults_to_false(api_db: ApiDb) -> None:
    destination_id = _destination(api_db)
    created = _preset(api_db, destination_id, "p")
    assert created["paused"] is False
    fetched = api_db.client.get(f"/api/v1/presets/{created['id']}", headers=VIEWER_HEADERS)
    assert fetched.json()["paused"] is False


def test_pause_resume_set_and_unset_default_are_admin_only_and_audited(api_db: ApiDb) -> None:
    destination_id = _destination(api_db)
    first = _preset(api_db, destination_id, "first", is_default=True)
    second = _preset(api_db, destination_id, "second")
    client = api_db.client

    with api_db.session_factory() as session:
        source = Source(name="hms", type="rest", config={})
        session.add(source)
        session.flush()
        session.add(Table(source_id=source.id, schema_name="sales", table_name="orders"))
        session.commit()

    denied = client.post(f"/api/v1/presets/{first['id']}/pause", headers=OPERATOR_HEADERS)
    assert denied.status_code == 403

    paused = client.post(f"/api/v1/presets/{first['id']}/pause", headers=ADMIN_HEADERS)
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    resumed = client.post(f"/api/v1/presets/{first['id']}/resume", headers=ADMIN_HEADERS)
    assert resumed.json()["paused"] is False

    switched = client.post(f"/api/v1/presets/{second['id']}/set-default", headers=ADMIN_HEADERS)
    assert switched.status_code == 200
    assert switched.json()["is_default"] is True
    assert (
        client.get(f"/api/v1/presets/{first['id']}", headers=VIEWER_HEADERS).json()["is_default"]
        is False
    )

    unset = client.post(f"/api/v1/presets/{second['id']}/unset-default", headers=ADMIN_HEADERS)
    assert unset.json()["is_default"] is False

    (pause_row,) = _audit(api_db, "preset_paused")
    assert pause_row.actor
    assert "paused=False->True" in (pause_row.detail or "")
    assert "affected_tables=1" in (pause_row.detail or "")
    assert len(_audit(api_db, "preset_resumed")) == 1
    set_rows = [
        row
        for row in _audit(api_db, "preset_default_set")
        if f"preset_id={second['id']}" in (row.detail or "")
    ]
    assert len(set_rows) == 1
    assert f"previous_default={first['id']}" in (set_rows[0].detail or "")
    assert len(_audit(api_db, "preset_default_unset")) == 1


def test_set_default_rejects_a_preset_without_a_schedule(api_db: ApiDb) -> None:
    destination_id = _destination(api_db)
    plain = _preset(api_db, destination_id, "plain", schedule_cron=None)
    response = api_db.client.post(
        f"/api/v1/presets/{plain['id']}/set-default", headers=ADMIN_HEADERS
    )
    assert response.status_code == 400


def test_patch_that_pauses_or_switches_default_is_audited(api_db: ApiDb) -> None:
    destination_id = _destination(api_db)
    preset = _preset(api_db, destination_id, "p")
    response = api_db.client.patch(
        f"/api/v1/presets/{preset['id']}", headers=ADMIN_HEADERS, json={"paused": True}
    )
    assert response.status_code == 200
    assert response.json()["paused"] is True
    assert len(_audit(api_db, "preset_paused")) == 1


def test_manual_run_on_a_paused_preset_needs_an_explicit_override(api_db: ApiDb) -> None:
    destination_id = _destination(api_db)
    preset = _preset(api_db, destination_id, "p", paused=True)
    with api_db.session_factory() as session:
        source = Source(name="hms", type="rest", config={})
        session.add(source)
        session.flush()
        table = Table(source_id=source.id, schema_name="sales", table_name="orders")
        session.add(table)
        session.commit()
        table_id = str(table.id)
    body = {"table_ids": [table_id], "destination_id": destination_id, "preset_id": preset["id"]}
    response = api_db.client.post("/api/v1/maintenance/run", headers=OPERATOR_HEADERS, json=body)
    assert response.status_code == 409
    assert "paused" in response.text


def test_sql_store_skips_paused_presets(session) -> None:
    from cryo.db.repositories.scheduler_store import SQLSchedulerStore

    destination = Destination(name="d", engine="trino", config={})
    session.add(destination)
    session.flush()
    session.add_all(
        [
            Preset(
                name="live",
                destination_id=destination.id,
                operations=["optimize"],
                params={},
                guardrails={},
                schedule_cron=CRON,
            ),
            Preset(
                name="held",
                destination_id=destination.id,
                operations=["optimize"],
                params={},
                guardrails={},
                schedule_cron=CRON,
                paused=True,
            ),
        ]
    )
    session.commit()

    class _Redis:
        def get(self, key: str) -> None:
            return None

        def hget(self, *args: object) -> None:
            return None

    store = SQLSchedulerStore(lambda: session, _Redis())
    store_presets = store.list_due_presets(NOW)
    assert [preset.name for preset, _ in store_presets] == ["live"]


def test_table_assignment_model_unchanged() -> None:
    # Default routing stays implicit: no TableAssignment row is ever written for it.
    assert "pinned" in TableAssignment.__table__.columns


# --- scanner: new tables on the default ----------------------------------------------------


def test_scanner_counts_and_audits_new_tables_that_land_on_the_default(session) -> None:
    from uuid import uuid4

    from cryo.scanner.new_tables import record_new_tables_on_default, unregistered_keys

    destination = Destination(name="d", engine="trino", config={})
    source = Source(id=uuid4(), name="hms", type="rest", config={})
    session.add_all([destination, source])
    session.flush()
    default = Preset(
        name="default",
        destination_id=destination.id,
        operations=["optimize"],
        params={},
        guardrails={},
        schedule_cron=CRON,
        is_default=True,
    )
    session.add(default)
    session.add(Table(source_id=source.id, schema_name="sales", table_name="old"))
    session.commit()

    listed = [
        (source.id, "sales", "old"),
        (source.id, "sales", "fresh"),
        (source.id, "sales", "fresh__dbt_tmp"),
    ]
    new_keys = unregistered_keys(session, listed)
    assert new_keys == {(source.id, "sales", "fresh"), (source.id, "sales", "fresh__dbt_tmp")}

    session.add_all(
        [
            Table(source_id=source.id, schema_name="sales", table_name="fresh"),
            Table(source_id=source.id, schema_name="sales", table_name="fresh__dbt_tmp"),
        ]
    )
    session.commit()

    assert record_new_tables_on_default(session, new_keys) == 1
    rows = list(
        session.scalars(select(AuditLog).where(AuditLog.action == "table_auto_assigned_default"))
    )
    assert len(rows) == 1
    assert "sales.fresh " in (rows[0].detail or "")


def test_scanner_records_nothing_when_there_is_no_default(session) -> None:
    from uuid import uuid4

    from cryo.scanner.new_tables import record_new_tables_on_default

    source = Source(id=uuid4(), name="hms", type="rest", config={})
    session.add(source)
    session.add(Table(source_id=source.id, schema_name="sales", table_name="fresh"))
    session.commit()
    assert record_new_tables_on_default(session, {(source.id, "sales", "fresh")}) == 0


def test_scan_chunk_totals_sum_new_tables_on_default() -> None:
    from cryo.db.repositories.scanner import _recompute_chunk_totals

    summary = {
        "chunks": {
            "0": {"status": "succeeded", "new_tables_on_default": 2},
            "1": {"status": "succeeded", "new_tables_on_default": 3},
        }
    }
    _recompute_chunk_totals(summary)
    assert summary["new_tables_on_default"] == 5
