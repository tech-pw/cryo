"""Postgres rejects two separately-built coalesce() calls in one GROUP BY (#375)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from cryo.db.models import Destination, MaintenanceJob, MaintenanceRun, Source, Table
from cryo.db.repositories.job_store import SQLJobStore
from cryo.db.session import SessionLocal
from cryo.domain.enums import JobStatus


class _Result:
    def all(self) -> list:
        return []


class _Session:
    def __init__(self) -> None:
        self.statement = None

    def execute(self, statement):
        self.statement = statement
        return _Result()


def test_in_flight_counts_uses_one_coalesce_expression() -> None:
    """The pre-fix query compiled to coalesce_1 in SELECT and coalesce_2 in GROUP BY."""
    held = _Session()

    @contextmanager
    def factory() -> Iterator[_Session]:
        yield held

    SQLJobStore(session_factory=factory).in_flight_counts()  # type: ignore[arg-type]
    compiled = held.statement.compile(dialect=postgresql.dialect())
    sql = str(compiled).lower()
    # The constant is inlined, so SELECT and GROUP BY are the same expression. Two
    # bind parameters here are what Postgres rejects.
    assert "'default'" in sql
    assert "default" not in compiled.params.values()
    assert sql.count("coalesce(cryo.jobs.replica, 'default')") == 2


def _postgres() -> bool:
    try:
        with SessionLocal() as session:
            return session.bind is not None and session.bind.dialect.name == "postgresql"
    except Exception:
        return False


@pytest.mark.skipif(not _postgres(), reason="requires Postgres")
def test_in_flight_counts_runs_on_postgres_for_null_and_named_replicas() -> None:
    with SessionLocal() as session:
        destination_id = _seed(session)
        store = SQLJobStore(session_factory=_same_session(session))
        counts = store.in_flight_counts()
        session.rollback()
    assert counts[destination_id] == {"default": 1, "spark-a": 2}


def _same_session(session: Session):
    @contextmanager
    def factory() -> Iterator[Session]:
        yield session

    return factory


def _seed(session: Session) -> str:
    marker = uuid.uuid4().hex[:8]
    source = Source(name=f"inflight-{marker}", type="rest", config={})
    destination = Destination(name=f"inflight-dest-{marker}", engine="spark_emr", config={})
    session.add_all([source, destination])
    session.flush()
    table = Table(source_id=source.id, schema_name="s", table_name=f"t-{marker}", is_active=True)
    run = MaintenanceRun(kind="scheduled", status="running", trigger="cron")
    session.add_all([table, run])
    session.flush()
    for replica, status in (
        (None, JobStatus.ADMITTED.value),
        ("spark-a", JobStatus.RUNNING.value),
        ("spark-a", JobStatus.ADMITTED.value),
        ("spark-a", JobStatus.QUEUED.value),
    ):
        session.add(
            MaintenanceJob(
                run_id=run.id,
                table_id=table.id,
                destination_id=destination.id,
                mode="standard",
                status=status,
                replica=replica,
                started_at=datetime.now(UTC),
            )
        )
    session.flush()
    # Touch the connection so a skipped import of text does not look unused in editors.
    session.execute(text("SELECT 1"))
    return str(destination.id)
