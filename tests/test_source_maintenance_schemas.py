"""Tests for source maintenance schema allowlists."""

from __future__ import annotations

from uuid import uuid4

from cryo.db.models import Source, Table
from cryo.db.repositories.presets import resolve_selection
from cryo.db.repositories.sources import (
    deactivate_orphaned_evolving_catalog_entries,
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


def test_deactivate_orphaned_evolving_catalog_entries_deactivates_only_confirmed_orphans(
    session,
) -> None:
    """Direct regression test for the live incident: 1,371 active tables named ``*_evolving``
    with location=None, each confirmed to have a same-schema sibling under the suffix-stripped
    name that DOES have a real location -- e.g. ezpay.gold_notifications_evolving (orphaned)
    alongside ezpay.gold_notifications (the real, currently-scanned table)."""
    source = Source(id=uuid4(), name="src", type="rest", config={}, maintenance_schema_names=[])
    session.add(source)
    session.flush()

    orphan = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="ezpay",
        table_name="gold_notifications_evolving",
        location=None,
        is_active=True,
    )
    real_sibling = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="ezpay",
        table_name="gold_notifications",
        location="s3a://example-prod-bucket/silver/ezpay/notifications_evolving",
        is_active=True,
    )
    session.add_all([orphan, real_sibling])
    session.flush()

    changed = deactivate_orphaned_evolving_catalog_entries(session)
    session.flush()
    session.refresh(orphan)
    session.refresh(real_sibling)

    assert changed == 1
    assert orphan.is_active is False
    assert real_sibling.is_active is True


def test_deactivate_orphaned_evolving_catalog_entries_leaves_unresolvable_orphans_alone(
    session,
) -> None:
    """The sibling-exists check is the actual safety boundary -- a table matching the
    ``*_evolving`` + location=None shape but with NO real-location sibling must be left alone,
    since deactivating it would be a guess, not a confirmed cleanup."""
    source = Source(id=uuid4(), name="src", type="rest", config={}, maintenance_schema_names=[])
    session.add(source)
    session.flush()

    unresolvable = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="orphanage",
        table_name="no_sibling_evolving",
        location=None,
        is_active=True,
    )
    session.add(unresolvable)
    session.flush()

    changed = deactivate_orphaned_evolving_catalog_entries(session)
    session.flush()
    session.refresh(unresolvable)

    assert changed == 0
    assert unresolvable.is_active is True


def test_deactivate_orphaned_evolving_catalog_entries_ignores_tables_with_a_real_location(
    session,
) -> None:
    """A table named ``*_evolving`` that itself has a real location is not orphaned -- some
    tables are legitimately named this way (e.g. dbt's own _evolving materialization suffix) --
    only the location IS NULL + confirmed-sibling combination is the actual orphan signature."""
    source = Source(id=uuid4(), name="src", type="rest", config={}, maintenance_schema_names=[])
    session.add(source)
    session.flush()

    legit = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="lms",
        table_name="course_change_histories_evolving",
        location="s3a://example-prod-bucket/silver/lms/course_change_histories_evolving",
        is_active=True,
    )
    session.add(legit)
    session.flush()

    changed = deactivate_orphaned_evolving_catalog_entries(session)
    session.flush()
    session.refresh(legit)

    assert changed == 0
    assert legit.is_active is True


def test_deactivate_orphaned_evolving_catalog_entries_ignores_already_inactive_orphans(
    session,
) -> None:
    """Idempotent -- an orphan already deactivated by a prior run must not be touched (and must
    not be miscounted) on a re-run."""
    source = Source(id=uuid4(), name="src", type="rest", config={}, maintenance_schema_names=[])
    session.add(source)
    session.flush()

    already_deactivated = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="curiousjr",
        table_name="keystores_evolving",
        location=None,
        is_active=False,
    )
    real_sibling = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="curiousjr",
        table_name="keystores",
        location="s3a://example-prod-bucket/bronze/curiousjr/keystores_evolving",
        is_active=True,
    )
    session.add_all([already_deactivated, real_sibling])
    session.flush()

    changed = deactivate_orphaned_evolving_catalog_entries(session)

    assert changed == 0


def test_deactivate_orphaned_evolving_catalog_entries_scopes_sibling_match_to_same_schema(
    session,
) -> None:
    """A same-named table in a DIFFERENT schema must not count as a sibling -- the orphan and
    its real replacement are always in the same schema in every live case found, and treating a
    cross-schema namesake as a match would be a false-positive safety hole."""
    source = Source(id=uuid4(), name="src", type="rest", config={}, maintenance_schema_names=[])
    session.add(source)
    session.flush()

    orphan = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="schema_a",
        table_name="widgets_evolving",
        location=None,
        is_active=True,
    )
    cross_schema_namesake = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="schema_b",
        table_name="widgets",
        location="s3a://bucket/schema_b/widgets",
        is_active=True,
    )
    session.add_all([orphan, cross_schema_namesake])
    session.flush()

    changed = deactivate_orphaned_evolving_catalog_entries(session)
    session.flush()
    session.refresh(orphan)

    assert changed == 0
    assert orphan.is_active is True


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

    candidates, _errors = _discover_candidates(
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

    candidates, _errors = _discover_candidates(
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
