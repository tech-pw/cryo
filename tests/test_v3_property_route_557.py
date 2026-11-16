"""Property changes on Iceberg v3 tables go through an engine, not PyIceberg (#557), as a
queued job rather than inside the request (#560)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from cryo.advisor.path_to_a import TableSnapshot, build_path_to_a
from cryo.db.models import (
    AuditLog,
    Destination,
    MaintenanceJob,
    MaintenanceRun,
    Source,
    TableStats,
)
from cryo.engines.spark import sql as spark_sql
from cryo.engines.trino import sql as trino_sql
from cryo.queue.redis_queue import InMemoryMaintenanceQueue
from tests.auth_helpers import OPERATOR_HEADERS, VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb
from tests.test_table_config import _live, _seed

# --- renderers -------------------------------------------------------------------------


def test_spark_set_tblproperties_golden() -> None:
    sql = spark_sql.render_set_table_properties(
        "lake",
        "analytics",
        "events",
        {"write.target-file-size-bytes": "268435456", "write.distribution-mode": "hash"},
    )
    assert sql == (
        "ALTER TABLE `lake`.`analytics`.`events` SET TBLPROPERTIES ("
        "'write.distribution-mode' = 'hash', 'write.target-file-size-bytes' = '268435456')"
    )


def test_spark_unset_tblproperties_golden() -> None:
    sql = spark_sql.render_unset_table_properties(
        "lake", "analytics", "events", ["b.key", "a.key", "a.key"]
    )
    assert sql == (
        "ALTER TABLE `lake`.`analytics`.`events` UNSET TBLPROPERTIES IF EXISTS ('a.key', 'b.key')"
    )


def test_spark_renderer_escapes_quotes_backslashes_and_backticks() -> None:
    sql = spark_sql.render_set_table_properties(
        "la`ke", "s", "t", {"k'); DROP TABLE x; --": "v\\' OR '1'='1"}
    )
    assert sql == (
        "ALTER TABLE `la``ke`.`s`.`t` SET TBLPROPERTIES ("
        "'k\\'); DROP TABLE x; --' = 'v\\\\\\' OR \\'1\\'=\\'1')"
    )
    with pytest.raises(ValueError):
        spark_sql.render_set_table_properties("c", "s", "t", {"k": "a\x00b"})


def test_trino_set_properties_golden_and_limits() -> None:
    sql = trino_sql.render_set_table_properties(
        "iceberg", "analytics", 'ev"ents', {"commit.retry.num-retries": "10"}
    )
    assert (
        sql == 'ALTER TABLE "iceberg"."analytics"."ev""ents" SET PROPERTIES max_commit_retry = 10'
    )
    with pytest.raises(ValueError):
        trino_sql.render_set_table_properties("c", "s", "t", {"write.distribution-mode": "hash"})
    with pytest.raises(ValueError):
        trino_sql.render_set_table_properties(
            "c", "s", "t", {"commit.retry.num-retries": "1; DROP TABLE x"}
        )
    assert trino_sql.trino_supports_table_properties(["commit.retry.num-retries"])
    assert not trino_sql.trino_supports_table_properties(["write.distribution-mode"])


# --- apply routing ---------------------------------------------------------------------


def _make_v3(api_db: ApiDb, table_id: uuid.UUID, *, version: int = 3) -> None:
    session = api_db.session_factory()
    stats = session.get(TableStats, table_id)
    stats.raw = {**(stats.raw or {}), "format_version": version}
    from cryo.db.models import Table

    source = session.get(Source, session.get(Table, table_id).source_id)
    source.config = {**source.config, "catalog_name": "lake"}
    session.commit()
    session.close()


class _Wiring:
    def __init__(self) -> None:
        self.queue = InMemoryMaintenanceQueue()
        self.run_events = None


@pytest.fixture()
def wiring() -> Iterator[_Wiring]:
    fake = _Wiring()
    with patch("cryo.infra.wiring.build_runtime_wiring", return_value=fake):
        yield fake


def test_v3_apply_queues_a_job_on_spark_and_returns_202(api_db: ApiDb, wiring: _Wiring) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, engine="spark_emr")
    session.close()
    _make_v3(api_db, table_id)
    with (
        patch("cryo.engines.factory.create_engine") as factory,
        patch("cryo.sources.pyiceberg_catalog.PyIcebergSourceCatalog") as catalog_cls,
    ):
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/properties/apply",
            headers=OPERATOR_HEADERS,
            json={"properties": {"write.distribution-mode": "hash"}},
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["route"] == "engine" and body["engine"] == "spark_emr"
    catalog_cls.assert_not_called()
    factory.assert_not_called()  # nothing runs inside the request
    session = api_db.session_factory()
    job = session.get(MaintenanceJob, uuid.UUID(body["job_id"]))
    assert job is not None and job.table_id == table_id
    assert job.operations == ["set_table_properties"]
    assert job.dedupe_key == "set_table_properties" and job.force_admit is True
    destination = session.get(Destination, job.destination_id)
    assert destination.engine == "spark_emr"
    run = session.get(MaintenanceRun, uuid.UUID(body["run_id"]))
    assert run.summary["operations"] == ["set_table_properties"]
    assert run.summary["table_properties"]["set"] == {"write.distribution-mode": "hash"}
    assert wiring.queue.status(str(job.id)) is not None
    audit = session.get(AuditLog, body["audit_log_id"])
    assert audit.status == "QUEUED" and audit.engine == "spark_emr"
    assert f"job_id={job.id}" in audit.detail
    assert audit.result_metrics["job_id"] == str(job.id)
    session.close()


def test_job_record_carries_the_change_to_the_planner(api_db: ApiDb, wiring: _Wiring) -> None:
    from cryo.db.repositories.job_store import SQLJobStore

    session = api_db.session_factory()
    table_id = _seed(session, engine="spark_emr")
    session.close()
    _make_v3(api_db, table_id)
    body = api_db.client.post(
        f"/api/v1/tables/{table_id}/properties/apply",
        headers=OPERATOR_HEADERS,
        json={"properties": {"write.distribution-mode": "hash"}},
    ).json()
    record = SQLJobStore(api_db.session_factory).get_job(body["job_id"])
    assert record.params["table_properties"]["set"] == {"write.distribution-mode": "hash"}
    assert record.params["requested_operations"] == ["set_table_properties"]
    assert record.force_admit is True


def test_second_apply_while_one_is_queued_is_409(api_db: ApiDb, wiring: _Wiring) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, engine="spark_emr")
    session.close()
    _make_v3(api_db, table_id)
    payload = {"properties": {"write.distribution-mode": "hash"}}
    url = f"/api/v1/tables/{table_id}/properties/apply"
    first = api_db.client.post(url, headers=OPERATOR_HEADERS, json=payload)
    second = api_db.client.post(url, headers=OPERATOR_HEADERS, json=payload)
    assert first.status_code == 202
    assert second.status_code == 409
    assert "already queued or running" in second.json()["detail"]


def test_v2_apply_keeps_pyiceberg(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, engine="spark_emr")
    session.close()
    _make_v3(api_db, table_id, version=2)
    with (
        patch("cryo.engines.factory.create_engine") as factory,
        patch("cryo.sources.pyiceberg_catalog.PyIcebergSourceCatalog") as catalog_cls,
    ):
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/properties/apply",
            headers=OPERATOR_HEADERS,
            json={"properties": {"write.distribution-mode": "hash"}},
        )
    assert response.status_code == 200, response.text
    assert response.json()["route"] == "pyiceberg"
    factory.assert_not_called()
    catalog_cls.return_value.load_table.assert_called_once()


def test_v3_without_capable_destination_is_409(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, engine="native")
    session.close()
    _make_v3(api_db, table_id)
    with patch("cryo.engines.factory.create_engine") as factory:
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/properties/apply",
            headers=OPERATOR_HEADERS,
            json={"properties": {"write.distribution-mode": "hash"}},
        )
    assert response.status_code == 409
    assert "Spark destination" in response.json()["detail"]
    factory.assert_not_called()
    recs = api_db.client.get(
        f"/api/v1/tables/{table_id}/property-recommendations", headers=VIEWER_HEADERS
    ).json()
    assert recs["format_version"] == 3
    assert recs["property_route"]["route"] == "none"


def test_v3_trino_route_only_for_supported_keys(api_db: ApiDb, wiring: _Wiring) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, engine="trino")
    session.close()
    _make_v3(api_db, table_id)
    ok = api_db.client.post(
        f"/api/v1/tables/{table_id}/properties/apply",
        headers=OPERATOR_HEADERS,
        json={"properties": {"commit.retry.num-retries": "10"}},
    )
    assert ok.status_code == 202, ok.text
    assert ok.json()["engine"] == "trino"
    refused = api_db.client.post(
        f"/api/v1/tables/{table_id}/properties/apply",
        headers=OPERATOR_HEADERS,
        json={"properties": {"write.distribution-mode": "hash"}},
    )
    assert refused.status_code == 409


def test_pyiceberg_v3_setting_switches_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryo.config import get_settings
    from cryo.table_config.engine_route import pyiceberg_can_commit

    assert pyiceberg_can_commit(2) and pyiceberg_can_commit(None)
    assert not pyiceberg_can_commit(3)
    assert not pyiceberg_can_commit(2, 3)
    monkeypatch.setattr(get_settings(), "pyiceberg_v3_writes", True)
    assert pyiceberg_can_commit(3)


# --- Config tab ------------------------------------------------------------------------


def test_config_apply_on_v3_queues_a_job_and_audits(api_db: ApiDb, wiring: _Wiring) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, engine="spark_emr")
    session.close()
    _make_v3(api_db, table_id)
    live = _live(format_version=3)
    with (
        patch("cryo.table_config.live.read_live_config", return_value=live),
        patch("cryo.table_config.live.load_iceberg_table") as load,
        patch("cryo.engines.factory.create_engine") as factory,
    ):
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/config/apply",
            headers=OPERATOR_HEADERS,
            json={"changes": [{"key": "write.distribution-mode", "value": "hash"}]},
        )
    assert response.status_code == 202, response.text
    load.assert_not_called()
    factory.assert_not_called()
    body = response.json()
    assert body["status"] == "queued" and body["job_id"]
    config = body["config"]
    assert config["format_version"] == 3
    assert config["property_route"]["route"] == "engine"
    session = api_db.session_factory()
    job = session.get(MaintenanceJob, uuid.UUID(body["job_id"]))
    assert job.operations == ["set_table_properties"]
    run = session.get(MaintenanceRun, job.run_id)
    assert run.summary["table_properties"]["set"] == {"write.distribution-mode": "hash"}
    assert run.summary["table_properties"]["origin"] == "config"
    audit = session.query(AuditLog).filter(AuditLog.table_id == table_id).one()
    assert audit.status == "QUEUED" and audit.action == "table_config_change"
    assert audit.engine == "spark_emr" and "route=engine" in audit.detail
    assert f"job_id={job.id}" in audit.detail
    session.close()


def test_config_on_v3_without_route_locks_property_settings(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, engine="native")
    session.query(Destination).delete()
    session.commit()
    session.close()
    _make_v3(api_db, table_id)
    with patch("cryo.table_config.live.read_live_config", return_value=_live(format_version=3)):
        body = api_db.client.get(
            f"/api/v1/tables/{table_id}/config?refresh=true", headers=VIEWER_HEADERS
        ).json()
    assert body["property_route"]["route"] == "none"
    target = next(s for s in body["settings"] if s["key"] == "write.distribution-mode")
    assert target["editable"] is False
    assert "Spark destination" in target["read_only_reason"]


# --- Path to A -------------------------------------------------------------------------


def test_path_to_a_hides_the_apply_action_without_a_route() -> None:
    from tests.test_path_to_a import _over_partitioned

    base = _over_partitioned()
    changes = {"write.target-file-size-bytes": "536870912"}
    open_snapshot = TableSnapshot(**{**base.__dict__, "property_changes": changes})
    blocked_snapshot = TableSnapshot(
        **{**base.__dict__, "property_changes": changes, "property_apply_blocked": "needs Spark"}
    )
    blocked = next(s for s in build_path_to_a(blocked_snapshot).steps if s.id == "table_properties")
    opened = next(s for s in build_path_to_a(open_snapshot).steps if s.id == "table_properties")
    assert opened.action is not None
    assert blocked.action is None and blocked.kind == "external"
    assert blocked.fixes == []
    assert "needs Spark" in blocked.detail
