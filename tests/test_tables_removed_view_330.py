"""#330: one ``removed`` mode drives the registry list, CSV export and group-by."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from cryo.db.models import Table
from tests.auth_helpers import ADMIN_HEADERS, VIEWER_HEADERS

REMOVED_AT = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)


@pytest.fixture
def seeded(api_db):
    created = api_db.client.post(
        "/api/v1/sources",
        headers=ADMIN_HEADERS,
        json={"name": "removed-view", "type": "rest", "config": {}},
    )
    source_id = UUID(created.json()["id"])
    with api_db.session_factory() as session:
        session.add_all(
            [
                Table(source_id=source_id, schema_name="s", table_name="live"),
                Table(
                    source_id=source_id,
                    schema_name="s",
                    table_name="paused",
                    is_active=False,
                ),
                Table(
                    source_id=source_id,
                    schema_name="s",
                    table_name="gone",
                    is_active=False,
                    removed_from_catalog_at=REMOVED_AT,
                    removed_reason="not_found_in_catalog: removed-view/s",
                ),
            ]
        )
        session.commit()
    return api_db


def _names(client, query: str = "") -> list[str]:
    body = client.get(f"/api/v1/tables?limit=50{query}", headers=VIEWER_HEADERS).json()
    names = sorted(row["table_name"] for row in body["items"])
    assert body["total"] == len(names)
    return names


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", ["live", "paused"]),
        ("&removed=exclude", ["live", "paused"]),
        ("&removed=include", ["gone", "live", "paused"]),
        ("&removed=only", ["gone"]),
        ("&include_removed=true", ["gone", "live", "paused"]),
        ("&include_removed=true&removed=only", ["gone"]),
        ("&include_removed=true&removed=exclude", ["live", "paused"]),
    ],
)
def test_list_removed_modes(seeded, query: str, expected: list[str]) -> None:
    assert _names(seeded.client, query) == expected


def test_removed_only_rows_carry_when_and_why(seeded) -> None:
    body = seeded.client.get("/api/v1/tables?removed=only", headers=VIEWER_HEADERS).json()
    (row,) = body["items"]
    assert row["removed_from_catalog_at"].startswith("2026-10-01T03:00:00")
    assert row["removed_reason"] == "not_found_in_catalog: removed-view/s"
    assert row["is_active"] is False


def test_removed_only_composes_with_the_predicate(seeded) -> None:
    active = json.dumps({"field": "is_active", "op": "=", "value": True})
    assert _names(seeded.client, f"&removed=only&filter={active}") == []
    assert _names(seeded.client, f"&removed=include&filter={active}") == ["live"]


def test_unknown_removed_mode_is_rejected(seeded) -> None:
    response = seeded.client.get("/api/v1/tables?removed=all", headers=VIEWER_HEADERS)
    assert response.status_code == 422


def test_cursor_is_bound_to_the_removed_mode(seeded) -> None:
    first = seeded.client.get(
        "/api/v1/tables?removed=include&limit=1&sort=table_name", headers=VIEWER_HEADERS
    ).json()
    assert first["cursor"]
    reused = seeded.client.get(
        f"/api/v1/tables?limit=1&sort=table_name&cursor={first['cursor']}",
        headers=VIEWER_HEADERS,
    )
    assert reused.status_code == 409
    same = seeded.client.get(
        f"/api/v1/tables?removed=include&limit=1&sort=table_name&cursor={first['cursor']}",
        headers=VIEWER_HEADERS,
    )
    assert same.status_code == 200


def _csv_rows(client, query: str = "") -> list[dict[str, str]]:
    response = client.get(f"/api/v1/tables/export.csv{query}", headers=VIEWER_HEADERS)
    assert response.status_code == 200
    return list(csv.DictReader(io.StringIO(response.text)))


def test_export_matches_the_list(seeded) -> None:
    assert sorted(row["table"] for row in _csv_rows(seeded.client)) == ["live", "paused"]
    (gone,) = _csv_rows(seeded.client, "?removed=only")
    assert gone["table"] == "gone"
    assert gone["removed_from_catalog_at"].startswith("2026-10-01T03:00:00")
    assert gone["removed_reason"] == "not_found_in_catalog: removed-view/s"
    everything = _csv_rows(seeded.client, "?removed=include")
    assert sorted(row["table"] for row in everything) == ["gone", "live", "paused"]
    live = next(row for row in everything if row["table"] == "live")
    assert live["removed_from_catalog_at"] == ""
    assert live["removed_reason"] == ""


def test_export_keeps_existing_column_positions(seeded) -> None:
    response = seeded.client.get("/api/v1/tables/export.csv", headers=VIEWER_HEADERS)
    header = next(csv.reader(io.StringIO(response.text)))
    assert header[:16] == [
        "source",
        "qualified_table",
        "schema",
        "table",
        "last_snapshot_committed_at",
        "last_scanned_at",
        "consecutive_scan_failures",
        "last_scan_error",
        "last_scan_error_at",
        "last_scan_error_kind",
        "grade",
        "health_score",
        "size_gb",
        "small_files",
        "total_files",
        "owner",
    ]
    assert header[16:] == [
        "effective_preset",
        "effective_provenance",
        "removed_from_catalog_at",
        "removed_reason",
    ]


def _provenance_of(client, table_name: str) -> str:
    body = client.get("/api/v1/tables?removed=include", headers=VIEWER_HEADERS).json()
    row = next(row for row in body["items"] if row["table_name"] == table_name)
    return str(row["effective_preset"]["provenance"])


def test_provenance_filter_applies_on_top_of_the_removed_mode(seeded) -> None:
    provenance = _provenance_of(seeded.client, "gone")
    query = f"&removed=only&effective_provenance={provenance}"
    assert _names(seeded.client, query) == ["gone"]
    (gone,) = _csv_rows(seeded.client, "?" + query.lstrip("&"))
    assert gone["effective_provenance"] == provenance
    assert gone["removed_reason"] == "not_found_in_catalog: removed-view/s"
    assert _group_total(seeded.client, query) == 1
    other = next(p for p in ("assignment", "rule") if p != provenance)
    assert _names(seeded.client, f"&removed=only&effective_provenance={other}") == []


def test_cursor_is_bound_to_both_removed_mode_and_provenance(seeded) -> None:
    provenance = _provenance_of(seeded.client, "live")
    base = (
        f"/api/v1/tables?limit=1&sort=table_name&removed=include&effective_provenance={provenance}"
    )
    first = seeded.client.get(base, headers=VIEWER_HEADERS).json()
    assert first["cursor"]
    cursor = first["cursor"]
    assert seeded.client.get(f"{base}&cursor={cursor}", headers=VIEWER_HEADERS).status_code == 200
    without_provenance = f"/api/v1/tables?limit=1&sort=table_name&removed=include&cursor={cursor}"
    assert seeded.client.get(without_provenance, headers=VIEWER_HEADERS).status_code == 409
    other_mode = (
        "/api/v1/tables?limit=1&sort=table_name&removed=exclude"
        f"&effective_provenance={provenance}&cursor={cursor}"
    )
    assert seeded.client.get(other_mode, headers=VIEWER_HEADERS).status_code == 409


def _group_total(client, query: str = "") -> int:
    response = client.get(
        f"/api/v1/tables/group-by?field=schema_name{query}", headers=VIEWER_HEADERS
    )
    assert response.status_code == 200
    return sum(row["count"] for row in response.json()["rows"])


def test_group_by_totals_match_the_list(seeded) -> None:
    assert _group_total(seeded.client) == len(_names(seeded.client))
    assert _group_total(seeded.client, "&removed=only") == 1
    assert _group_total(seeded.client, "&removed=include") == 3
