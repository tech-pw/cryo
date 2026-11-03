"""#330 on real Postgres: the FK migration's result, its downgrade, and a cascading purge."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text

from cryo.db.models import AuditLog, Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.removed_table_purge import TABLE_PURGED_ACTION, purge_removed_tables
from cryo.db.session import SessionLocal

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20261103_1200_purge_removed_tables_fk_ondelete.py"
)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("purge_fk_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_applied() -> bool:
    """CI migrates to head first; a local database still behind this revision is skipped."""
    try:
        with SessionLocal() as session:
            return bool(
                session.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_description d "
                        "JOIN pg_constraint c ON c.oid = d.objoid WHERE d.description = :m)"
                    ),
                    {"m": _migration().MARKER},
                ).scalar()
            )
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _migration_applied(), reason="requires Postgres migrated to 20261103_1200 or later"
)

_FKS = text(
    """
    SELECT child.relname, a.attname, parent.relname, c.confdeltype, c.convalidated,
           obj_description(c.oid, 'pg_constraint')
    FROM pg_constraint c
    JOIN pg_class child ON child.oid = c.conrelid
    JOIN pg_namespace cn ON cn.oid = child.relnamespace
    JOIN pg_class parent ON parent.oid = c.confrelid
    JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
    WHERE c.contype = 'f' AND cn.nspname = 'cryo'
      AND parent.relname IN ('tables', 'jobs', 'maintenance_intents')
    """
)


def _actions(connection) -> dict[tuple[str, str, str], tuple[str, bool, str | None]]:
    return {
        (child, column, parent): (action, validated, comment)
        for child, column, parent, action, validated, comment in connection.execute(_FKS)
    }


def test_no_foreign_key_to_tables_jobs_or_intents_is_left_without_an_action() -> None:
    with SessionLocal() as session:
        actions = _actions(session.connection())
    assert actions
    assert {key: value for key, value in actions.items() if value[0] == "a"} == {}
    assert all(validated for _action, validated, _comment in actions.values())
    expected = {
        ("jobs", "table_id", "tables"): "c",
        ("maintenance_intents", "table_id", "tables"): "c",
        ("lineage_edges", "source_table_id", "tables"): "c",
        ("audit_log", "table_id", "tables"): "n",
        ("audit_log", "job_id", "jobs"): "n",
        ("query_logs", "table_id", "tables"): "n",
        ("run_failure_diagnoses", "job_id", "jobs"): "n",
        ("jobs", "current_intent_id", "maintenance_intents"): "n",
    }
    for key, action in expected.items():
        assert actions[key][0] == action, key


def test_purge_indexes_exist_and_are_valid() -> None:
    names = [name for name, _table, _column in _migration().INDEXES]
    with SessionLocal() as session:
        rows = session.execute(
            text(
                "SELECT c.relname, i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'cryo' AND c.relname = ANY(:names)"
            ),
            {"names": names},
        ).all()
    assert dict(rows) == {name: True for name in names}


def test_downgrade_reverts_exactly_the_tagged_constraints_and_upgrade_restores_them() -> None:
    migration = _migration()
    with SessionLocal() as session:
        connection = session.connection()
        before = _actions(connection)
        tagged = {key for key, (_a, _v, comment) in before.items() if comment == migration.MARKER}
        assert ("jobs", "table_id", "tables") in tagged
        try:
            connection.execute(text(migration.DOWNGRADE_SQL))
            connection.execute(
                text(migration.validate_sql(migration.REVERTED_MARKER, clear_comment=True))
            )
            reverted = _actions(connection)
            for key in tagged:
                assert reverted[key] == ("a", True, None), key
            untouched = set(before) - tagged
            assert {key: reverted[key] for key in untouched} == {
                key: before[key] for key in untouched
            }

            connection.execute(text(migration.UPGRADE_SQL))
            connection.execute(text(migration.validate_sql(migration.MARKER, clear_comment=False)))
            assert _actions(connection) == before
        finally:
            session.rollback()


def test_purge_cascades_on_postgres_and_keeps_the_audit_trail() -> None:
    source_id, destination_id, run_id, table_id = (uuid.uuid4() for _ in range(4))
    removed_at = datetime.now(UTC) - timedelta(days=60)
    with SessionLocal() as session:
        session.add(Source(id=source_id, name=f"lake-{source_id.hex[:8]}", type="rest", config={}))
        session.add(
            Destination(
                id=destination_id, name=f"d-{destination_id.hex[:8]}", engine="trino", config={}
            )
        )
        session.add(MaintenanceRun(id=run_id, kind="maintenance", status="succeeded"))
        session.flush()
        session.add(
            Table(
                id=table_id,
                source_id=source_id,
                schema_name="sales",
                table_name="gone",
                is_active=False,
                removed_from_catalog_at=removed_at,
            )
        )
        session.flush()
        job_id = uuid.uuid4()
        session.add(
            MaintenanceJob(
                id=job_id,
                run_id=run_id,
                table_id=table_id,
                destination_id=destination_id,
                mode="standard",
                status="succeeded",
                finished_at=removed_at,
            )
        )
        session.flush()
        audit = AuditLog(table_id=table_id, job_id=job_id, action="optimize", status="OK")
        session.add(audit)
        session.commit()
        audit_id = audit.id

    try:
        with SessionLocal() as session:
            # Bounded to this test's table: other tests may leave old tombstones behind.
            result = purge_removed_tables(
                session, 30, now=removed_at + timedelta(days=31, seconds=1), max_tables=1000
            )
            assert result.failed == 0
            assert session.get(Table, table_id) is None
            assert session.get(MaintenanceJob, job_id) is None
            kept = session.get(AuditLog, audit_id)
            assert kept is not None and kept.table_id is None and kept.job_id is None
            purged = session.execute(
                text("SELECT count(*) FROM cryo.audit_log WHERE action = :a AND detail LIKE :d"),
                {"a": TABLE_PURGED_ACTION, "d": f"%table_id={table_id}%"},
            ).scalar_one()
            assert purged == 1
    finally:
        with SessionLocal() as session:
            session.execute(text("DELETE FROM cryo.audit_log WHERE id = :i"), {"i": audit_id})
            session.execute(
                text("DELETE FROM cryo.audit_log WHERE detail LIKE :d"),
                {"d": f"%table_id={table_id}%"},
            )
            session.execute(text("DELETE FROM cryo.jobs WHERE id = :j"), {"j": job_id})
            session.execute(text("DELETE FROM cryo.tables WHERE id = :t"), {"t": table_id})
            session.execute(text("DELETE FROM cryo.runs WHERE id = :r"), {"r": run_id})
            session.execute(text("DELETE FROM cryo.sources WHERE id = :s"), {"s": source_id})
            session.execute(
                text("DELETE FROM cryo.destinations WHERE id = :d"), {"d": destination_id}
            )
            session.commit()
