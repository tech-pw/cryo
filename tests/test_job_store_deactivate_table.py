"""Regression, 2026-07-31: SQLJobStore.deactivate_table -- the reconciliation write-path a
worker calls when a maintenance job discovers its table has been dropped from the catalog.
Without this, Table.is_active stays true forever and scans/maintenance keep targeting a table
that no longer exists."""

from __future__ import annotations

from uuid import uuid4

from cryo.db.models import Source, Table
from cryo.db.repositories.job_store import SQLJobStore


def test_deactivate_table_sets_is_active_false_and_records_reason(session) -> None:
    source = Source(id=uuid4(), name=f"source-{uuid4()}", type="rest", config={})
    table = Table(id=uuid4(), source_id=source.id, schema_name="ns", table_name="dropped_tbl")
    session.add_all([source, table])
    session.commit()

    store = SQLJobStore(lambda: session)
    store.deactivate_table(str(table.id), "Table not found in catalog: NoSuchTableException")

    reloaded = session.get(Table, table.id)
    assert reloaded.is_active is False
    assert reloaded.last_scan_error == "Table not found in catalog: NoSuchTableException"
    assert reloaded.last_scan_error_at is not None


def test_deactivate_table_is_a_noop_for_unknown_table_id(session) -> None:
    store = SQLJobStore(lambda: session)
    # Must not raise -- the job's own failure is already recorded regardless.
    store.deactivate_table(str(uuid4()), "irrelevant")
