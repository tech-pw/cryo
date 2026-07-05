from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from cryo.api.app import create_app
from cryo.config import Settings
from tests.auth_helpers import OPERATOR_HEADERS as AUTH_HEADERS


def _client() -> TestClient:
    app = create_app(Settings(_env_file=None, environment="test"))
    return TestClient(app)


def test_healthz() -> None:
    response = _client().get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "test"}


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
    assert body["items"][0]["statements"][1]["sql"] == (
        "ALTER TABLE iceberg.fixtures.fixture_multi_column "
        "EXECUTE optimize(file_size_threshold => '256MB') "
        "WHERE (event_date >= DATE '2025-01-01' AND event_date < DATE '2025-01-02' "
        "AND region = 'us')"
    )


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


def test_source_test_endpoint_reports_discovered_tables() -> None:
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
    assert body["table_count"] == 2
    assert set(body["sample_tables"]) == {"events.clicks", "events.impressions"}


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
