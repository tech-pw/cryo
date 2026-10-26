"""#120: tables gone from the catalog are tombstoned in the registry, never on a bad listing."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from cryo.config import get_settings
from cryo.db.models import AuditLog, Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.scanner.reconcile_missing import reconcile_missing_tables
from cryo.scanner.run import ScanCandidate

SCAN_AT = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)


class NoSuchNamespaceError(Exception):
    """Same class name pyiceberg raises; matched by name, not import."""


class _Catalog:
    def __init__(
        self,
        tables: dict[str, list[str]],
        *,
        failing: set[str] = frozenset(),  # type: ignore[assignment]
        missing_namespaces: set[str] = frozenset(),  # type: ignore[assignment]
    ) -> None:
        self.tables = tables
        self.failing = set(failing)
        self.missing_namespaces = set(missing_namespaces)
        self.confirm_calls: list[str] = []

    def list_tables_in_namespace(self, namespace: str) -> list[str]:
        self.confirm_calls.append(namespace)
        if namespace in self.failing:
            raise TimeoutError("metastore timed out")
        if namespace in self.missing_namespaces:
            raise NoSuchNamespaceError(namespace)
        return list(self.tables.get(namespace, []))

    def list_namespaces(self) -> list[str]:
        return sorted(set(self.tables) - self.missing_namespaces)


@pytest.fixture()
def settings():
    return get_settings().model_copy(
        update={
            "scan_remove_missing_tables_enabled": True,
            "scan_missing_table_max_fraction": 0.2,
            "scan_missing_table_max_count": 500,
        }
    )


def _source(session, allowlist: list[str] | None = None) -> Source:
    source = Source(
        id=uuid4(),
        name=f"hms-{uuid4().hex[:6]}",
        type="hive",
        config={},
        maintenance_schema_names=allowlist or [],
    )
    session.add(source)
    session.flush()
    return source


def _register(session, source: Source, schema: str, *names: str) -> dict[str, Table]:
    rows = {}
    for name in names:
        table = Table(id=uuid4(), source_id=source.id, schema_name=schema, table_name=name)
        session.add(table)
        rows[name] = table
    session.commit()
    return rows


def _candidates(source: Source, catalog: _Catalog, listing: dict[str, list[str]]):
    return [
        ScanCandidate(source=source, catalog=catalog, schema_name=schema, table_name=name)
        for schema, names in listing.items()
        for name in names
    ]


def _fresh(session, table: Table) -> Table:
    session.expire_all()
    row = session.get(Table, table.id)
    assert row is not None
    return row


def _filler(session, source: Source, count: int = 20) -> None:
    _register(session, source, "bulk", *[f"t{i}" for i in range(count)])


def test_dropped_table_is_tombstoned_in_the_same_scan(session, settings) -> None:
    source = _source(session)
    _filler(session, source)
    rows = _register(session, source, "sales", "orders", "orders__dbt_tmp")
    listing = {"sales": ["orders"], "bulk": [f"t{i}" for i in range(20)]}
    catalog = _Catalog(listing)

    result = reconcile_missing_tables(
        session, _candidates(source, catalog, listing), scan_at=SCAN_AT, settings=settings
    )

    gone = _fresh(session, rows["orders__dbt_tmp"])
    assert gone.removed_from_catalog_at is not None
    assert gone.is_active is False
    assert gone.removed_reason == f"not_found_in_catalog: {source.name}/sales"
    assert _fresh(session, rows["orders"]).removed_from_catalog_at is None
    assert result.removed == [f"{source.name}.sales.orders__dbt_tmp"]
    audit = session.scalars(
        select(AuditLog).where(AuditLog.action == "table_removed_from_catalog")
    ).all()
    assert [row.table_id for row in audit] == [gone.id]
    # Only the schema with a missing table is re-listed to confirm.
    assert catalog.confirm_calls == ["sales"]


def test_namespace_that_fails_to_list_is_never_reconciled(session, settings) -> None:
    source = _source(session)
    _filler(session, source)
    rows = _register(session, source, "finance", "ledger")
    # Discovery swallowed the finance listing error, so it produced no candidates there,
    # and the confirmation call fails too.
    listing = {"bulk": [f"t{i}" for i in range(20)]}
    catalog = _Catalog({"finance": ["ledger"], **listing}, failing={"finance"})

    result = reconcile_missing_tables(
        session, _candidates(source, catalog, listing), scan_at=SCAN_AT, settings=settings
    )

    assert _fresh(session, rows["ledger"]).removed_from_catalog_at is None
    assert result.removed == []
    assert any("namespace_list_failed" in reason for reason in result.skipped)


def test_source_with_no_listing_is_never_reconciled(session, settings) -> None:
    source = _source(session)
    rows = _register(session, source, "sales", "orders")
    result = reconcile_missing_tables(session, [], scan_at=SCAN_AT, settings=settings)
    assert _fresh(session, rows["orders"]).removed_from_catalog_at is None
    assert result.removed == []


def test_schema_scoped_scan_reconciles_only_its_schemas(session, settings) -> None:
    source = _source(session)
    _filler(session, source)
    sales = _register(session, source, "sales", "orders", "gone")
    other = _register(session, source, "other", "gone_too")
    listing = {"sales": ["orders"], "bulk": [f"t{i}" for i in range(20)]}
    catalog = _Catalog(listing)

    reconcile_missing_tables(
        session,
        _candidates(source, catalog, listing),
        scan_at=SCAN_AT,
        settings=settings,
        schema_names={"sales"},
    )

    assert _fresh(session, sales["gone"]).removed_from_catalog_at is not None
    assert _fresh(session, other["gone_too"]).removed_from_catalog_at is None


def test_out_of_allowlist_schema_is_never_treated_as_dropped(session, settings) -> None:
    source = _source(session, allowlist=["sales", "bulk"])
    _filler(session, source)
    _register(session, source, "sales", "orders")
    hidden = _register(session, source, "private", "secret")
    listing = {"sales": ["orders"], "bulk": [f"t{i}" for i in range(20)]}
    catalog = _Catalog(listing)

    reconcile_missing_tables(
        session, _candidates(source, catalog, listing), scan_at=SCAN_AT, settings=settings
    )

    assert _fresh(session, hidden["secret"]).removed_from_catalog_at is None


def test_mass_removal_guard_removes_nothing_and_records_why(session, settings) -> None:
    source = _source(session)
    rows = _register(session, source, "sales", *[f"t{i}" for i in range(20)])
    listing = {"sales": ["t0", "t1", "t2"]}  # truncated listing: 17 of 20 missing
    catalog = _Catalog(listing)

    result = reconcile_missing_tables(
        session, _candidates(source, catalog, listing), scan_at=SCAN_AT, settings=settings
    )

    assert result.removed == []
    assert all(_fresh(session, row).removed_from_catalog_at is None for row in rows.values())
    assert any("guard" in reason for reason in result.skipped)
    aborted = session.scalars(
        select(AuditLog).where(AuditLog.action == "missing_table_reconcile_aborted")
    ).all()
    assert len(aborted) == 1


def test_absolute_count_guard(session, settings) -> None:
    source = _source(session)
    _filler(session, source, 40)
    rows = _register(session, source, "sales", "a", "b", "c")
    listing = {"bulk": [f"t{i}" for i in range(40)], "sales": ["keep"]}
    catalog = _Catalog({**listing, "sales": ["keep"]})
    capped = settings.model_copy(update={"scan_missing_table_max_count": 2})

    result = reconcile_missing_tables(
        session, _candidates(source, catalog, listing), scan_at=SCAN_AT, settings=capped
    )

    assert result.removed == []
    assert all(_fresh(session, row).removed_from_catalog_at is None for row in rows.values())


def test_schema_that_lists_empty_is_skipped_unless_the_namespace_is_gone(session, settings) -> None:
    source = _source(session)
    _filler(session, source)
    still_there = _register(session, source, "quiet", "q1")
    gone = _register(session, source, "dropped_ns", "d1")
    listing = {"bulk": [f"t{i}" for i in range(20)]}
    catalog = _Catalog({**listing, "quiet": []}, missing_namespaces={"dropped_ns"})

    result = reconcile_missing_tables(
        session, _candidates(source, catalog, listing), scan_at=SCAN_AT, settings=settings
    )

    assert _fresh(session, still_there["q1"]).removed_from_catalog_at is None
    assert any("schema_listed_empty" in reason for reason in result.skipped)
    assert _fresh(session, gone["d1"]).removed_from_catalog_at is not None


def test_queued_jobs_are_cancelled_and_running_jobs_left_alone(session, settings) -> None:
    source = _source(session)
    _filler(session, source)
    rows = _register(session, source, "sales", "orders", "gone")
    destination = Destination(id=uuid4(), name="d", engine="trino", config={})
    run = MaintenanceRun(id=uuid4(), kind="adhoc", trigger="api", status="running", summary={})
    session.add_all([destination, run])
    session.flush()
    queued = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=rows["gone"].id,
        destination_id=destination.id,
        mode="standard",
        status="queued",
    )
    deferred = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=rows["gone"].id,
        destination_id=destination.id,
        mode="standard",
        status="deferred",
    )
    running = MaintenanceJob(
        id=uuid4(),
        run_id=run.id,
        table_id=rows["gone"].id,
        destination_id=destination.id,
        mode="standard",
        status="running",
    )
    session.add_all([queued, deferred, running])
    session.commit()
    listing = {"sales": ["orders"], "bulk": [f"t{i}" for i in range(20)]}

    reconcile_missing_tables(
        session,
        _candidates(source, _Catalog(listing), listing),
        scan_at=SCAN_AT,
        settings=settings,
    )

    session.expire_all()
    assert session.get(MaintenanceJob, queued.id).status == "skipped"
    assert session.get(MaintenanceJob, deferred.id).status == "skipped"
    assert session.get(MaintenanceJob, running.id).status == "running"


def test_recreated_table_is_restored_on_the_next_scan(session, settings) -> None:
    source = _source(session)
    _filler(session, source)
    rows = _register(session, source, "sales", "orders")
    table = rows["orders"]
    table.removed_from_catalog_at = SCAN_AT
    table.removed_reason = "not_found_in_catalog: x/sales"
    table.is_active = False
    session.commit()
    listing = {"sales": ["orders"], "bulk": [f"t{i}" for i in range(20)]}

    result = reconcile_missing_tables(
        session,
        _candidates(source, _Catalog(listing), listing),
        scan_at=SCAN_AT,
        settings=settings,
    )

    restored = _fresh(session, table)
    assert restored.removed_from_catalog_at is None
    assert restored.removed_reason is None
    assert result.restored == [f"{source.name}.sales.orders"]
    assert session.scalars(
        select(AuditLog).where(AuditLog.action == "table_restored_to_catalog")
    ).all()


def test_names_match_case_insensitively(session, settings) -> None:
    source = _source(session)
    _filler(session, source)
    rows = _register(session, source, "sales", "Orders")
    listing = {"sales": ["orders"], "bulk": [f"t{i}" for i in range(20)]}

    reconcile_missing_tables(
        session,
        _candidates(source, _Catalog(listing), listing),
        scan_at=SCAN_AT,
        settings=settings,
    )

    assert _fresh(session, rows["Orders"]).removed_from_catalog_at is None


def test_disabled_setting_is_a_no_op(session, settings) -> None:
    source = _source(session)
    _filler(session, source)
    rows = _register(session, source, "sales", "gone")
    listing = {"bulk": [f"t{i}" for i in range(20)], "sales": ["other"]}
    off = settings.model_copy(update={"scan_remove_missing_tables_enabled": False})

    result = reconcile_missing_tables(
        session, _candidates(source, _Catalog(listing), listing), scan_at=SCAN_AT, settings=off
    )

    assert _fresh(session, rows["gone"]).removed_from_catalog_at is None
    assert result.removed == []


def test_successful_scan_clears_a_tombstone(session) -> None:
    from cryo.scanner.persistence import record_table_scan_outcome

    source = _source(session)
    rows = _register(session, source, "sales", "orders")
    table = rows["orders"]
    table.removed_from_catalog_at = SCAN_AT
    table.removed_reason = "not_found_in_catalog: x/sales"
    table.is_active = False
    session.commit()

    record_table_scan_outcome(
        session,
        source_id=source.id,
        schema_name="sales",
        table_name="orders",
        error=None,
        scanned_at=SCAN_AT,
    )
    session.commit()

    restored = _fresh(session, table)
    assert restored.removed_from_catalog_at is None
    assert restored.is_active is True


def test_allowlist_change_does_not_reactivate_tombstoned_tables(session) -> None:
    from cryo.db.repositories.sources import deactivate_out_of_scope_tables

    source = _source(session)
    rows = _register(session, source, "sales", "orders")
    table = rows["orders"]
    table.removed_from_catalog_at = SCAN_AT
    table.is_active = False
    session.commit()

    deactivate_out_of_scope_tables(session, source_id=source.id, maintenance_schema_names=[])
    deactivate_out_of_scope_tables(session, source_id=source.id, maintenance_schema_names=["sales"])
    session.commit()

    assert _fresh(session, table).is_active is False


# --- run_scan integration -------------------------------------------------------------


def _patch_scan(monkeypatch, candidates, errors=None):
    from cryo.scanner import run as run_mod

    monkeypatch.setattr(
        run_mod, "_discover_candidates", lambda *_a, **_k: (list(candidates), list(errors or []))
    )
    monkeypatch.setattr(run_mod, "order_by_staleness", lambda _s, c: c)
    monkeypatch.setattr(run_mod, "_run_scan_serial", lambda *_a, **_k: run_mod.ScanSummary())
    monkeypatch.setattr(run_mod, "_run_scan_parallel", lambda *_a, **_k: run_mod.ScanSummary())
    return run_mod


def test_run_scan_chunk_zero_reconciles_and_reports(session, settings, monkeypatch) -> None:
    source = _source(session)
    _filler(session, source)
    rows = _register(session, source, "sales", "orders", "stale")
    listing = {"sales": ["orders"], "bulk": [f"t{i}" for i in range(20)]}
    run_mod = _patch_scan(monkeypatch, _candidates(source, _Catalog(listing), listing))

    summary = run_mod.run_scan(
        session, settings=settings, source_id=source.id, chunk_id=0, num_chunks=2, now=SCAN_AT
    )

    assert summary.removed_from_catalog == 1
    assert summary.removed_tables == (f"{source.name}.sales.stale",)
    assert _fresh(session, rows["stale"]).removed_from_catalog_at is not None


@pytest.mark.parametrize(
    ("chunk_id", "scoped", "errors"),
    [(1, False, None), (0, True, None), (0, False, ["bulk: metastore timed out"])],
    ids=["other-chunk", "table-scoped", "discovery-errors"],
)
def test_run_scan_skips_reconcile_when_listing_is_partial(
    session, settings, monkeypatch, chunk_id, scoped, errors
) -> None:
    source = _source(session)
    _filler(session, source)
    rows = _register(session, source, "sales", "orders", "stale")
    listing = {"sales": ["orders"], "bulk": [f"t{i}" for i in range(20)]}
    run_mod = _patch_scan(monkeypatch, _candidates(source, _Catalog(listing), listing), errors)

    summary = run_mod.run_scan(
        session,
        settings=settings,
        source_id=source.id,
        table_names=[("sales", "orders")] if scoped else None,
        chunk_id=chunk_id,
        num_chunks=2,
        now=SCAN_AT,
    )

    assert summary.removed_from_catalog == 0
    assert _fresh(session, rows["stale"]).removed_from_catalog_at is None


def test_run_scan_survives_a_reconcile_crash(session, settings, monkeypatch) -> None:
    source = _source(session)
    listing = {"sales": ["orders"]}
    run_mod = _patch_scan(monkeypatch, _candidates(source, _Catalog(listing), listing))

    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(run_mod, "reconcile_missing_tables", boom)
    summary = run_mod.run_scan(session, settings=settings, source_id=source.id, now=SCAN_AT)
    assert summary.missing_table_reconcile_skipped == 1


def test_chunk_counters_roll_up_and_removed_list_is_detail_only() -> None:
    from cryo.db.repositories.scanner import _recompute_chunk_totals

    summary = {
        "chunks": {
            "0": {"status": "succeeded", "removed_from_catalog": 3, "restored_to_catalog": 1},
            "1": {"status": "succeeded", "removed_from_catalog": 0, "restored_to_catalog": 0},
        }
    }
    _recompute_chunk_totals(summary)
    assert summary["removed_from_catalog"] == 3
    assert summary["restored_to_catalog"] == 1

    from cryo.db.repositories.presets import _DETAIL_ONLY_SUMMARY_KEYS

    assert "removed_tables" in _DETAIL_ONLY_SUMMARY_KEYS


# --- API ------------------------------------------------------------------------------


def test_table_list_hides_removed_tables_unless_asked(api_db) -> None:
    from tests.auth_helpers import ADMIN_HEADERS, VIEWER_HEADERS

    created = api_db.client.post(
        "/api/v1/sources",
        headers=ADMIN_HEADERS,
        json={"name": "removed-list", "type": "rest", "config": {}},
    )
    source_id = created.json()["id"]
    from uuid import UUID

    with api_db.session_factory() as session:
        live = Table(source_id=UUID(source_id), schema_name="s", table_name="live")
        gone = Table(
            source_id=UUID(source_id),
            schema_name="s",
            table_name="gone",
            is_active=False,
            removed_from_catalog_at=SCAN_AT,
            removed_reason="not_found_in_catalog: removed-list/s",
        )
        session.add_all([live, gone])
        session.commit()
        gone_id = str(gone.id)

    default = api_db.client.get("/api/v1/tables", headers=VIEWER_HEADERS).json()
    assert [row["table_name"] for row in default["items"]] == ["live"]
    assert default["total"] == 1

    everything = api_db.client.get(
        "/api/v1/tables?include_removed=true", headers=VIEWER_HEADERS
    ).json()
    assert {row["table_name"] for row in everything["items"]} == {"live", "gone"}

    detail = api_db.client.get(f"/api/v1/tables/{gone_id}", headers=VIEWER_HEADERS).json()
    assert detail["removed_from_catalog_at"] is not None
    assert detail["removed_reason"] == "not_found_in_catalog: removed-list/s"
