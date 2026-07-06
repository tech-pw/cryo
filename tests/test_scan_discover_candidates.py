"""Tests for cryo/scanner/run.py::_discover_candidates's table_names targeting.

Covers the real gap this closed: "Specific tables" scan scope previously required a table to
already have a Cryo-assigned id (from a prior scan) before it could be targeted individually --
even though the live catalog already knows its name. table_names lets a never-before-scanned
table be targeted directly by (schema_name, table_name), and _list_candidate_tables should use
the cheap per-namespace listing instead of the full catalog walk whenever specific schemas are
already known.
"""

from __future__ import annotations

from uuid import uuid4

from cryo.db.models import Source, Table
from cryo.scanner.run import _discover_candidates, _list_candidate_tables


class _FakeCatalog:
    """Records which listing method was called, to prove the narrow path is actually used."""

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

    def load_table(self, schema: str, table: str) -> None:  # pragma: no cover - unused here
        raise NotImplementedError


def _seed_source(session) -> Source:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={"uri": "x"})
    session.add(source)
    session.flush()
    return source


def test_table_names_targets_a_table_with_no_prior_scan(session) -> None:
    """The whole point: a table that has never been scanned (no Table row/id exists) must still
    be selectable via table_names -- this previously required table_ids, which by definition
    can't reference a table Cryo has never seen."""
    source = _seed_source(session)
    catalog = _FakeCatalog({"ai_grader": ["gold_tests", "gold_submissions"], "other_schema": ["x"]})

    candidates = _discover_candidates(
        session,
        source_id=source.id,
        table_names=[("ai_grader", "gold_tests")],
        source_factory=lambda _source: catalog,
    )

    assert [(c.schema_name, c.table_name) for c in candidates] == [("ai_grader", "gold_tests")]


def test_table_names_uses_narrow_per_namespace_listing_not_full_walk(session) -> None:
    source = _seed_source(session)
    catalog = _FakeCatalog({"ai_grader": ["gold_tests"], "other_schema": ["x", "y", "z"]})

    _discover_candidates(
        session,
        source_id=source.id,
        table_names=[("ai_grader", "gold_tests")],
        source_factory=lambda _source: catalog,
    )

    assert catalog.list_tables_calls == 0
    assert catalog.list_tables_in_namespace_calls == ["ai_grader"]


def test_table_names_and_table_ids_can_combine(session) -> None:
    source = _seed_source(session)
    table = Table(
        id=uuid4(),
        source_id=source.id,
        schema_name="ai_grader",
        table_name="already_scanned",
        is_active=True,
    )
    session.add(table)
    session.flush()
    catalog = _FakeCatalog({"ai_grader": ["already_scanned", "gold_tests"], "other_schema": ["x"]})

    candidates = _discover_candidates(
        session,
        source_id=source.id,
        table_ids=[table.id],
        table_names=[("ai_grader", "gold_tests")],
        source_factory=lambda _source: catalog,
    )

    found = {(c.schema_name, c.table_name) for c in candidates}
    assert found == {("ai_grader", "already_scanned"), ("ai_grader", "gold_tests")}


def test_table_names_without_source_id_is_ignored() -> None:
    """table_names is only meaningful scoped to one source (see _validate_scan_scope, which
    requires source_id whenever table_names is used) -- without it, name-only refs can't be
    resolved to a (source_id, schema, table) tuple, so they're silently dropped rather than
    matched against every source."""
    catalog = _FakeCatalog({"ai_grader": ["gold_tests"]})
    candidates = _list_candidate_tables(catalog, {"ai_grader"})
    assert candidates == [("ai_grader", "gold_tests")]


def test_list_candidate_tables_falls_back_to_full_walk_without_schema_hint() -> None:
    catalog = _FakeCatalog({"a": ["t1"], "b": ["t2"]})
    result = _list_candidate_tables(catalog, set())
    assert catalog.list_tables_calls == 1
    assert set(result) == {("a", "t1"), ("b", "t2")}
