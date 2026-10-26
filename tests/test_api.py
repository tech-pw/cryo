from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cryo.api.app import create_app
from cryo.config import Settings
from cryo.sources.locations import CatalogTableLocation
from tests.auth_helpers import OPERATOR_HEADERS as AUTH_HEADERS


def _client() -> TestClient:
    app = create_app(Settings(_env_file=None, environment="test"))
    return TestClient(app)


def test_healthz() -> None:
    response = _client().get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"
    assert body["version"]


def test_preview_renders_trino_partition_filtered_sql() -> None:
    response = _client().post(
        "/api/v1/maintenance/preview",
        headers=AUTH_HEADERS,
        json={
            "engine": "trino",
            "mode": "standard",
            "params": {
                "min_small_files": 10,
                "chunk_size_gb": 10,
                "file_size_mb": 256,
                "partition_fields": [
                    {
                        "base_column": "event_date",
                        "partition_field": "event_date_day",
                        "transform": "day",
                        "col_type": "date",
                    },
                    {
                        "base_column": "region",
                        "partition_field": "region",
                        "transform": "identity",
                        "col_type": "string",
                    },
                ],
            },
            "tables": [
                {
                    "catalog": "iceberg",
                    "schema_name": "fixtures",
                    "table_name": "fixture_multi_column",
                    "stats": {"small_files_count": 100, "total_size_gb": 50},
                    "partitions": [
                        {
                            "partition_values": {
                                "event_date_day": "2025-01-01",
                                "region": "us",
                            },
                            "small_files_in_partition_count": 50,
                            "partition_size_gb": 5,
                        }
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["table_ref"] == "iceberg.fixtures.fixture_multi_column"
    optimize_sql = [
        statement["sql"]
        for statement in body["items"][0]["statements"]
        if statement["action"] == "optimize"
    ]
    assert optimize_sql == [
        "ALTER TABLE iceberg.fixtures.fixture_multi_column "
        "EXECUTE optimize(file_size_threshold => '256MB') "
        "WHERE (event_date >= DATE '2025-01-01' AND event_date < DATE '2025-01-02' "
        "AND region = 'us')"
    ]


def test_preview_skips_bucket_filter_without_unsafe_sql() -> None:
    response = _client().post(
        "/api/v1/maintenance/preview",
        headers=AUTH_HEADERS,
        json={
            "engine": "spark_emr",
            "mode": "standard",
            "params": {
                "min_small_files": 10,
                "chunk_size_gb": 10,
                "partition_fields": [
                    {
                        "base_column": "user_id",
                        "partition_field": "user_id_bucket",
                        "transform": "bucket[8]",
                        "col_type": "int",
                    }
                ],
            },
            "tables": [
                {
                    "schema_name": "fixtures",
                    "table_name": "fixture_bucket",
                    "stats": {"small_files_count": 100, "total_size_gb": 50},
                    "partitions": [
                        {
                            "partition_value": 3,
                            "small_files_in_partition_count": 50,
                            "partition_size_gb": 5,
                        }
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert len(item["statements"]) == 1
    assert item["statements"][0]["action"] == "expire_snapshots"
    assert any("bucket_filter" in note for note in item["notes"])


def test_preview_renders_native_payload_statements(monkeypatch) -> None:
    monkeypatch.setenv("CRYO_ENGINE_IMPL", "go")
    response = _client().post(
        "/api/v1/maintenance/preview",
        headers=AUTH_HEADERS,
        json={
            "engine": "native",
            "mode": "deep",
            "params": {
                "min_small_files": 10,
                "chunk_size_gb": 10,
            },
            "tables": [
                {
                    "schema_name": "fixtures",
                    "table_name": "fixture_small",
                    "stats": {"small_files_count": 100, "total_size_gb": 5},
                    "partitions": [],
                }
            ],
        },
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["table_ref"] == "iceberg.fixtures.fixture_small"
    batch = next(stmt for stmt in item["statements"] if stmt["sql"].startswith("NATIVE:BATCH:"))
    assert batch["engine"] == "native"
    assert batch["payload"]["table_ref"] == "iceberg.fixtures.fixture_small"
    assert batch["payload"]["mode"] == "deep"
    optimize = next(op for op in batch["payload"]["operations"] if op["type"] == "optimize")
    assert optimize["params"]["min_small_files"] == 10


def test_source_test_endpoint_reports_namespaces_without_listing_tables() -> None:
    """test_source must call list_namespaces() only -- list_tables() is O(namespaces)
    round-trips and can take minutes against a real Hive Metastore, unsuitable for a
    "Test connection" click a human is waiting on. Table discovery is the async Scanner's job."""
    mock_catalog = MagicMock()
    mock_catalog.list_namespaces.return_value = ["events"]
    mock_catalog.list_tables.return_value = [("events", "clicks"), ("events", "impressions")]

    with patch("cryo.sources.pyiceberg_catalog.load_catalog", return_value=mock_catalog):
        response = _client().post(
            "/api/v1/sources/test",
            headers=AUTH_HEADERS,
            json={"type": "rest", "config": {"uri": "http://localhost:8181"}},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["namespaces"] == ["events"]
    assert body["table_count"] == 0
    assert body["sample_tables"] == []
    assert body["note"]
    mock_catalog.list_tables.assert_not_called()


def test_source_test_endpoint_hive_reports_namespaces_without_listing_tables() -> None:
    mock_catalog = MagicMock()
    mock_catalog.list_namespaces.return_value = ["hms_issues"]
    mock_catalog.list_tables.return_value = [
        ("hms_issues", "balanced_control"),
        ("hms_issues", "day_hot_partition_skew"),
    ]

    with patch("cryo.sources.pyiceberg_catalog.load_catalog", return_value=mock_catalog):
        response = _client().post(
            "/api/v1/sources/test",
            headers=AUTH_HEADERS,
            json={
                "type": "hive",
                "config": {
                    "catalog_name": "hms",
                    "uri": "thrift://localhost:9083",
                    "warehouse": "s3://warehouse/",
                },
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["namespaces"] == ["hms_issues"]
    assert body["table_count"] == 0
    assert body["sample_tables"] == []
    mock_catalog.list_tables.assert_not_called()


def test_source_test_suggests_a_warehouse_when_the_config_has_none() -> None:
    """A connection test passes without a warehouse, but every scan then fails.

    list_namespaces() doesn't need one, so onboarding reported success and the scanner
    later failed every table with "source config missing warehouse". Deriving it from the
    catalog's own database locations means the operator never has to invent a value.
    """
    mock_catalog = MagicMock()
    mock_catalog.list_namespaces.return_value = ["sales"]

    with (
        patch("cryo.sources.pyiceberg_catalog.load_catalog", return_value=mock_catalog),
        patch(
            "cryo.api.routes.derive_hive_warehouse_root",
            return_value="s3a://lake-prod/iceberg/warehouse",
        ),
    ):
        response = _client().post(
            "/api/v1/sources/test",
            headers=AUTH_HEADERS,
            json={"type": "hive", "config": {"uri": "thrift://localhost:9083"}},
        )

    body = response.json()
    assert body["ok"] is True
    assert body["suggested_warehouse"] == "s3a://lake-prod/iceberg/warehouse"


def test_source_test_leaves_an_existing_warehouse_alone() -> None:
    """Never second-guess a warehouse the operator already set."""
    mock_catalog = MagicMock()
    mock_catalog.list_namespaces.return_value = ["sales"]

    with (
        patch("cryo.sources.pyiceberg_catalog.load_catalog", return_value=mock_catalog),
        patch("cryo.api.routes.derive_hive_warehouse_root") as derive,
    ):
        response = _client().post(
            "/api/v1/sources/test",
            headers=AUTH_HEADERS,
            json={
                "type": "hive",
                "config": {"uri": "thrift://localhost:9083", "warehouse": "s3://mine/wh"},
            },
        )

    assert response.json()["suggested_warehouse"] is None
    derive.assert_not_called()


def test_source_test_survives_warehouse_derivation_failure() -> None:
    """Derivation is a convenience -- it must never break the connection test itself."""
    mock_catalog = MagicMock()
    mock_catalog.list_namespaces.return_value = ["sales"]

    with (
        patch("cryo.sources.pyiceberg_catalog.load_catalog", return_value=mock_catalog),
        patch(
            "cryo.api.routes.derive_hive_warehouse_root",
            side_effect=RuntimeError("metastore refused"),
        ),
    ):
        response = _client().post(
            "/api/v1/sources/test",
            headers=AUTH_HEADERS,
            json={"type": "hive", "config": {"uri": "thrift://localhost:9083"}},
        )

    body = response.json()
    assert body["ok"] is True
    assert body["suggested_warehouse"] is None


def test_discover_storage_reports_buckets_from_table_locations() -> None:
    """Buckets come from TABLE locations, not the database default.

    A database's DB_LOCATION_URI is just its default -- on a real metastore nearly every
    database inherits one warehouse default while its tables sit in several different
    buckets, so reading database-level locations reports a small wrong answer.
    """
    with (
        patch("cryo.api.routes.probe_bucket_access", return_value={}),
        patch(
            "cryo.api.routes.list_hive_databases_from_uri",
            return_value=["sales", "marketing"],
        ),
        patch(
            "cryo.api.routes.list_hive_table_locations_for_databases",
            return_value=[
                CatalogTableLocation("sales", "orders", "s3a://bronze-layer/sales/orders"),
                CatalogTableLocation("sales", "returns", "s3a://gold-layer/sales/returns"),
                CatalogTableLocation("marketing", "leads", "s3a://bronze-layer/mk/leads"),
            ],
        ),
    ):
        response = _client().post(
            "/api/v1/sources/discover-storage",
            headers=AUTH_HEADERS,
            json={
                "type": "hive",
                "config": {"uri": "thrift://localhost:9083"},
                "offset": 0,
                "limit": 25,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    # Ranked by table count: bronze holds 2 of the 3 tables, gold 1.
    assert [b["name"] for b in body["buckets"]] == ["bronze-layer", "gold-layer"]
    assert [b["tables"] for b in body["buckets"]] == [2, 1]
    assert body["total"] == 2
    assert body["scanned"] == 2


def test_discover_storage_pages_through_databases() -> None:
    """Each call walks only its slice and reports a cursor, so the UI can show buckets
    as they arrive instead of blocking on a full multi-minute sweep."""
    captured: dict[str, object] = {}

    def _fake_locations(uri: str, databases: list[str]) -> list[CatalogTableLocation]:
        captured["databases"] = databases
        return [CatalogTableLocation(databases[0], "t", "s3://second-slice/x")]

    with (
        patch("cryo.api.routes.probe_bucket_access", return_value={}),
        patch(
            "cryo.api.routes.list_hive_databases_from_uri",
            return_value=["a", "b", "c", "d"],
        ),
        patch("cryo.api.routes.list_hive_table_locations_for_databases", _fake_locations),
    ):
        response = _client().post(
            "/api/v1/sources/discover-storage",
            headers=AUTH_HEADERS,
            json={
                "type": "hive",
                "config": {"uri": "thrift://localhost:9083"},
                "offset": 2,
                "limit": 2,
            },
        )

    body = response.json()
    assert captured["databases"] == ["c", "d"]
    assert body["scanned"] == 4
    assert body["total"] == 4
    assert [b["name"] for b in body["buckets"]] == ["second-slice"]


def test_discover_storage_skips_a_failing_slice_without_losing_the_walk() -> None:
    """One unreadable slice must advance the cursor, not abort discovery."""
    with (
        patch("cryo.api.routes.list_hive_databases_from_uri", return_value=["a", "b", "c"]),
        patch(
            "cryo.api.routes.list_hive_table_locations_for_databases",
            side_effect=RuntimeError("permission denied"),
        ),
    ):
        response = _client().post(
            "/api/v1/sources/discover-storage",
            headers=AUTH_HEADERS,
            json={
                "type": "hive",
                "config": {"uri": "thrift://localhost:9083"},
                "offset": 0,
                "limit": 2,
            },
        )

    body = response.json()
    assert body["ok"] is True
    assert body["scanned"] == 2  # cursor advanced past the bad slice
    assert "permission denied" in body["error"]


def test_discover_storage_reports_per_bucket_access() -> None:
    """A bucket in the catalog may still be unreadable -- say so during onboarding.

    Catalog metadata and S3 permissions are configured by different people at different
    times, so a bucket appearing here is no guarantee Cryo can read it. Without this the
    mismatch first surfaces during a scan, long after onboarding reported success.
    """
    with (
        patch("cryo.api.routes.list_hive_databases_from_uri", return_value=["sales"]),
        patch(
            "cryo.api.routes.list_hive_table_locations_for_databases",
            return_value=[
                CatalogTableLocation("sales", "a", "s3://readable-bucket/a"),
                CatalogTableLocation("sales", "b", "s3://locked-bucket/b"),
            ],
        ),
        patch(
            "cryo.api.routes.probe_bucket_access",
            return_value={
                "readable-bucket": ("readable", None),
                "locked-bucket": ("denied", "Credentials can't read this bucket"),
            },
        ),
    ):
        response = _client().post(
            "/api/v1/sources/discover-storage",
            headers=AUTH_HEADERS,
            json={"type": "hive", "config": {"uri": "thrift://localhost:9083"}},
        )

    by_name = {b["name"]: b for b in response.json()["buckets"]}
    assert by_name["readable-bucket"]["access"] == "readable"
    assert by_name["locked-bucket"]["access"] == "denied"
    assert "can't read" in by_name["locked-bucket"]["access_detail"]


def test_discover_storage_still_lists_buckets_when_the_probe_fails() -> None:
    """A probe that blows up must not take the bucket list down with it."""
    with (
        patch("cryo.api.routes.list_hive_databases_from_uri", return_value=["sales"]),
        patch(
            "cryo.api.routes.list_hive_table_locations_for_databases",
            return_value=[CatalogTableLocation("sales", "a", "s3://some-bucket/a")],
        ),
        patch(
            "cryo.api.routes.s3_client_kwargs_for_source_config",
            side_effect=RuntimeError("no credentials"),
        ),
    ):
        response = _client().post(
            "/api/v1/sources/discover-storage",
            headers=AUTH_HEADERS,
            json={"type": "hive", "config": {"uri": "thrift://localhost:9083"}},
        )

    body = response.json()
    assert body["ok"] is True
    assert body["buckets"][0]["name"] == "some-bucket"
    assert body["buckets"][0]["access"] == "unknown"


def test_discover_storage_rejects_non_hive_catalogs() -> None:
    """Only HMS exposes table locations cheaply; others would need a load_table per table."""
    response = _client().post(
        "/api/v1/sources/discover-storage",
        headers=AUTH_HEADERS,
        json={"type": "rest", "config": {"uri": "http://localhost:8181"}},
    )

    body = response.json()
    assert body["ok"] is False
    assert "Hive Metastore" in body["error"]


def test_source_test_endpoint_reports_connection_failure() -> None:
    response = _client().post(
        "/api/v1/sources/test",
        headers=AUTH_HEADERS,
        json={"type": "made-up-catalog-type", "config": {}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["error"] and "Unsupported source type" in body["error"]
