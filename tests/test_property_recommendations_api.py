"""Tests for table property recommendations API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from cryo.db.models import Source, Table, TableStats
from cryo.scanner.property_recommendations import compute_property_recommendations
from tests.auth_helpers import OPERATOR_HEADERS, VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb


def _seed_table(
    session,
    *,
    target_bytes: int | None = None,
    write_mode: str | None = None,
    has_sort: bool = False,
    raw_extra: dict | None = None,
    with_stats: bool = True,
) -> uuid.UUID:
    source_id = uuid.uuid4()
    table_id = uuid.uuid4()
    scan_at = datetime(2026, 7, 10, tzinfo=UTC)
    raw = {
        "iceberg_properties": {},
        "equality_delete_files": 50,
        "position_delete_files": 10,
        "commits_per_day": 30.0,
        "is_streaming": True,
        "v3_upgrade_candidate": True,
        "format_version": 2,
        "gc_enabled": True,
    }
    if raw_extra:
        raw.update(raw_extra)
    session.add(
        Source(
            id=source_id,
            name="lake",
            type="rest",
            config={"catalog_name": "iceberg", "type": "rest", "uri": "http://localhost"},
        )
    )
    session.add(
        Table(
            id=table_id,
            source_id=source_id,
            schema_name="analytics",
            table_name="events",
            first_seen_at=scan_at,
            is_active=True,
        )
    )
    if with_stats:
        session.add(
            TableStats(
                table_id=table_id,
                scan_at=scan_at,
                needs_compaction=True,
                compaction_priority="HIGH",
                grade="C",
                overall_score=65,
                total_files=120_000,
                small_files_count=45_000,
                total_size_gb=480.0,
                avg_file_size_mb=41.0,
                total_partitions=120,
                partition_skew_ratio=18.5,
                manifest_count=220,
                total_snapshots=80,
                delete_file_pct=12.5,
                total_delete_files=1500,
                write_distribution_mode=write_mode,
                has_sort_order=has_sort,
                target_file_size_bytes=target_bytes,
                query_count_30d=0,
                raw=raw,
            )
        )
    session.commit()
    return table_id


def test_compute_property_recommendations_without_stats_defaults_target_file_size(
    api_db: ApiDb,
) -> None:
    session = api_db.session_factory()
    table_id = _seed_table(session, with_stats=False)
    session.close()

    session = api_db.session_factory()
    items = compute_property_recommendations(session, table_id)
    session.close()

    target = next(
        item for item in items if item.property_key == "write.target-file-size-bytes"
    )
    assert target.action == "apply"
    assert target.tier == "tier1"
    assert target.recommended_value == str(256 * 1024 * 1024)


def test_compute_property_recommendations_tier1_keys(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_table(session, target_bytes=None, write_mode=None, has_sort=False)
    session.close()

    session = api_db.session_factory()
    items = compute_property_recommendations(session, table_id)
    session.close()

    keys = {item.property_key for item in items}
    assert "write.target-file-size-bytes" in keys
    assert "write.distribution-mode" in keys
    assert "write.metadata.delete-after-commit.enabled" in keys
    assert "commit.retry.num-retries" in keys
    assert "write.merge-mode" in keys
    assert "write.sort-order" in keys


def test_get_property_recommendations_endpoint(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_table(session)
    session.close()

    response = api_db.client.get(
        f"/api/v1/tables/{table_id}/property-recommendations",
        headers=VIEWER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["table_id"] == str(table_id)
    assert len(body["items"]) >= 5
    tiers = {item["tier"] for item in body["items"]}
    assert "tier1" in tiers


def test_apply_table_properties_endpoint_audited(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_table(session)
    session.close()

    mock_table = MagicMock()
    mock_txn = MagicMock()
    mock_table.transaction.return_value.__enter__ = MagicMock(return_value=mock_txn)
    mock_table.transaction.return_value.__exit__ = MagicMock(return_value=False)

    with patch(
        "cryo.sources.pyiceberg_catalog.PyIcebergSourceCatalog",
    ) as catalog_cls:
        catalog_cls.return_value.load_table.return_value = mock_table
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/properties/apply",
            headers=OPERATOR_HEADERS,
            json={
                "properties": {
                    "write.target-file-size-bytes": "268435456",
                    "write.distribution-mode": "hash",
                },
                "note": "test apply",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["applied"]["write.distribution-mode"] == "hash"
    assert body["audit_log_id"] > 0
    mock_txn.set_properties.assert_called_once()

    session = api_db.session_factory()
    from cryo.db.models import AuditLog

    audit = session.get(AuditLog, body["audit_log_id"])
    assert audit is not None
    assert audit.action == "apply_table_properties"
    assert audit.actor == "test-user"
    session.close()


def test_apply_rejects_unlisted_property(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_table(session)
    session.close()

    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/properties/apply",
        headers=OPERATOR_HEADERS,
        json={"properties": {"unknown.property": "x"}},
    )
    assert response.status_code == 400


def test_apply_format_version_requires_confirmation(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    table_id = _seed_table(session)
    session.close()

    response = api_db.client.post(
        f"/api/v1/tables/{table_id}/properties/apply",
        headers=OPERATOR_HEADERS,
        json={"properties": {"format-version": "3"}},
    )
    assert response.status_code == 400

    mock_table = MagicMock()
    mock_txn = MagicMock()
    mock_table.transaction.return_value.__enter__ = MagicMock(return_value=mock_txn)
    mock_table.transaction.return_value.__exit__ = MagicMock(return_value=False)

    with patch(
        "cryo.sources.pyiceberg_catalog.PyIcebergSourceCatalog",
    ) as catalog_cls:
        catalog_cls.return_value.load_table.return_value = mock_table
        response = api_db.client.post(
            f"/api/v1/tables/{table_id}/properties/apply",
            headers=OPERATOR_HEADERS,
            json={"properties": {"format-version": "3"}, "confirm_risky": True},
        )
    assert response.status_code == 200


def test_property_recommendations_404(api_db: ApiDb) -> None:
    missing = uuid.uuid4()
    response = api_db.client.get(
        f"/api/v1/tables/{missing}/property-recommendations",
        headers=VIEWER_HEADERS,
    )
    assert response.status_code == 404
