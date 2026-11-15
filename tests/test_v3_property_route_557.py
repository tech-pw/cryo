"""Property changes on Iceberg v3 tables go through an engine, not PyIceberg (#557)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from cryo.advisor.path_to_a import TableSnapshot, build_path_to_a
from cryo.db.models import AuditLog, Destination, Source, TableStats
from cryo.engines.base import ExecResult
from cryo.engines.spark import sql as spark_sql
from cryo.engines.trino import sql as trino_sql
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


def _engine_ok() -> MagicMock:
    engine = MagicMock()
    engine.execute.return_value = ExecResult(ok=True)
    return engine


def test_v3_apply_routes_through_spark_and_audits_route(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, engine="spark_emr")
    session.close()
    _make_v3(api_db, table_id)
    engine = _engine_ok()
    with (
        patch("cryo.engines.factory.create_engine", return_value=engine) as factory,
        patch("cryo.sources.pyiceberg_catalog.PyIcebergSourceCatalog") as catalog_cls,
    ):
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/properties/apply",
            headers=OPERATOR_HEADERS,
            json={"properties": {"write.distribution-mode": "hash"}},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["route"] == "engine" and body["engine"] == "spark_emr"
    catalog_cls.assert_not_called()
    assert factory.call_args.args[0].value == "spark_emr"
    stmt = engine.execute.call_args.args[0]
    assert stmt.sql == (
        "ALTER TABLE `lake`.`analytics`.`events` SET TBLPROPERTIES "
        "('write.distribution-mode' = 'hash')"
    )
    session = api_db.session_factory()
    audit = session.get(AuditLog, body["audit_log_id"])
    assert audit.engine == "spark_emr"
    assert "route=engine" in audit.detail
    assert audit.result_metrics["route"] == "engine"
    session.close()


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


def test_v3_trino_route_only_for_supported_keys(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, engine="trino")
    session.close()
    _make_v3(api_db, table_id)
    engine = _engine_ok()
    with patch("cryo.engines.factory.create_engine", return_value=engine):
        ok = api_db.client.post(
            f"/api/v1/tables/{table_id}/properties/apply",
            headers=OPERATOR_HEADERS,
            json={"properties": {"commit.retry.num-retries": "10"}},
        )
        refused = api_db.client.post(
            f"/api/v1/tables/{table_id}/properties/apply",
            headers=OPERATOR_HEADERS,
            json={"properties": {"write.distribution-mode": "hash"}},
        )
    assert ok.status_code == 200, ok.text
    assert ok.json()["engine"] == "trino"
    assert "SET PROPERTIES max_commit_retry = 10" in engine.execute.call_args.args[0].sql
    assert refused.status_code == 409


def test_v3_engine_failure_is_audited(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, engine="spark_emr")
    session.close()
    _make_v3(api_db, table_id)
    engine = MagicMock()
    engine.execute.return_value = ExecResult(ok=False, error="boom")
    with patch("cryo.engines.factory.create_engine", return_value=engine):
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/properties/apply",
            headers=OPERATOR_HEADERS,
            json={"properties": {"write.distribution-mode": "hash"}},
        )
    assert response.status_code == 502
    session = api_db.session_factory()
    audit = session.query(AuditLog).filter(AuditLog.table_id == table_id).one()
    assert audit.status == "FAILED" and audit.engine == "spark_emr"
    session.close()


def test_pyiceberg_v3_setting_switches_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryo.config import get_settings
    from cryo.table_config.engine_route import pyiceberg_can_commit

    assert pyiceberg_can_commit(2) and pyiceberg_can_commit(None)
    assert not pyiceberg_can_commit(3)
    assert not pyiceberg_can_commit(2, 3)
    monkeypatch.setattr(get_settings(), "pyiceberg_v3_writes", True)
    assert pyiceberg_can_commit(3)


# --- Config tab ------------------------------------------------------------------------


def test_config_apply_on_v3_uses_engine_and_audits(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed(session, engine="spark_emr")
    session.close()
    _make_v3(api_db, table_id)
    engine = _engine_ok()
    live = _live(format_version=3)
    with (
        patch("cryo.table_config.live.read_live_config", return_value=live),
        patch("cryo.table_config.live.load_iceberg_table") as load,
        patch("cryo.engines.factory.create_engine", return_value=engine),
    ):
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/config/apply",
            headers=OPERATOR_HEADERS,
            json={"changes": [{"key": "write.distribution-mode", "value": "hash"}]},
        )
    assert response.status_code == 200, response.text
    load.assert_not_called()
    assert "SET TBLPROPERTIES" in engine.execute.call_args.args[0].sql
    config = response.json()["config"]
    assert config["format_version"] == 3
    assert config["property_route"]["route"] == "engine"
    session = api_db.session_factory()
    audit = session.query(AuditLog).filter(AuditLog.table_id == table_id).one()
    assert audit.engine == "spark_emr" and "route=engine" in audit.detail
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
