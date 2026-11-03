"""#330: the provenance filter's ID list stays one array parameter on real Postgres.

An expanding ``IN`` binds one parameter per ID, and psycopg caps a statement at 65,535.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from cryo.db.models import Source, Table
from cryo.db.repositories.presets import id_in
from cryo.db.session import SessionLocal


def _postgres_available() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(), reason="requires a reachable local Postgres with migrations applied"
)


def test_id_in_binds_a_single_array_for_a_large_id_list() -> None:
    with SessionLocal() as session:
        source = Source(id=uuid.uuid4(), name=f"src-{uuid.uuid4().hex[:8]}", type="rest", config={})
        table = Table(id=uuid.uuid4(), source_id=source.id, schema_name="s", table_name="t")
        session.add(source)
        session.flush()
        session.add(table)
        session.flush()
        try:
            ids = [uuid.uuid4() for _ in range(70_000)] + [table.id]
            clause = id_in(session, Table.id, ids)
            compiled = select(Table.id).where(clause).compile(bind=session.get_bind())
            assert len(compiled.params) == 1
            assert session.scalar(select(func.count()).select_from(Table).where(clause)) == 1
            assert (
                session.scalar(
                    select(func.count()).select_from(Table).where(id_in(session, Table.id, []))
                )
                == 0
            )
        finally:
            session.rollback()
