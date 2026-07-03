from __future__ import annotations

from cryo.domain.enums import EngineType, OperationType
from cryo.engines.base import ExecCtx, RenderedStatement
from cryo.engines.trino.client import coordinators_from_config
from cryo.engines.trino.engine import TrinoEngine
from cryo.engines.trino.load import probe_capacity


class FakeCursor:
    def __init__(
        self, rows: list[tuple[object, ...]] | None = None, fail_on: str | None = None
    ) -> None:
        self.rows = rows or []
        self.fail_on = fail_on
        self.executed: list[str] = []
        self.stats = {"queryId": "query_123"}
        self.closed = False

    def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("boom")

    def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def test_coordinators_from_config_supports_shared_defaults() -> None:
    coordinators = coordinators_from_config(
        {
            "coordinators": ["replica-a", {"host": "replica-b", "port": 8443}],
            "port": 8081,
            "user": "maintainer",
            "catalog": "iceberg",
        }
    )

    assert [coordinator.host for coordinator in coordinators] == ["replica-a", "replica-b"]
    assert [coordinator.port for coordinator in coordinators] == [8081, 8443]
    assert all(coordinator.user == "maintainer" for coordinator in coordinators)


def test_probe_capacity_sums_live_optimizes() -> None:
    cursors = [FakeCursor([(2,)]), FakeCursor([(1,)])]

    def connector(_coordinator):
        return FakeConnection(cursors.pop(0))

    snapshot = probe_capacity(
        coordinators_from_config({"coordinators": ["a", "b"]}),
        per_replica_cap=5,
        connector=connector,
    )

    assert snapshot.capacity == 10
    assert snapshot.used == 3
    assert snapshot.available == 7


def test_trino_engine_executes_session_setup_and_statement() -> None:
    cursor = FakeCursor([(1,)])

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost", "catalog": "iceberg"}, connector=connector)
    result = engine.execute(
        RenderedStatement(
            action=OperationType.OPTIMIZE,
            sql="ALTER TABLE iceberg.demo.t EXECUTE optimize",
            engine=EngineType.TRINO,
        ),
        ExecCtx(replica="localhost"),
    )

    assert result.ok is True
    assert result.engine_ref == "query_123"
    assert cursor.executed[:2] == [
        "SET SESSION task_min_writer_count = 8",
        "SET SESSION writer_scaling_min_data_processed = '32MB'",
    ]
    assert cursor.executed[-1] == "ALTER TABLE iceberg.demo.t EXECUTE optimize"


def test_trino_engine_captures_execution_errors() -> None:
    cursor = FakeCursor(fail_on="optimize")

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost"}, connector=connector)
    result = engine.execute(
        RenderedStatement(
            action=OperationType.OPTIMIZE,
            sql="ALTER TABLE iceberg.demo.t EXECUTE optimize",
            engine=EngineType.TRINO,
        )
    )

    assert result.ok is False
    assert result.engine_ref == "query_123"
    assert result.error == "boom"
