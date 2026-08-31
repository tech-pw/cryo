"""Shared S3 warehouse-path anomaly detector, harvest, Slack, API, and cron."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from cryo.anomalies.harvest import harvest_all_sources
from cryo.anomalies.paths import LocationRow, detect_shared_s3_groups, normalize_warehouse_path
from cryo.anomalies.report import (
    SHARED_S3_REPORT_CRON,
    SHARED_S3_REPORT_TZ,
    latest_shared_s3_report,
    notify_shared_s3_report,
    refresh_shared_s3_report,
    report_to_payload,
)
from cryo.anomalies.slack import build_shared_s3_slack_blocks
from cryo.db.models import SlackConfig, Source, Table
from cryo.scheduler.cron import cron_is_due
from cryo.security.crypto import encrypt_secret
from cryo.sources.locations import (
    CatalogTableLocation,
    list_glue_table_locations,
    list_hive_table_locations,
)
from tests.auth_helpers import OPERATOR_HEADERS, VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb


def _hive_locations(monkeypatch: object, locations: list[CatalogTableLocation]) -> None:
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "cryo.anomalies.harvest.list_hive_table_locations_from_uri",
        lambda _uri: locations,
    )


def _row(
    *,
    location: str,
    table: str = "orders",
    source: str = "hive",
    source_id: uuid.UUID | None = None,
    schema: str = "demo",
) -> LocationRow:
    return LocationRow(
        source_id=source_id or uuid4(),
        source_name=source,
        source_type=source,
        schema_name=schema,
        table_name=table,
        location=location,
    )


def test_normalize_s3a_trailing_slash_and_non_s3() -> None:
    assert normalize_warehouse_path("s3a://Warehouse/demo/orders/") == "s3://warehouse/demo/orders"
    assert normalize_warehouse_path("s3://bucket/foo//bar/") == "s3://bucket/foo/bar"
    assert normalize_warehouse_path("file:///tmp/table") is None
    assert normalize_warehouse_path("") is None


def test_exact_match_groups_aliases() -> None:
    detection = detect_shared_s3_groups(
        [
            _row(location="s3://wh/demo/orders", table="orders"),
            _row(location="s3a://wh/demo/orders/", table="orders_evolving"),
        ]
    )
    assert len(detection.groups) == 1
    group = detection.groups[0]
    assert group.kind == "exact"
    assert group.shared_path == "s3://wh/demo/orders"
    assert group.table_count == 2


def test_nested_prefix_groups_subdirectory() -> None:
    detection = detect_shared_s3_groups(
        [
            _row(location="s3://wh/demo/orders", table="orders"),
            _row(location="s3://wh/demo/orders/archive", table="orders_nested"),
        ]
    )
    assert len(detection.groups) == 1
    assert detection.groups[0].kind == "nested"
    assert detection.groups[0].shared_path == "s3://wh/demo/orders"


def test_path_boundary_does_not_match_sibling_prefix() -> None:
    detection = detect_shared_s3_groups(
        [
            _row(location="s3://wh/demo/orders", table="orders"),
            _row(location="s3://wh/demo/orders_v2", table="orders_v2"),
        ]
    )
    assert detection.groups == ()


def test_cross_source_group() -> None:
    hive_id, glue_id = uuid4(), uuid4()
    detection = detect_shared_s3_groups(
        [
            _row(location="s3://wh/demo/orders", source="local-hms", source_id=hive_id),
            _row(
                location="s3://wh/demo/orders",
                table="orders_copy",
                source="glue",
                source_id=glue_id,
            ),
        ]
    )
    assert len(detection.groups) == 1
    names = {member.source_name for member in detection.groups[0].tables}
    assert names == {"local-hms", "glue"}


def test_isolated_tables_are_not_groups() -> None:
    detection = detect_shared_s3_groups(
        [
            _row(location="s3://wh/demo/a", table="a"),
            _row(location="s3://wh/demo/b", table="b"),
        ]
    )
    assert detection.usable_count == 2
    assert detection.groups == ()


def test_parse_hms_thrift_uri() -> None:
    from cryo.sources.hms_thrift import parse_hms_thrift_uri

    assert parse_hms_thrift_uri("thrift://localhost:9083") == ("localhost", 9083)
    assert parse_hms_thrift_uri("thrift://hms.example") == ("hms.example", 9083)
    assert parse_hms_thrift_uri("hive-metastore:9083") == ("hive-metastore", 9083)


def test_open_hms_thrift_client_uses_raw_thrift(monkeypatch) -> None:
    from cryo.sources import hms_thrift

    created: dict[str, object] = {}

    class FakeSocket:
        def __init__(self, host: str, port: int) -> None:
            created["host"] = host
            created["port"] = port
            created["timeouts"] = []

        def setTimeout(self, ms: int | None) -> None:
            created["timeouts"].append(ms)

    class FakeTransport:
        def __init__(self, sock: object) -> None:
            created["socket"] = sock

        def open(self) -> None:
            created["opened"] = True

        def close(self) -> None:
            created["closed"] = True

    class FakeProtocol:
        def __init__(self, transport: object) -> None:
            created["protocol"] = True

    class FakeClient:
        def __init__(self, protocol: object) -> None:
            created["client"] = True

    monkeypatch.setattr(hms_thrift.TSocket, "TSocket", FakeSocket)
    monkeypatch.setattr(hms_thrift.TTransport, "TBufferedTransport", FakeTransport)
    monkeypatch.setattr(hms_thrift.TBinaryProtocol, "TBinaryProtocol", FakeProtocol)
    monkeypatch.setattr(hms_thrift.ThriftHiveMetastore, "Client", FakeClient)

    with hms_thrift.open_hms_thrift_client("thrift://hms.example:9083") as client:
        assert isinstance(client, FakeClient)
        assert created["host"] == "hms.example"
        assert created["port"] == 9083
        assert created["opened"] is True
        assert created["timeouts"] == [
            hms_thrift.HMS_CONNECT_TIMEOUT_MS,
            hms_thrift.HMS_RPC_TIMEOUT_MS,
        ]
    assert created["closed"] is True


def test_hive_source_catalog_uses_thrift_uri_not_pyiceberg(monkeypatch) -> None:
    from cryo.sources.hive import HiveSourceCatalog

    catalog = HiveSourceCatalog.__new__(HiveSourceCatalog)
    catalog.name = "local-hms"
    catalog.config = {"uri": "thrift://localhost:9083"}
    opened: list[str] = []

    def fake_list(uri: str) -> list[CatalogTableLocation]:
        opened.append(uri)
        return [CatalogTableLocation("demo", "orders_evolving", "s3://wh/demo/orders")]

    monkeypatch.setattr("cryo.sources.hive.list_hive_table_locations_from_uri", fake_list)
    locations = catalog.list_table_locations()
    assert opened == ["thrift://localhost:9083"]
    assert [(loc.table_name, loc.location) for loc in locations] == [
        ("orders_evolving", "s3://wh/demo/orders")
    ]


def test_harvest_hive_skips_pyiceberg_catalog(session, monkeypatch) -> None:
    source = Source(
        id=uuid4(),
        name="local-hms",
        type="hive",
        config={"uri": "thrift://localhost:9083"},
        enabled=True,
    )
    session.add(source)
    session.flush()

    monkeypatch.setattr(
        "cryo.anomalies.harvest.list_hive_table_locations_from_uri",
        lambda uri: [
            CatalogTableLocation("demo", "orders", "s3://wh/demo/orders"),
            CatalogTableLocation("demo", "orders_evolving", "s3://wh/demo/orders"),
        ],
    )

    def _boom(_source: Source) -> object:
        raise RuntimeError("pyiceberg catalog must not be constructed for Hive harvest")

    monkeypatch.setattr("cryo.anomalies.harvest.source_catalog_for", _boom)
    harvest = harvest_all_sources(session)
    assert harvest.sources[0].mode == "live"
    assert harvest.sources[0].error is None
    detection = detect_shared_s3_groups(harvest.location_rows())
    assert len(detection.groups) == 1


def test_hive_lists_sd_location_without_load_table() -> None:
    class Sd:
        def __init__(self, location: str) -> None:
            self.location = location

    class HiveTable:
        def __init__(self, name: str, location: str) -> None:
            self.tableName = name
            self.dbName = "demo"
            self.sd = Sd(location)

    client = SimpleNamespace(
        get_all_databases=lambda: ["demo"],
        get_all_tables=lambda db: ["orders", "orders_evolving"],
        get_table_objects_by_name=lambda db, names: [
            HiveTable("orders", "s3a://wh/demo/orders"),
            HiveTable("orders_evolving", "s3a://wh/demo/orders"),
        ],
    )
    locations = list_hive_table_locations(client)
    assert {(loc.table_name, loc.location) for loc in locations} == {
        ("orders", "s3a://wh/demo/orders"),
        ("orders_evolving", "s3a://wh/demo/orders"),
    }


def test_glue_lists_storage_descriptor_location() -> None:
    class Paginator:
        def __init__(self, pages: list[dict]) -> None:
            self._pages = pages

        def paginate(self, **kwargs: object) -> list[dict]:
            return self._pages

    class Glue:
        def get_paginator(self, name: str) -> Paginator:
            if name == "get_databases":
                return Paginator([{"DatabaseList": [{"Name": "demo"}]}])
            return Paginator(
                [
                    {
                        "TableList": [
                            {
                                "Name": "orders",
                                "DatabaseName": "demo",
                                "StorageDescriptor": {"Location": "s3://wh/demo/orders"},
                            }
                        ]
                    }
                ]
            )

    locations = list_glue_table_locations(Glue())
    assert locations == [
        CatalogTableLocation(
            schema_name="demo",
            table_name="orders",
            location="s3://wh/demo/orders",
        )
    ]


def test_harvest_db_fallback_when_catalog_fails(session, monkeypatch) -> None:
    source = Source(
        id=uuid4(),
        name="local-hms",
        type="hive",
        config={"uri": "thrift://localhost:9083"},
        enabled=True,
    )
    session.add(source)
    session.flush()
    session.add_all(
        [
            Table(
                id=uuid4(),
                source_id=source.id,
                schema_name="demo",
                table_name="orders",
                location="s3://wh/demo/orders",
                is_active=True,
            ),
            Table(
                id=uuid4(),
                source_id=source.id,
                schema_name="demo",
                table_name="orders_evolving",
                location="s3://wh/demo/orders",
                is_active=True,
            ),
        ]
    )
    session.flush()

    def _boom(_uri: str) -> object:
        raise RuntimeError("thrift down")

    monkeypatch.setattr("cryo.anomalies.harvest.list_hive_table_locations_from_uri", _boom)
    harvest = harvest_all_sources(session)
    assert harvest.sources[0].mode == "db_fallback"
    assert harvest.sources[0].error is not None
    detection = detect_shared_s3_groups(harvest.location_rows())
    assert len(detection.groups) == 1


def test_refresh_persists_snapshot(session, monkeypatch) -> None:
    source = Source(id=uuid4(), name="local-rest", type="rest", config={}, enabled=True)
    session.add(source)
    session.flush()

    class FakeCatalog:
        def list_table_locations(self) -> list[CatalogTableLocation]:
            return [
                CatalogTableLocation("demo", "orders", "s3://wh/demo/orders"),
                CatalogTableLocation("demo", "orders_evolving", "s3://wh/demo/orders"),
            ]

    monkeypatch.setattr("cryo.anomalies.harvest.source_catalog_for", lambda _source: FakeCatalog())
    report = refresh_shared_s3_report(session)
    assert report.group_count == 1
    assert latest_shared_s3_report(session) is not None
    payload = report_to_payload(report)
    assert payload["overlapping_group_count"] == 1
    assert payload["groups"][0]["kind"] == "exact"


def test_notify_skips_slack_when_no_groups(session, monkeypatch) -> None:
    source = Source(id=uuid4(), name="local-rest", type="rest", config={}, enabled=True)
    session.add(source)
    session.flush()

    class FakeCatalog:
        def list_table_locations(self) -> list[CatalogTableLocation]:
            return [CatalogTableLocation("demo", "orders", "s3://wh/demo/orders")]

    monkeypatch.setattr("cryo.anomalies.harvest.source_catalog_for", lambda _source: FakeCatalog())
    snapshot = refresh_shared_s3_report(session)
    sent = MagicMock()
    monkeypatch.setattr("cryo.anomalies.report.SlackClient", sent)
    result = asyncio.run(notify_shared_s3_report(session, snapshot))
    assert result["slack_skipped_reason"] == "no_anomalies"
    sent.assert_not_called()


def test_notify_sends_when_groups_exist(session, monkeypatch) -> None:
    source = Source(
        id=uuid4(),
        name="local-hms",
        type="hive",
        config={"uri": "thrift://localhost:9083"},
        enabled=True,
    )
    session.add(source)
    session.add(
        SlackConfig(
            id=uuid4(),
            name="alerts",
            webhook_url_encrypted=encrypt_secret("https://hooks.slack.test/x"),
            channel="#data",
            enabled=True,
        )
    )
    session.flush()
    _hive_locations(
        monkeypatch,
        [
            CatalogTableLocation("demo", "orders", "s3://wh/demo/orders"),
            CatalogTableLocation("demo", "orders_evolving", "s3://wh/demo/orders"),
        ],
    )

    class FakeSlack:
        def __init__(self, webhook_url: str, channel: str | None = None) -> None:
            self.webhook_url = webhook_url
            self.channel = channel

        async def send_blocks(self, blocks: object, fallback_text: str = "") -> bool:
            assert blocks
            assert "shared s3" in fallback_text.lower()
            return True

    monkeypatch.setattr("cryo.anomalies.report.SlackClient", FakeSlack)
    snapshot = refresh_shared_s3_report(session)
    result = asyncio.run(notify_shared_s3_report(session, snapshot))
    assert result["slack_sent"] is True
    assert result["slack_targets"] == 1


def test_slack_blocks_include_groups() -> None:
    detection = detect_shared_s3_groups(
        [
            _row(location="s3://wh/demo/orders", table="orders"),
            _row(location="s3://wh/demo/orders", table="orders_evolving"),
        ]
    )
    blocks = build_shared_s3_slack_blocks(
        detection, generated_at=datetime(2026, 8, 18, tzinfo=UTC), source_count=1
    )
    kinds = {block.type for block in blocks}
    assert "header" in kinds
    assert "table" in kinds


def test_cron_10am_ist_not_10am_utc() -> None:
    """10:00 IST (04:30 UTC) is the real scheduled fire time; 10:00 UTC (15:30 IST) is a
    different clock time entirely and must not accidentally also match a timezone bug.

    cron_is_due now scans backward for a due-but-unrun fire time within its lookback window
    (see its own docstring for why -- a tick landing even slightly late must not silently skip
    a whole day's cron), so simply calling it again later the same day with no last_run_at would
    still correctly report the still-outstanding 04:30 UTC fire as due -- that's the intended
    fix, not a leak of this test's real assertion. Passing last_run_at=ist_tick (this cron
    already ran for today) isolates what this test actually checks: whether 10:00 UTC clock time
    itself is mistaken for a second, distinct fire -- it is not, since only one fire (04:30 UTC)
    exists per day for this expression.
    """
    ist_tick = datetime(2026, 8, 18, 4, 30, tzinfo=UTC)
    utc_ten = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    assert cron_is_due(SHARED_S3_REPORT_CRON, now=ist_tick, tz=SHARED_S3_REPORT_TZ)
    assert not cron_is_due(
        SHARED_S3_REPORT_CRON, now=utc_ten, last_run_at=ist_tick, tz=SHARED_S3_REPORT_TZ
    )


def test_shared_s3_api_refresh_and_get(api_db: ApiDb, monkeypatch) -> None:
    session = api_db.session_factory()
    source = Source(
        id=uuid4(),
        name="local-hms",
        type="hive",
        config={"uri": "thrift://localhost:9083"},
        enabled=True,
    )
    session.add(source)
    session.commit()
    session.close()
    _hive_locations(
        monkeypatch,
        [
            CatalogTableLocation("demo", "orders", "s3://wh/demo/orders"),
            CatalogTableLocation("demo", "orders_evolving", "s3://wh/demo/orders"),
        ],
    )

    empty = api_db.client.get("/api/v1/anomalies/shared-s3", headers=VIEWER_HEADERS)
    assert empty.status_code == 200
    assert empty.json()["generated_at"] is None

    forbidden = api_db.client.post("/api/v1/anomalies/shared-s3/refresh", headers=VIEWER_HEADERS)
    assert forbidden.status_code == 403

    refreshed = api_db.client.post("/api/v1/anomalies/shared-s3/refresh", headers=OPERATOR_HEADERS)
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["overlapping_group_count"] == 1
    assert len(body["groups"][0]["tables"]) == 2

    latest = api_db.client.get("/api/v1/anomalies/shared-s3", headers=VIEWER_HEADERS)
    assert latest.json()["id"] == body["id"]


def test_notify_api_skips_without_slack(api_db: ApiDb, monkeypatch) -> None:
    session = api_db.session_factory()
    session.add(Source(id=uuid4(), name="local-rest", type="rest", config={}, enabled=True))
    session.commit()
    session.close()

    class FakeCatalog:
        def list_table_locations(self) -> list[CatalogTableLocation]:
            return [
                CatalogTableLocation("demo", "a", "s3://wh/a"),
                CatalogTableLocation("demo", "b", "s3://wh/a"),
            ]

    monkeypatch.setattr("cryo.anomalies.harvest.source_catalog_for", lambda _source: FakeCatalog())
    response = api_db.client.post("/api/v1/anomalies/shared-s3/notify", headers=OPERATOR_HEADERS)
    assert response.status_code == 200
    assert response.json()["slack_skipped_reason"] in {"no_slack_config", "send_failed"}
    assert response.json()["overlapping_group_count"] == 1
