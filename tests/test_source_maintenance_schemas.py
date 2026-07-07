"""Tests for source maintenance schema allowlists."""

from __future__ import annotations

from uuid import uuid4

from cryo.db.models import Source, Table
from cryo.db.repositories.presets import resolve_selection
from cryo.db.repositories.sources import (
    deactivate_out_of_scope_tables,
    normalize_maintenance_schema_names,
    table_in_source_scope,
)
from cryo.scanner.run import _discover_candidates
from tests.auth_helpers import ADMIN_HEADERS, OPERATOR_HEADERS
from tests.test_presets_api_db import ApiDb


def test_normalize_maintenance_schema_names_dedupes_and_strips() -> None:
    assert normalize_maintenance_schema_names([" analytics ", "analytics", "", "cdp"]) == [
        "analytics",
        "cdp",
    ]


def test_table_in_source_scope() -> None:
    source = Source(
        id=uuid4(),
        name="src",
        type="rest",
        config={},
        maintenance_schema_names=["analytics"],
    )
    in_scope = Table(id=uuid4(), source_id=source.id, schema_name="analytics", table_name="events")
    out_scope = Table(id=uuid4(), source_id=source.id, schema_name="staging", table_name="raw")
    assert table_in_source_scope(in_scope, source)
    assert not table_in_source_scope(out_scope, source)

    source_all = Source(
        id=uuid4(),
        name="all",
        type="rest",
        config={},
        maintenance_schema_names=[],
    )
    assert table_in_source_scope(out_scope, source_all)


def test_deactivate_out_of_scope_tables(session) -> None:
    source = Source(id=uuid4(), name="src", type="rest", config={}, maintenance_schema_names=[])
    session.add(source)
    session.flush()
    kept = Table(
        id=uuid4(), source_id=source.id, schema_name="analytics", table_name="a", is_active=True
    )
    dropped = Table(
        id=uuid4(), source_id=source.id, schema_name="staging", table_name="b", is_active=True
    )
    session.add_all([kept, dropped])
    session.flush()

    deactivate_out_of_scope_tables(
        session, source_id=source.id, maintenance_schema_names=["analytics"]
    )
    session.flush()
    session.refresh(kept)
    session.refresh(dropped)
    assert kept.is_active is True
    assert dropped.is_active is False


class _FakeCatalog:
    def __init__(self, tables_by_namespace: dict[str, list[str]]):
        self._tables_by_namespace = tables_by_namespace
        self.list_tables_calls = 0
        self.list_tables_in_namespace_calls: list[str] = []

    def list_tables(self) -> list[tuple[str, str]]:
        self.list_tables_calls += 1
        return [
            (namespace, table_name)
            for namespace, tables in self._tables_by_namespace.items()
            for table_name in tables
        ]

    def list_tables_in_namespace(self, namespace: str) -> list[str]:
        self.list_tables_in_namespace_calls.append(namespace)
        return list(self._tables_by_namespace.get(namespace, []))


def test_discover_candidates_honors_source_allowlist(session) -> None:
    source = Source(
        id=uuid4(),
        name="src",
        type="rest",
        config={},
        maintenance_schema_names=["analytics"],
    )
    session.add(source)
    session.flush()
    catalog = _FakeCatalog({"analytics": ["events"], "staging": ["raw"]})

    candidates = _discover_candidates(
        session,
        source_id=source.id,
        source_factory=lambda _source: catalog,
    )

    assert [(c.schema_name, c.table_name) for c in candidates] == [("analytics", "events")]
    assert catalog.list_tables_calls == 0
    assert catalog.list_tables_in_namespace_calls == ["analytics"]


def test_discover_candidates_intersects_explicit_scan_scope(session) -> None:
    source = Source(
        id=uuid4(),
        name="src",
        type="rest",
        config={},
        maintenance_schema_names=["analytics", "staging"],
    )
    session.add(source)
    session.flush()
    catalog = _FakeCatalog({"analytics": ["events"], "staging": ["raw"]})

    candidates = _discover_candidates(
        session,
        source_id=source.id,
        schema_names=["staging"],
        source_factory=lambda _source: catalog,
    )

    assert [(c.schema_name, c.table_name) for c in candidates] == [("staging", "raw")]


def test_source_crud_maintenance_schema_names(api_db: ApiDb) -> None:
    client = api_db.client
    create = client.post(
        "/api/v1/sources",
        headers=ADMIN_HEADERS,
        json={
            "name": f"lake-{uuid4()}",
            "type": "rest",
            "config": {"uri": "http://localhost:8181"},
            "maintenance_schema_names": ["analytics", "analytics", " cdp "],
        },
    )
    assert create.status_code == 201
    body = create.json()
    assert body["maintenance_schema_names"] == ["analytics", "cdp"]
    source_id = body["id"]

    patch = client.patch(
        f"/api/v1/sources/{source_id}",
        headers=ADMIN_HEADERS,
        json={"maintenance_schema_names": ["cdp"]},
    )
    assert patch.status_code == 200
    assert patch.json()["maintenance_schema_names"] == ["cdp"]

    listed = client.get("/api/v1/sources", headers=OPERATOR_HEADERS)
    assert listed.status_code == 200
    match = next(item for item in listed.json() if item["id"] == source_id)
    assert match["maintenance_schema_names"] == ["cdp"]


def test_resolve_selection_excludes_out_of_scope_tables(api_db: ApiDb) -> None:
    session = api_db.session_factory()
    source = Source(
        id=uuid4(),
        name="scoped",
        type="rest",
        config={},
        maintenance_schema_names=["analytics"],
    )
    in_scope = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="analytics",
        table_name="events",
        is_active=True,
    )
    out_scope = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="staging",
        table_name="raw",
        is_active=True,
    )
    session.add_all([source, in_scope, out_scope])
    session.commit()

    result = resolve_selection(
        session,
        mode="ids",
        ids=[in_scope.id, out_scope.id],
        filter_payload=None,
    )
    assert result.table_ids == [in_scope.id]
    session.close()
