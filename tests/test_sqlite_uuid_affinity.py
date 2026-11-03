"""Regression test: sqlite must store Postgres UUIDs as text, never as a number."""

import uuid

from sqlalchemy import Column, MetaData, Table, create_engine, select
from sqlalchemy.dialects.postgresql import UUID


def test_numeric_looking_uuid_round_trips_on_sqlite() -> None:
    # Importing conftest (pytest does this) registers the sqlite UUID compile rule.
    engine = create_engine("sqlite://")
    metadata = MetaData()
    table = Table("t", metadata, Column("id", UUID(as_uuid=True), primary_key=True))
    metadata.create_all(engine)
    value = uuid.UUID("12345678123412341234123456789e12")
    with engine.begin() as conn:
        conn.execute(table.insert().values(id=value))
        assert conn.exec_driver_sql("SELECT typeof(id) FROM t").scalar_one() == "text"
        assert conn.execute(select(table.c.id)).scalar_one() == value
