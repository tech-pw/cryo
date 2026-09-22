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

import pytest

from cryo.db.models import Source, Table
from cryo.scanner.run import _discover_candidates, _list_candidate_tables, run_scan


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

    candidates, errors = _discover_candidates(
        session,
        source_id=source.id,
        table_names=[("ai_grader", "gold_tests")],
        source_factory=lambda _source: catalog,
    )

    assert [(c.schema_name, c.table_name) for c in candidates] == [("ai_grader", "gold_tests")]
    assert errors == []


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

    candidates, _errors = _discover_candidates(
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


class _FailingCatalog:
    """Simulates a broken catalog connection (bad credentials, bad uri, metastore down, ...)."""

    def list_tables(self) -> list[tuple[str, str]]:
        raise KeyError("uri")


def test_discover_candidates_reports_errors_instead_of_swallowing_them(session) -> None:
    source = _seed_source(session)

    candidates, errors = _discover_candidates(
        session,
        source_id=source.id,
        source_factory=lambda _source: _FailingCatalog(),
    )

    assert candidates == []
    assert len(errors) == 1
    assert "uri" in errors[0]


def test_run_scan_raises_when_source_scoped_discovery_fails(session) -> None:
    """A source-scoped scan chunk (every queued scan chunk is) that hits a total catalog
    connection failure must surface as a failure, not silently report "0 tables, succeeded" --
    this is the exact bug that let a broken cdp source config look like a clean scan."""
    source = _seed_source(session)

    with pytest.raises(RuntimeError, match="uri"):
        run_scan(
            session,
            source_id=source.id,
            chunk_id=0,
            num_chunks=1,
            source_factory=lambda _source: _FailingCatalog(),
        )


def test_table_scoped_discovery_never_touches_a_source_it_did_not_ask_for(session) -> None:
    """A scan scoped to specific table_ids must only connect to the sources owning those
    tables. Prod, 2026-09-23: a five-table scan of `cdp` tables sat at scanned=0 for over five
    minutes because discovery also walked cdp_dev, whose metastore is unreachable and whose
    three A records each burned a full TCP connect timeout in turn."""
    wanted = _seed_source(session)
    unrelated = _seed_source(session)
    table = Table(
        id=uuid4(),
        source_id=wanted.id,
        schema_name="ns",
        table_name="t1",
        is_active=True,
    )
    session.add(table)
    session.flush()

    visited: list[str] = []

    def factory(source: Source):
        visited.append(source.name)
        if source.id == unrelated.id:
            raise AssertionError(f"connected to {source.name}, which owns none of the tables")
        return _FakeCatalog({"ns": ["t1"]})

    candidates, errors = _discover_candidates(
        session, source_id=None, table_ids=[table.id], source_factory=factory
    )

    assert visited == [wanted.name]
    assert [(c.schema_name, c.table_name) for c in candidates] == [("ns", "t1")]
    assert errors == []


def test_an_unreachable_unrelated_source_does_not_fail_a_table_scoped_scan(session) -> None:
    """The same scenario end to end: the broken source must not even be reached, so the scan
    reports a clean success rather than a discovery error it could do nothing about."""
    wanted = _seed_source(session)
    broken = _seed_source(session)
    table = Table(
        id=uuid4(), source_id=wanted.id, schema_name="ns", table_name="t1", is_active=True
    )
    session.add(table)
    session.flush()

    catalogs = {wanted.id: _FakeCatalog({"ns": ["t1"]}), broken.id: _FailingCatalog()}
    summary = run_scan(
        session,
        source_id=None,
        table_ids=[table.id],
        chunk_id=0,
        num_chunks=1,
        source_factory=lambda source: catalogs[source.id],
    )

    assert summary.discovered == 1


def test_run_scan_all_sources_tolerates_one_broken_source(session) -> None:
    """Without an explicit source_id (scanning every source), one broken source shouldn't abort
    discovery for the others -- only a source-scoped chunk request should hard-fail."""
    healthy = _seed_source(session)
    broken = _seed_source(session)
    catalogs = {
        healthy.id: _FakeCatalog({"ns": ["t1"]}),
        broken.id: _FailingCatalog(),
    }

    summary = run_scan(
        session,
        source_id=None,
        chunk_id=0,
        num_chunks=1,
        source_factory=lambda source: catalogs[source.id],
    )

    assert summary.discovered == 1
