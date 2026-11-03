"""#330: GET /tables reports each table's effective preset and filters by its provenance."""

from __future__ import annotations

import csv
import io
import uuid

from cryo.db.models import Source, Table, TableAssignment
from tests.auth_helpers import ADMIN_HEADERS, VIEWER_HEADERS
from tests.test_presets_api_db import ApiDb

CRON = "0 2 * * *"


def _seed(api_db: ApiDb, *, with_default: bool = True) -> dict[str, str]:
    client = api_db.client
    destination = client.post(
        "/api/v1/destinations",
        headers=ADMIN_HEADERS,
        json={"name": "trino", "engine": "trino", "config": {"host": "trino"}},
    ).json()

    def preset(name: str, **extra: object) -> dict:
        response = client.post(
            "/api/v1/presets",
            headers=ADMIN_HEADERS,
            json={
                "name": name,
                "destination_id": destination["id"],
                "operations": ["optimize"],
                "schedule_cron": CRON,
                **extra,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    pinned = preset("pinned", paused=True)
    ruled = preset("ruled")
    rule = client.post(
        f"/api/v1/presets/{ruled['id']}/rules",
        headers=ADMIN_HEADERS,
        json={"predicate": {"field": "schema_name", "op": "=", "value": "sales"}},
    )
    assert rule.status_code == 201, rule.text
    ids = {"pinned_preset": pinned["id"], "ruled_preset": ruled["id"]}
    if with_default:
        ids["default_preset"] = preset("fallback", is_default=True)["id"]

    with api_db.session_factory() as session:
        source = Source(name="hms", type="rest", config={})
        session.add(source)
        session.flush()
        tables = {
            "pinned": Table(source_id=source.id, schema_name="ops", table_name="pinned"),
            "ruled": Table(source_id=source.id, schema_name="sales", table_name="orders"),
            "fallback": Table(source_id=source.id, schema_name="ops", table_name="events"),
            "scratch": Table(source_id=source.id, schema_name="tmp", table_name="scratch"),
        }
        session.add_all(tables.values())
        session.flush()
        session.add(
            TableAssignment(
                table_id=tables["pinned"].id, preset_id=uuid.UUID(pinned["id"]), pinned=True
            )
        )
        session.commit()
        ids.update({name: str(table.id) for name, table in tables.items()})
    return ids


def _items(api_db: ApiDb, query: str = "") -> dict[str, dict]:
    response = api_db.client.get(f"/api/v1/tables{query}", headers=VIEWER_HEADERS)
    assert response.status_code == 200, response.text
    return {row["table_name"]: row for row in response.json()["items"]}


def test_each_row_carries_its_effective_preset_and_provenance(api_db: ApiDb) -> None:
    ids = _seed(api_db)
    rows = _items(api_db)

    assert rows["pinned"]["effective_preset"] == {
        "preset_id": ids["pinned_preset"],
        "preset_name": "pinned",
        "provenance": "assignment",
        "rule_id": None,
        "paused": True,
    }
    ruled = rows["orders"]["effective_preset"]
    assert (ruled["preset_name"], ruled["provenance"], ruled["paused"]) == ("ruled", "rule", False)
    assert ruled["rule_id"] is not None
    fallback = rows["events"]["effective_preset"]
    assert (fallback["preset_id"], fallback["provenance"]) == (ids["default_preset"], "default")
    # Ephemeral tables never fall through to the default.
    assert rows["scratch"]["effective_preset"] == {
        "preset_id": None,
        "preset_name": None,
        "provenance": "none",
        "rule_id": None,
        "paused": False,
    }


def test_detail_view_leaves_the_page_only_field_null(api_db: ApiDb) -> None:
    ids = _seed(api_db)
    detail = api_db.client.get(f"/api/v1/tables/{ids['ruled']}", headers=VIEWER_HEADERS).json()
    assert detail["effective_preset"] is None


def test_filter_on_default_and_unmaintained(api_db: ApiDb) -> None:
    _seed(api_db)

    on_default = api_db.client.get(
        "/api/v1/tables?effective_provenance=default", headers=VIEWER_HEADERS
    ).json()
    assert [row["table_name"] for row in on_default["items"]] == ["events"]
    assert on_default["total"] == 1

    unmaintained = _items(api_db, "?effective_provenance=none")
    assert set(unmaintained) == {"scratch"}

    combined = _items(api_db, "?effective_provenance=default&effective_provenance=none")
    assert set(combined) == {"events", "scratch"}

    pinned = _items(api_db, "?effective_provenance=assignment")
    assert set(pinned) == {"pinned"}


def test_without_a_default_unmatched_tables_are_unmaintained(api_db: ApiDb) -> None:
    _seed(api_db, with_default=False)
    assert set(_items(api_db, "?effective_provenance=none")) == {"events", "scratch"}
    assert _items(api_db, "?effective_provenance=default") == {}


def test_provenance_filter_composes_with_the_predicate_filter(api_db: ApiDb) -> None:
    _seed(api_db)
    rows = _items(
        api_db,
        '?effective_provenance=none&effective_provenance=default&filter={"field":"schema_name",'
        '"op":"=","value":"ops"}',
    )
    assert set(rows) == {"events"}


def test_unknown_provenance_is_rejected(api_db: ApiDb) -> None:
    response = api_db.client.get(
        "/api/v1/tables?effective_provenance=orphaned", headers=VIEWER_HEADERS
    )
    assert response.status_code == 422


def test_cursor_from_one_provenance_filter_is_refused_for_another(api_db: ApiDb) -> None:
    _seed(api_db)
    first = api_db.client.get(
        "/api/v1/tables?effective_provenance=default&effective_provenance=none&limit=1",
        headers=VIEWER_HEADERS,
    ).json()
    assert first["has_more"] is True
    cursor = first["cursor"]

    same = api_db.client.get(
        f"/api/v1/tables?effective_provenance=none&effective_provenance=default&limit=1&cursor={cursor}",
        headers=VIEWER_HEADERS,
    )
    assert same.status_code == 200
    other = api_db.client.get(
        f"/api/v1/tables?effective_provenance=none&limit=1&cursor={cursor}",
        headers=VIEWER_HEADERS,
    )
    assert other.status_code == 409


def test_csv_export_has_effective_preset_columns_and_honors_the_filter(api_db: ApiDb) -> None:
    _seed(api_db)
    response = api_db.client.get("/api/v1/tables/export.csv", headers=VIEWER_HEADERS)
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    by_table = {row["table"]: row for row in rows}
    assert (by_table["pinned"]["effective_preset"], by_table["pinned"]["effective_provenance"]) == (
        "pinned",
        "assignment",
    )
    assert by_table["events"]["effective_provenance"] == "default"
    assert (
        by_table["scratch"]["effective_preset"],
        by_table["scratch"]["effective_provenance"],
    ) == (
        "",
        "none",
    )

    filtered = api_db.client.get(
        "/api/v1/tables/export.csv?effective_provenance=none", headers=VIEWER_HEADERS
    )
    assert [row["table"] for row in csv.DictReader(io.StringIO(filtered.text))] == ["scratch"]


def test_group_by_honors_the_provenance_filter(api_db: ApiDb) -> None:
    _seed(api_db)
    response = api_db.client.get(
        "/api/v1/tables/group-by?field=schema_name&effective_provenance=default"
        "&effective_provenance=none",
        headers=VIEWER_HEADERS,
    )
    assert response.status_code == 200, response.text
    counts = {row["group_value"]: row["count"] for row in response.json()["rows"]}
    assert counts == {"ops": 1, "tmp": 1}
