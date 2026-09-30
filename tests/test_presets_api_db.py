from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from cryo.api.app import create_app
from cryo.api.deps import get_tenant_session
from cryo.config import Settings
from cryo.db.base import Base
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Preset,
    PresetRule,
    SharedS3AnomalyReport,
    Source,
    Table,
    TableAlertDispatch,
    TableAlertSubscription,
    TableCostHistory,
    TableStats,
    TableStatsHistory,
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
    SharedS3AnomalyReport,
)

_AUTOINCREMENT_ID_MODELS = (AuditLog, TableStatsHistory, TableCostHistory)
_next_autoincrement_id = itertools.count(1)


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
    # Real bug fixed here, 2026-09-21: this file's own private _patch_sqlite_incompatible_
    # defaults() below is a near-duplicate of conftest.py's _session_maker fixture's helper of
    # the same name, but with no restore-on-teardown -- it permanently nulled server_default
    # (e.g. gen_random_uuid()) on every shared model class, process-wide, for the rest of the
    # test run the instant any test using this api_db fixture executed. Confirmed live: any
    # later real-Postgres test inserting an IncidentCohort/AnalysisTask/etc. row (relying on
    # Postgres to generate the UUID) then failed with a NULL identity key, purely because this
    # fixture ran earlier in the same pytest process and never put the default back. Restoring
    # it in `finally`, same pattern as conftest.py's own fixture, closes that leak here too.
    restore_defaults = _patch_sqlite_incompatible_defaults()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
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
        event.listen(session_factory, "before_flush", _assign_missing_autoincrement_primary_keys)

        def override_session() -> Iterator[Session]:
            with session_factory() as session:
                yield session

        app = create_app(Settings(_env_file=None, environment="test"))
        app.dependency_overrides[get_tenant_session] = override_session
        with TestClient(app) as client:
            yield ApiDb(client=client, session_factory=session_factory)

        event.remove(session_factory, "before_flush", _assign_missing_uuid_primary_keys)
        event.remove(session_factory, "before_flush", _assign_missing_autoincrement_primary_keys)
        engine.dispose()
    finally:
        for column, original_default in restore_defaults:
            column.server_default = original_default


_PG_URL = "postgresql+psycopg://cryo:cryo@localhost:5432/cryo"


@pytest.fixture()
def postgres_session() -> Iterator[Session]:
    """Real Postgres session -- see test_datalake_dashboard_repository.py's fixture of the same
    name for the general rationale (SQLite doesn't enforce constraints/transaction semantics
    Postgres does, so Postgres-only bugs are invisible to the rest of this suite). Skips
    cleanly if no local Postgres is available (`docker compose up -d postgres` +
    `alembic upgrade head`, per the Makefile's `migrate` target)."""
    try:
        engine = create_engine(_PG_URL)
        connection = engine.connect()
    except OperationalError:
        pytest.skip("local Postgres not available at localhost:5432 (see Makefile: migrate)")
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def _assign_missing_autoincrement_primary_keys(
    session: Session,
    flush_context: object,
    instances: object,
) -> None:
    for item in session.new:
        if not isinstance(item, _AUTOINCREMENT_ID_MODELS):
            continue
        mapper = inspect(item.__class__)
        pk_cols = mapper.primary_key
        if len(pk_cols) != 1:
            continue
        pk_attr = pk_cols[0].key
        if getattr(item, pk_attr) is None:
            setattr(item, pk_attr, next(_next_autoincrement_id))


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


def test_effective_preset_reports_rule_after_stale_pin_without_500(api_db: ApiDb) -> None:
    """Regression, confirmed live 2026-09-14: user_experience.silver_results 500'd on every
    GET /tables/{id}/effective-preset call. cryo.scheduler.rules.resolve_effective_preset
    correctly falls through to the rule engine when a table's pinned assignment points at a
    disabled preset (documented safety behavior -- a hard stop there would silently blackhole
    the table forever; found live in prod: 21 tables pinned to retired presets, including a
    24TB table, never rescheduled) and reports provenance="rule_after_stale_pin" -- but
    EffectivePresetResponse's Literal type never included that value, so Pydantic validation
    raised on construction and the endpoint 500'd for every table that had ever hit this
    fallback path, instead of returning the (correct, safer-than-None) rule match."""
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    hot_table_id, _cold_table_id = _seed_tables(api_db.session_factory, source_id)
    client = api_db.client

    stale_id = _create_preset(client, "stale", destination_id, priority=10)
    fallback_id = _create_preset(client, "fallback", destination_id, priority=5)

    assign_response = client.post(
        "/api/v1/assignments",
        headers=OPERATOR_HEADERS,
        json={
            "preset_id": stale_id,
            "selection": {"mode": "ids", "ids": [str(hot_table_id)]},
            "assigned_by": "pytest",
        },
    )
    assert assign_response.status_code == 200

    disable_response = client.patch(
        f"/api/v1/presets/{stale_id}",
        headers=ADMIN_HEADERS,
        json={"enabled": False},
    )
    assert disable_response.status_code == 200
    assert disable_response.json()["enabled"] is False

    rule_response = client.post(
        f"/api/v1/presets/{fallback_id}/rules",
        headers=ADMIN_HEADERS,
        json={"predicate": {"field": "small_files_count", "op": ">=", "value": 50}},
    )
    assert rule_response.status_code == 201

    effective_response = client.get(
        f"/api/v1/tables/{hot_table_id}/effective-preset",
        headers=VIEWER_HEADERS,
    )
    assert effective_response.status_code == 200
    body = effective_response.json()
    assert body["provenance"] == "rule_after_stale_pin"
    assert body["preset_id"] == fallback_id


def test_table_param_overrides_layer_on_preset_and_are_admin_gated(api_db: ApiDb) -> None:
    """Per-table param tweaks (e.g. a tighter retention_days on one hot/test table) must
    layer on top of the assigned preset's params without mutating the preset itself --
    the preset here is shared, so a change to it would leak to every other table pinned
    to it."""
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    hot_table_id, cold_table_id = _seed_tables(api_db.session_factory, source_id)
    client = api_db.client

    preset_response = client.post(
        "/api/v1/presets",
        headers=ADMIN_HEADERS,
        json={
            "name": "shared-preset",
            "destination_id": str(destination_id),
            "operations": ["expire_snapshots", "optimize"],
            "params": {"min_small_files": 10, "retention_days": 7},
            "priority": 100,
        },
    )
    assert preset_response.status_code == 201
    preset_id = preset_response.json()["id"]

    assign_response = client.post(
        "/api/v1/assignments",
        headers=OPERATOR_HEADERS,
        json={
            "preset_id": preset_id,
            "selection": {"mode": "ids", "ids": [str(hot_table_id)]},
        },
    )
    assert assign_response.status_code == 200

    baseline = client.get(f"/api/v1/tables/{hot_table_id}/effective-params", headers=VIEWER_HEADERS)
    assert baseline.status_code == 200
    assert baseline.json()["param_overrides"] == {}
    assert baseline.json()["effective_params"] == {"min_small_files": 10, "retention_days": 7}

    viewer_patch = client.patch(
        f"/api/v1/tables/{hot_table_id}/param-overrides",
        headers=VIEWER_HEADERS,
        json={"param_overrides": {"retention_days": 1}},
    )
    assert viewer_patch.status_code == 403

    operator_patch = client.patch(
        f"/api/v1/tables/{hot_table_id}/param-overrides",
        headers=OPERATOR_HEADERS,
        json={"param_overrides": {"retention_days": 1}},
    )
    assert operator_patch.status_code == 403

    invalid_patch = client.patch(
        f"/api/v1/tables/{hot_table_id}/param-overrides",
        headers=ADMIN_HEADERS,
        json={"param_overrides": {"retention_days": -1}},
    )
    assert invalid_patch.status_code == 400

    admin_patch = client.patch(
        f"/api/v1/tables/{hot_table_id}/param-overrides",
        headers=ADMIN_HEADERS,
        json={"param_overrides": {"retention_days": 1}},
    )
    assert admin_patch.status_code == 200
    assert admin_patch.json()["param_overrides"] == {"retention_days": 1}
    # Override wins for retention_days; min_small_files still inherited from the preset.
    assert admin_patch.json()["effective_params"] == {"min_small_files": 10, "retention_days": 1}

    refetched = client.get(
        f"/api/v1/tables/{hot_table_id}/effective-params", headers=VIEWER_HEADERS
    )
    assert refetched.json()["effective_params"]["retention_days"] == 1

    preset_unchanged = client.get(f"/api/v1/presets/{preset_id}", headers=VIEWER_HEADERS)
    assert preset_unchanged.json()["params"]["retention_days"] == 7

    unassigned_patch = client.patch(
        f"/api/v1/tables/{cold_table_id}/param-overrides",
        headers=ADMIN_HEADERS,
        json={"param_overrides": {"retention_days": 1}},
    )
    assert unassigned_patch.status_code == 400


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


def test_maintenance_run_applies_adaptive_routing_around_failing_destination(
    api_db: ApiDb,
) -> None:
    """Regression, confirmed live 2026-08-11: unified_bq.gold_dbt_pw_bq failed on
    native-datafusion 4 times in a row, and consecutive_engine_failures() correctly detected
    that streak -- but every retry actually hit this table through POST /maintenance/run
    (trigger="api"/"claude-retry-failed-*"), which always dispatched to the single
    `destination_id` the caller passed and never consulted adaptive routing at all. Only the
    scheduler's cron path (_apply_routing) applied the failure-exclusion logic. This is the
    ad-hoc API path getting the same protection.
    """
    source_id, native_destination_id = _seed_source_destination(api_db.session_factory)
    hot_table_id, _ = _seed_tables(api_db.session_factory, source_id)

    with api_db.session_factory() as session:
        # A second, healthy destination adaptive routing should fall back to once
        # native_destination_id is excluded for this table.
        spark_destination_id = uuid.uuid4()
        session.add(
            Destination(
                id=spark_destination_id,
                name="spark-fallback",
                engine="spark",
                config={"catalog_bindings": [{"source_id": str(source_id)}]},
            )
        )
        session.commit()

        # Seed 2 consecutive failures on native_destination_id for hot_table_id -- matches
        # the default max_consecutive_failures_before_exclusion threshold.
        for i in range(2):
            run_id = uuid.uuid4()
            session.add(MaintenanceRun(id=run_id, kind="adhoc", status="succeeded"))
            session.flush()
            session.add(
                MaintenanceJob(
                    id=uuid.uuid4(),
                    run_id=run_id,
                    table_id=hot_table_id,
                    destination_id=native_destination_id,
                    mode="standard",
                    status="failed",
                    started_at=datetime(2026, 8, 10, 10 + i, tzinfo=UTC),
                    finished_at=datetime(2026, 8, 10, 10 + i, 5, tzinfo=UTC),
                )
            )
            session.commit()

    client = api_db.client
    preset_id = _create_preset(client, "adaptive-standard", native_destination_id, priority=100)

    # Turn on adaptive_routing and scope engine_pool to both destinations, matching how
    # default-native-deep is actually configured in prod.
    patch_response = client.patch(
        f"/api/v1/presets/{preset_id}",
        headers=ADMIN_HEADERS,
        json={
            "guardrails": {
                "adaptive_routing": True,
                "engine_pool": [str(native_destination_id), str(spark_destination_id)],
            }
        },
    )
    assert patch_response.status_code == 200

    run_response = client.post(
        "/api/v1/maintenance/run",
        headers=OPERATOR_HEADERS,
        json={
            "destination_id": str(native_destination_id),
            "preset_id": preset_id,
            "mode": "standard",
            "table_ids": [str(hot_table_id)],
        },
    )
    assert run_response.status_code == 201
    job_id = run_response.json()["job_ids"][0]

    job_detail = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER_HEADERS).json()
    # The bug: this used to always equal native_destination_id (payload.destination_id),
    # regardless of the 2 prior failures. The fix: adaptive routing should have routed this
    # job away from the excluded destination.
    assert job_detail["destination_id"] != str(native_destination_id)


def test_adaptive_routing_helper_does_not_close_the_caller_session() -> None:
    """Regression, confirmed live 2026-08-11: _adaptive_routing_destinations_for_run used to
    build `SQLSchedulerStore(lambda: session, redis_client=None)` -- but SQLSchedulerStore's
    methods do `with self._session_factory() as session:`, i.e. they call __exit__ on
    whatever the "factory" returns. Passed the live request-scoped session directly, that
    closes/rolls back it partway through create_maintenance_run's own request, so the later
    MaintenanceRun/MaintenanceJob inserts in the same request hit "insert or update on table
    jobs violates foreign key constraint fk_jobs_run_id_runs" once Postgres actually enforced
    the now-invisible earlier insert -- a real prod 500 on POST /maintenance/run whenever
    adaptive_routing was on and the table had any prior job history at all (findable by
    consecutive_engine_failures's own query, whether or not it changes the routing decision).

    test_maintenance_run_applies_adaptive_routing_around_failing_destination (above) exercises
    the same endpoint but does NOT catch this on in-memory SQLite (session.is_active stayed
    True and a later commit did not raise). This test needs real Postgres AND a session bound
    directly to the engine via sessionmaker -- exactly how cryo/db/session.py's real
    `get_session()` builds the request session (`with SessionLocal() as session: yield
    session`, SessionLocal = sessionmaker(bind=engine, ...)`). The shared postgres_session
    fixture (see test_datalake_dashboard_repository.py) binds Session to a single pre-opened
    `connection` instead, for test-isolation-via-rollback -- confirmed live that shape does
    NOT reproduce this bug either (a connection-bound session's close/reopen semantics differ
    enough that __exit__ being called twice doesn't detach it the same way). Skips cleanly
    without local Postgres, same as postgres_session."""
    from sqlalchemy.exc import OperationalError as SAOperationalError
    from sqlalchemy.orm import sessionmaker as sa_sessionmaker

    from cryo.api.routes import _adaptive_routing_destinations_for_run

    try:
        engine = create_engine(_PG_URL)
        engine.connect().close()
    except SAOperationalError:
        pytest.skip("local Postgres not available at localhost:5432 (see Makefile: migrate)")

    SessionLocal = sa_sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    source_id = uuid.uuid4()
    destination_id = uuid.uuid4()
    table_id = uuid.uuid4()
    preset_id = uuid.uuid4()
    try:
        with SessionLocal() as session:
            session.add(Source(id=source_id, name=f"src-{source_id}", type="rest", config={}))
            session.add(
                Destination(
                    id=destination_id,
                    name=f"dest-{destination_id}",
                    engine="native",
                    config={"catalog_bindings": [{"source_id": str(source_id)}]},
                )
            )
            session.add(
                Table(
                    id=table_id,
                    source_id=source_id,
                    schema_name="analytics",
                    table_name=f"hot_events_{table_id.hex[:8]}",
                    is_active=True,
                )
            )
            session.add(
                Preset(
                    id=preset_id,
                    name=f"adaptive-pg-test-{preset_id.hex[:8]}",
                    destination_id=destination_id,
                    operations=["optimize"],
                    params={},
                    priority=100,
                    guardrails={"adaptive_routing": True},
                )
            )
            # Any prior job history is enough to exercise consecutive_engine_failures' query --
            # doesn't need to actually trigger a routing change to reproduce the bug.
            old_run_id = uuid.uuid4()
            session.add(MaintenanceRun(id=old_run_id, kind="adhoc", status="succeeded"))
            session.commit()
            session.add(
                MaintenanceJob(
                    id=uuid.uuid4(),
                    run_id=old_run_id,
                    table_id=table_id,
                    destination_id=destination_id,
                    mode="standard",
                    status="succeeded",
                    started_at=datetime(2026, 8, 10, 10, tzinfo=UTC),
                    finished_at=datetime(2026, 8, 10, 10, 5, tzinfo=UTC),
                )
            )
            session.commit()
            preset = session.get(Preset, preset_id)

            # Matches create_maintenance_run's real sequence exactly: flush(), not commit(),
            # before calling the helper -- this is what makes the bug reproducible.
            new_run = MaintenanceRun(id=uuid.uuid4(), kind="adhoc", status="queued")
            session.add(new_run)
            session.flush()

            _adaptive_routing_destinations_for_run(session, preset, [table_id])

            # The real bug: this session would already be closed/detached here, so new_run's
            # flushed-but-uncommitted row is gone and this insert 500s with a FK violation.
            session.add(
                MaintenanceJob(
                    id=uuid.uuid4(),
                    run_id=new_run.id,
                    table_id=table_id,
                    destination_id=destination_id,
                    mode="standard",
                    status="queued",
                )
            )
            session.flush()
            session.commit()
    finally:
        with engine.connect() as cleanup_conn:
            cleanup_conn.execute(
                text("DELETE FROM cryo.jobs WHERE table_id = :tid"), {"tid": table_id}
            )
            cleanup_conn.execute(text("DELETE FROM cryo.runs"))
            cleanup_conn.execute(
                text("DELETE FROM cryo.presets WHERE id = :pid"), {"pid": preset_id}
            )
            cleanup_conn.execute(text("DELETE FROM cryo.tables WHERE id = :tid"), {"tid": table_id})
            cleanup_conn.execute(
                text("DELETE FROM cryo.destinations WHERE id = :did"), {"did": destination_id}
            )
            cleanup_conn.execute(
                text("DELETE FROM cryo.sources WHERE id = :sid"), {"sid": source_id}
            )
            cleanup_conn.commit()
        engine.dispose()


def test_maintenance_run_keeps_payload_destination_when_adaptive_routing_is_off(
    api_db: ApiDb,
) -> None:
    """The common case (adaptive_routing not set on the preset) must be unaffected --
    payload.destination_id still wins, exactly as before this fix."""
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    hot_table_id, _ = _seed_tables(api_db.session_factory, source_id)
    client = api_db.client

    preset_id = _create_preset(client, "plain-standard", destination_id, priority=100)

    run_response = client.post(
        "/api/v1/maintenance/run",
        headers=OPERATOR_HEADERS,
        json={
            "destination_id": str(destination_id),
            "preset_id": preset_id,
            "mode": "standard",
            "table_ids": [str(hot_table_id)],
        },
    )
    assert run_response.status_code == 201
    job_id = run_response.json()["job_ids"][0]

    job_detail = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER_HEADERS).json()
    assert job_detail["destination_id"] == str(destination_id)


def test_cancel_releases_table_lock_and_is_idempotent(
    api_db: ApiDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel must free Redis table locks; re-cancel cleans leftovers from older cancels."""
    from cryo.api import routes
    from cryo.domain.enums import JobStatus
    from cryo.queue.locks import InMemoryLockStore
    from cryo.queue.redis_queue import InMemoryMaintenanceQueue

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
    run_id = run_response.json()["run_id"]
    job_id = run_response.json()["job_ids"][0]

    with api_db.session_factory() as session:
        job = session.get(MaintenanceJob, uuid.UUID(job_id))
        assert job is not None
        job.status = JobStatus.RUNNING.value
        job.started_at = datetime.now(UTC)
        session.commit()

    locks = InMemoryLockStore()
    queue = InMemoryMaintenanceQueue()
    queue.enqueue(job_id, str(destination_id))
    queue.admit(job_id, str(destination_id), replica="r1")
    queue.mark_running(job_id)
    complete_calls = 0
    original_complete = queue.complete

    def tracked_complete(job_id_arg: str, status_arg: JobStatus | str) -> None:
        nonlocal complete_calls
        complete_calls += 1
        original_complete(job_id_arg, status_arg)

    monkeypatch.setattr(queue, "complete", tracked_complete)
    assert locks.acquire_table_lock(str(hot_table_id), job_id, ttl_seconds=3600)

    class _Wiring:
        def __init__(self) -> None:
            self.queue = queue
            self.lock_store = locks

    monkeypatch.setattr(routes, "build_runtime_wiring", lambda: _Wiring())

    cancel_response = client.post(f"/api/v1/runs/{run_id}/cancel", headers=OPERATOR_HEADERS)
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"
    assert not locks.is_table_locked(str(hot_table_id))
    assert queue.local_in_flight_by_replica(str(destination_id)) == {}

    # Simulate a leftover lock from a pre-fix cancel, then re-cancel to reclaim it.
    assert locks.acquire_table_lock(str(hot_table_id), job_id, ttl_seconds=3600)
    re_cancel = client.post(f"/api/v1/runs/{run_id}/cancel", headers=OPERATOR_HEADERS)
    assert re_cancel.status_code == 200
    assert not locks.is_table_locked(str(hot_table_id))
    assert complete_calls == 1


def test_cancel_terminates_deferred_jobs_not_just_active_ones(
    api_db: ApiDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression, 2026-07-28: cancel_run's `cancellable` set only covered QUEUED/ADMITTED/
    RUNNING, so a job sitting DEFERRED (e.g. error="table_locked", waiting for another job's
    lock to clear) was left completely untouched by cancel -- the run flipped to "cancelled"
    but the job row stayed status="deferred". reconcile_deferred_jobs (cryo/queue/reconcile.py)
    has no notion of the parent run's status and re-evaluates every DEFERRED job on each
    governor tick, so that "cancelled" run could silently resurrect and dispatch for real once
    the lock it was waiting on freed up. Confirmed live: a Spark run stayed deferred/uncancelled
    for hours after the operator cancelled it."""
    from cryo.api import routes
    from cryo.domain.enums import JobStatus
    from cryo.queue.locks import InMemoryLockStore
    from cryo.queue.redis_queue import InMemoryMaintenanceQueue

    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    _seed_tables(api_db.session_factory, source_id)
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
    run_id = run_response.json()["run_id"]
    job_id = run_response.json()["job_ids"][0]

    with api_db.session_factory() as session:
        job = session.get(MaintenanceJob, uuid.UUID(job_id))
        assert job is not None
        job.status = JobStatus.DEFERRED.value
        job.error = "table_locked"
        session.commit()

    locks = InMemoryLockStore()
    queue = InMemoryMaintenanceQueue()

    class _Wiring:
        def __init__(self) -> None:
            self.queue = queue
            self.lock_store = locks

    monkeypatch.setattr(routes, "build_runtime_wiring", lambda: _Wiring())

    cancel_response = client.post(f"/api/v1/runs/{run_id}/cancel", headers=OPERATOR_HEADERS)
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"

    job_response = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER_HEADERS)
    assert job_response.json()["status"] == "skipped"
    assert job_response.json()["error"] == "cancelled"


def test_cancel_keeps_a_finished_runs_outcome_but_stops_leftover_jobs(
    api_db: ApiDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression, 2026-09-28: every nightly run ends "partial", which neither the API nor
    the UI treated as terminal, so Run detail offered Cancel and the endpoint overwrote the
    recorded outcome with "cancelled". A finished run keeps its status and finished_at;
    only jobs still waiting (here a DEFERRED one) are stopped."""
    from cryo.api import routes
    from cryo.domain.enums import JobStatus
    from cryo.queue.locks import InMemoryLockStore
    from cryo.queue.redis_queue import InMemoryMaintenanceQueue

    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    _seed_tables(api_db.session_factory, source_id)
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
    run_id = run_response.json()["run_id"]
    job_id = run_response.json()["job_ids"][0]
    finished_at = datetime(2026, 9, 28, 1, 30, tzinfo=UTC)

    with api_db.session_factory() as session:
        run = session.get(MaintenanceRun, uuid.UUID(run_id))
        job = session.get(MaintenanceJob, uuid.UUID(job_id))
        assert run is not None and job is not None
        run.status = "partial"
        run.finished_at = finished_at
        job.status = JobStatus.DEFERRED.value
        job.error = "table_locked"
        session.commit()

    class _Wiring:
        def __init__(self) -> None:
            self.queue = InMemoryMaintenanceQueue()
            self.lock_store = InMemoryLockStore()

    monkeypatch.setattr(routes, "build_runtime_wiring", lambda: _Wiring())

    cancel_response = client.post(f"/api/v1/runs/{run_id}/cancel", headers=OPERATOR_HEADERS)
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "partial"

    with api_db.session_factory() as session:
        run = session.get(MaintenanceRun, uuid.UUID(run_id))
        assert run is not None
        assert run.status == "partial"
        assert run.finished_at is not None
        assert run.finished_at.replace(tzinfo=None) == finished_at.replace(tzinfo=None)

    job_response = client.get(f"/api/v1/jobs/{job_id}", headers=VIEWER_HEADERS)
    assert job_response.json()["status"] == "skipped"
    assert job_response.json()["error"] == "cancelled"


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


def _patch_sqlite_incompatible_defaults() -> list[tuple[object, object]]:
    """Clears server_default on columns SQLite can't evaluate -- see the api_db fixture's own
    comment for the restore-on-teardown bug this used to have (fixed 2026-09-21). Returns the
    original (column, server_default) pairs so the caller can put them back."""
    originals: list[tuple[object, object]] = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if column.server_default is None:
                continue
            default = str(column.server_default.arg)
            if "::jsonb" in default or "gen_random_uuid" in default:
                originals.append((column, column.server_default))
                column.server_default = None
    return originals


def test_table_operation_run_allows_operations_outside_the_workflow(api_db: ApiDb) -> None:
    """The table page's "Expire snapshots" must work even when the workflow only compacts."""
    source_id, destination_id = _seed_source_destination(api_db.session_factory)
    hot_table_id, _ = _seed_tables(api_db.session_factory, source_id)
    client = api_db.client

    unassigned = client.post(
        f"/api/v1/tables/{hot_table_id}/maintenance/operations",
        headers=OPERATOR_HEADERS,
        json={"operations": ["remove_orphan_files"]},
    )
    assert unassigned.status_code == 409

    preset_id = _create_preset(client, "standard", destination_id, priority=100)
    assert (
        client.post(
            "/api/v1/assignments",
            headers=OPERATOR_HEADERS,
            json={
                "preset_id": preset_id,
                "selection": {"mode": "ids", "ids": [str(hot_table_id)]},
                "assigned_by": "pytest",
            },
        ).status_code
        == 200
    )

    viewer = client.post(
        f"/api/v1/tables/{hot_table_id}/maintenance/operations",
        headers=VIEWER_HEADERS,
        json={"operations": ["remove_orphan_files"]},
    )
    assert viewer.status_code == 403

    response = client.post(
        f"/api/v1/tables/{hot_table_id}/maintenance/operations",
        headers=OPERATOR_HEADERS,
        json={"operations": ["remove_orphan_files"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["job_ids"]) == 1
    job = client.get(f"/api/v1/jobs/{body['job_ids'][0]}", headers=VIEWER_HEADERS).json()
    assert job["table_id"] == str(hot_table_id)
    assert job["mode"] == "deep"
    run = client.get(f"/api/v1/runs/{body['run_id']}", headers=VIEWER_HEADERS).json()
    assert run["summary"]["operations"] == ["remove_orphan_files"]

    bulk = client.post(
        "/api/v1/maintenance/run",
        headers=OPERATOR_HEADERS,
        json={
            "destination_id": str(destination_id),
            "preset_id": preset_id,
            "table_ids": [str(hot_table_id)],
            "operations": ["remove_orphan_files"],
        },
    )
    assert bulk.status_code == 400


def test_mode_for_operations_picks_the_lightest_mode_that_plans_them() -> None:
    from cryo.api.routes_table_maintenance import mode_for_operations
    from cryo.domain.enums import Mode, OperationType

    assert mode_for_operations({OperationType.EXPIRE_SNAPSHOTS}) is Mode.LIGHT
    assert mode_for_operations({OperationType.OPTIMIZE}) is Mode.STANDARD
    assert mode_for_operations({OperationType.OPTIMIZE_MANIFESTS}) is Mode.MANIFEST_ONLY
    assert mode_for_operations({OperationType.REMOVE_ORPHAN_FILES}) is Mode.DEEP
    assert (
        mode_for_operations({OperationType.OPTIMIZE, OperationType.OPTIMIZE_MANIFESTS}) is Mode.DEEP
    )
