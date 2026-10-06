"""Tests for global catalog search helpers and endpoint."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cryo.api.search_routes import (
    SearchMatchRead,
    escape_ilike_pattern,
    ilike_pattern,
    merge_table_matches,
    table_literal_match_reason,
)
from cryo.db.models import Source, Table, TableColumn, TableStats
from tests.auth_helpers import VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb


def test_escape_ilike_pattern_escapes_wildcards() -> None:
    assert escape_ilike_pattern("50%_off") == "50\\%\\_off"
    assert ilike_pattern("user") == "%user%"


def test_table_literal_match_reason_prefers_name_over_schema() -> None:
    assert (
        table_literal_match_reason(
            query="orders",
            schema_name="sales",
            table_name="orders",
            qualified="iceberg.sales.orders",
        )
        == "name"
    )
    assert (
        table_literal_match_reason(
            query="user_data",
            schema_name="user_data",
            table_name="profiles",
            qualified="iceberg.user_data.profiles",
        )
        == "schema"
    )
    assert (
        table_literal_match_reason(
            query="billing",
            schema_name="sales",
            table_name="orders",
            qualified="iceberg.sales.orders",
        )
        is None
    )


def test_merge_table_matches_deduplicates_by_table_id() -> None:
    table_id = uuid.uuid4()
    literal = [
        SearchMatchRead(
            asset_type="table",
            name="customer_accounts",
            table_id=table_id,
            match_reason="name",
        )
    ]
    semantic = [
        SearchMatchRead(
            asset_type="table",
            name="customer_accounts",
            table_id=table_id,
            match_reason="semantic",
            score=0.91,
        )
    ]
    merged = merge_table_matches(literal, semantic)
    assert len(merged) == 1
    assert merged[0].match_reason == "name"
    assert merged[0].score is None


def test_search_endpoint_literal_catalog_schema_table_hits(api_db: ApiDb, monkeypatch) -> None:
    session = api_db.session_factory()
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    session.add(
        Source(
            id=source_id,
            name="userlake",
            type="rest",
            config={"catalog_name": "userlake"},
        )
    )
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name="user_data",
            table_name="user_profiles",
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=1,
            total_size_gb=1.0,
            query_count_30d=0,
        )
    )
    session.commit()
    session.close()

    def _forbid_fleet_scan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("search must not load the fleet or compute embeddings")

    monkeypatch.setattr("cryo.api.search_routes._fleet_rows", _forbid_fleet_scan)
    monkeypatch.setattr("cryo.api.search_routes.search_semantic_tables", _forbid_fleet_scan)

    response = api_db.client.get("/api/v1/search?q=user", headers=VIEWER_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "user"

    groups = {group["asset_type"]: group for group in payload["groups"]}
    assert groups["catalogs"]["total"] >= 1
    assert any(hit["name"] == "userlake" for hit in groups["catalogs"]["hits"])
    assert groups["schemas"]["total"] >= 1
    assert any(hit["schema_name"] == "user_data" for hit in groups["schemas"]["hits"])
    assert groups["tables"]["total"] >= 1
    assert any(hit["table_id"] == str(table_id) for hit in groups["tables"]["hits"])


def test_search_endpoint_group_pagination(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    source_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    session.add(
        Source(
            id=source_id,
            name="alpha",
            type="rest",
            config={},
        )
    )
    for idx in range(3):
        table_id = uuid.uuid4()
        session.add(
            Table(
                id=table_id,
                source_id=source_id,
                schema_name="analytics",
                table_name=f"alpha_table_{idx}",
                first_seen_at=scan_at,
                is_active=True,
            )
        )
        session.add(
            TableStats(
                table_id=table_id,
                scan_at=scan_at,
                needs_compaction=False,
                compaction_priority="LOW",
                grade="B",
                overall_score=80,
                total_files=10,
                small_files_count=1,
                total_size_gb=1.0,
                query_count_30d=0,
            )
        )
    session.commit()
    session.close()

    response = api_db.client.get(
        "/api/v1/search?q=alpha&group=tables&offset=1&limit=1",
        headers=VIEWER_HEADERS,
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["groups"]) == 1
    group = payload["groups"][0]
    assert group["asset_type"] == "tables"
    assert group["total"] == 3
    assert len(group["hits"]) == 1


def test_search_endpoint_literal_column_hits(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    session.add(
        Source(
            id=source_id,
            name="analytics",
            type="rest",
            config={"catalog_name": "analytics"},
        )
    )
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name="core",
            table_name="orders",
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=1,
            total_size_gb=1.0,
            query_count_30d=0,
        )
    )
    session.add(
        TableColumn(
            table_id=table_id,
            column_name="customer_email",
            data_type="string",
            description="Primary contact email for the customer",
        )
    )
    session.commit()
    session.close()

    response = api_db.client.get("/api/v1/search?q=email", headers=VIEWER_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    groups = {group["asset_type"]: group for group in payload["groups"]}
    assert groups["columns"]["total"] >= 1
    hit = next(
        item for item in groups["columns"]["hits"] if item["column_name"] == "customer_email"
    )
    assert hit["asset_type"] == "column"
    assert hit["match_reason"] == "column"
    assert hit["table_id"] == str(table_id)
    assert hit["data_type"] == "string"
    assert hit["qualified_name"] == "analytics.core.orders.customer_email"


def test_search_endpoint_column_group_pagination(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    session.add(
        Source(
            id=source_id,
            name="warehouse",
            type="rest",
            config={},
        )
    )
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name="public",
            table_name="events",
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    session.add(
        TableStats(
            table_id=table_id,
            scan_at=scan_at,
            needs_compaction=False,
            compaction_priority="LOW",
            grade="B",
            overall_score=80,
            total_files=10,
            small_files_count=1,
            total_size_gb=1.0,
            query_count_30d=0,
        )
    )
    for idx in range(3):
        session.add(
            TableColumn(
                table_id=table_id,
                column_name=f"metric_{idx}",
                data_type="bigint",
            )
        )
    session.commit()
    session.close()

    response = api_db.client.get(
        "/api/v1/search?q=metric&group=columns&offset=1&limit=1",
        headers=VIEWER_HEADERS,
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["groups"]) == 1
    group = payload["groups"][0]
    assert group["asset_type"] == "columns"
    assert group["total"] == 3
    assert len(group["hits"]) == 1
