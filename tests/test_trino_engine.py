from __future__ import annotations

import threading
import time

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


def test_coordinator_omits_catalog_and_honors_jwt_and_verify() -> None:
    coordinator = coordinators_from_config(
        {
            "coordinators": ["coordinator"],
            "http_scheme": "https",
            "verify_ssl": False,
            "auth": {"type": "jwt", "token": "header.payload.sig"},
        }
    )[0]
    assert coordinator.catalog is None
    assert coordinator.verify is False
    assert coordinator.auth is not None
    assert coordinator.auth.token == "header.payload.sig"


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


def test_trino_engine_truncates_runaway_error_messages() -> None:
    """Regression, 2026-07-27: a Trino GENERIC_INTERNAL_ERROR on a 1.5 TB, 44k-file table
    serialized its full split list into the exception message -- str(exc) alone produced a
    2,962,267-character error that got stored verbatim in MaintenanceJob.error. One runaway
    upstream exception must not be able to bloat the jobs table, the audit log, and every API
    response that includes it."""
    huge_message = "GENERIC_INTERNAL_ERROR: splitsInfo already set to [" + "x" * 50_000 + "]"
    cursor = FakeCursor(fail_on="optimize")

    def failing_execute(sql: str) -> None:
        cursor.executed.append(sql)
        if "optimize" in sql:
            raise RuntimeError(huge_message)

    cursor.execute = failing_execute  # type: ignore[method-assign]

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
    assert result.error is not None
    assert len(result.error) < 4200
    assert result.error.startswith("GENERIC_INTERNAL_ERROR: splitsInfo already set to")
    assert "truncated" in result.error
    assert "50,053 chars total" in result.error or "chars total" in result.error


def test_trino_engine_logs_submission_and_completion(caplog) -> None:
    """A long-running OPTIMIZE blocks synchronously on cursor.execute() with no other output --
    without these log lines, a genuinely in-progress query is indistinguishable from a hung one
    from Cryo's side. Regression test for that observability gap."""
    cursor = FakeCursor([(1,)])

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost", "catalog": "iceberg"}, connector=connector)
    with caplog.at_level("INFO", logger="cryo.engines.trino.engine"):
        engine.execute(
            RenderedStatement(
                action=OperationType.OPTIMIZE,
                sql="ALTER TABLE iceberg.demo.t EXECUTE optimize",
                engine=EngineType.TRINO,
            ),
            ExecCtx(replica="localhost", table_id="table-1"),
        )

    messages = [record.message for record in caplog.records]
    assert any("Submitting optimize to Trino" in m and "table-1" in m for m in messages)
    assert any("Trino optimize finished" in m and "query_123" in m for m in messages)


def test_trino_engine_logs_failure(caplog) -> None:
    cursor = FakeCursor(fail_on="optimize")

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost"}, connector=connector)
    with caplog.at_level("INFO", logger="cryo.engines.trino.engine"):
        engine.execute(
            RenderedStatement(
                action=OperationType.OPTIMIZE,
                sql="ALTER TABLE iceberg.demo.t EXECUTE optimize",
                engine=EngineType.TRINO,
            )
        )

    messages = [record.message for record in caplog.records]
    assert any("Trino optimize failed" in m and "boom" in m for m in messages)


class SlowStartCursor(FakeCursor):
    """Simulates the real trino-python-client: cursor.query_id/stats stays empty until
    the query is actually submitted, then becomes available well before the statement
    finishes executing (execute() itself remains blocking). Used to prove the engine
    captures engine_ref *during* execution, not only after it returns."""

    def __init__(
        self,
        rows: list[tuple[object, ...]] | None = None,
        *,
        query_id: str = "query_slow_1",
        submit_delay: float = 0.05,
        run_time: float = 0.3,
        fail: bool = False,
    ) -> None:
        super().__init__(rows)
        self.stats = {}
        self._query_id = query_id
        self._submit_delay = submit_delay
        self._run_time = run_time
        self._fail = fail
        self.query_id_observed_before_finish = False

    def execute(self, sql: str) -> None:
        self.executed.append(sql)
        if "SET SESSION" in sql:
            return
        time.sleep(self._submit_delay)
        self.stats = {"queryId": self._query_id}
        remaining = max(self._run_time - self._submit_delay, 0)
        time.sleep(remaining)
        if self._fail:
            raise RuntimeError("boom")


def test_trino_engine_reports_engine_ref_before_query_completes() -> None:
    """Regression, 2026-09-10: a worker crash while blocked inside the old synchronous
    cursor.execute() left the job stuck at status='running', engine_ref=null forever --
    recurred twice this session on demo.warehouse.gold_events, each requiring a manual
    cancel since nothing could recover it. The fix must call ctx.on_engine_ref as soon as
    the query_id is known, well before the statement finishes -- this test fails against
    the old implementation (on_engine_ref would only ever fire after execute() returned,
    i.e. never mid-flight, since the whole call was synchronous)."""
    cursor = SlowStartCursor(rows=[(1,)], query_id="query_early", submit_delay=0.05, run_time=0.3)

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost", "catalog": "iceberg"}, connector=connector)

    observed: list[tuple[str, float]] = []
    lock = threading.Lock()
    start = time.monotonic()

    def on_engine_ref(ref: str) -> None:
        with lock:
            observed.append((ref, time.monotonic() - start))

    result = engine.execute(
        RenderedStatement(
            action=OperationType.OPTIMIZE,
            sql="ALTER TABLE iceberg.demo.t EXECUTE optimize",
            engine=EngineType.TRINO,
        ),
        ExecCtx(replica="localhost", on_engine_ref=on_engine_ref),
    )

    assert result.ok is True
    assert result.engine_ref == "query_early"
    assert len(observed) == 1
    ref, observed_at = observed[0]
    assert ref == "query_early"
    # Must be reported while the statement is still running, not after the ~0.3s total.
    assert observed_at < 0.25


def test_trino_engine_reports_engine_ref_even_on_failure() -> None:
    """A query that fails after its queryId is known must still have reported that
    queryId via on_engine_ref -- a crash between capture and failure shouldn't lose it."""
    cursor = SlowStartCursor(
        rows=[], query_id="query_will_fail", submit_delay=0.02, run_time=0.1, fail=True
    )

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost"}, connector=connector)
    observed: list[str] = []

    result = engine.execute(
        RenderedStatement(
            action=OperationType.OPTIMIZE,
            sql="ALTER TABLE iceberg.demo.t EXECUTE optimize",
            engine=EngineType.TRINO,
        ),
        ExecCtx(on_engine_ref=observed.append),
    )

    assert result.ok is False
    assert result.engine_ref == "query_will_fail"
    assert observed == ["query_will_fail"]


class FilesDiffCursor(FakeCursor):
    """Fake cursor that answers a $files byte-sum SELECT with values from a queue, and any
    other statement (session setup, the maintenance ALTER TABLE) with `rows`. Lets tests
    control the before/after byte totals independently of the main statement's own result."""

    def __init__(
        self,
        rows: list[tuple[object, ...]] | None = None,
        *,
        files_sums: list[int | None] | None = None,
    ) -> None:
        super().__init__(rows)
        self._files_sums = list(files_sums or [])
        self._last_was_files_query = False

    def execute(self, sql: str) -> None:
        self.executed.append(sql)
        self._last_was_files_query = '"$files"' in sql
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("boom")

    def fetchone(self) -> tuple[object, ...] | None:
        if self._last_was_files_query:
            value = self._files_sums.pop(0) if self._files_sums else None
            return (value,)
        return self.rows[0] if self.rows else None


def test_trino_engine_measures_bytes_freed_via_files_diff_for_optimize() -> None:
    cursor = FilesDiffCursor([(3, 1)], files_sums=[1_000_000_000, 400_000_000])

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost", "catalog": "iceberg"}, connector=connector)
    result = engine.execute(
        RenderedStatement(
            action=OperationType.OPTIMIZE,
            sql="ALTER TABLE iceberg.demo.t EXECUTE optimize",
            engine=EngineType.TRINO,
            metadata={"table_ref": "iceberg.demo.t"},
        ),
        ExecCtx(replica="localhost"),
    )

    assert result.ok is True
    assert result.actual_bytes_freed == 600_000_000
    files_queries = [sql for sql in cursor.executed if '"$files"' in sql]
    assert len(files_queries) == 2
    assert all(
        sql.startswith("SELECT COALESCE(SUM(file_size_in_bytes), 0) FROM")
        for sql in files_queries
    )


def test_trino_engine_clamps_negative_files_diff_to_zero() -> None:
    """A concurrent writer landing between the before/after $files reads could make the after
    total exceed the before total; must not report negative bytes freed."""
    cursor = FilesDiffCursor([(1, 1)], files_sums=[500_000_000, 900_000_000])

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost", "catalog": "iceberg"}, connector=connector)
    result = engine.execute(
        RenderedStatement(
            action=OperationType.EXPIRE_SNAPSHOTS,
            sql="ALTER TABLE iceberg.demo.t EXECUTE expire_snapshots(retention_threshold => '7d')",
            engine=EngineType.TRINO,
            metadata={"table_ref": "iceberg.demo.t"},
        ),
    )

    assert result.ok is True
    assert result.actual_bytes_freed == 0


def test_trino_engine_leaves_bytes_freed_unmeasured_for_remove_orphan_files() -> None:
    """remove_orphan_files deletes files that are, by definition, absent from $files -- a
    $files diff is always zero for this operation and would be a silent wrong answer, not a
    measurement. Must stay None (honestly unmeasured), not report a fabricated 0."""
    cursor = FilesDiffCursor([(1,)], files_sums=[999_999_999, 999_999_999])

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost", "catalog": "iceberg"}, connector=connector)
    result = engine.execute(
        RenderedStatement(
            action=OperationType.REMOVE_ORPHAN_FILES,
            sql=(
                "ALTER TABLE iceberg.demo.t "
                "EXECUTE remove_orphan_files(retention_threshold => '7d')"
            ),
            engine=EngineType.TRINO,
            metadata={"table_ref": "iceberg.demo.t"},
        ),
    )

    assert result.ok is True
    assert result.actual_bytes_freed is None
    assert not any('"$files"' in sql for sql in cursor.executed)


def test_trino_engine_bytes_freed_none_when_files_query_fails() -> None:
    """A $files probe failure (permissions, connector quirk, transient error) must degrade to
    unmeasured, never raise and never fail the maintenance statement itself."""
    cursor = FilesDiffCursor([(2, 1)], files_sums=[None, 500_000_000])

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost", "catalog": "iceberg"}, connector=connector)
    result = engine.execute(
        RenderedStatement(
            action=OperationType.OPTIMIZE,
            sql="ALTER TABLE iceberg.demo.t EXECUTE optimize",
            engine=EngineType.TRINO,
            metadata={"table_ref": "iceberg.demo.t"},
        ),
    )

    assert result.ok is True
    assert result.actual_bytes_freed is None


def test_trino_engine_bytes_freed_none_without_table_ref_metadata() -> None:
    """A RenderedStatement missing table_ref metadata (e.g. built by hand in a test, or an
    older caller) must not attempt the $files query at all."""
    cursor = FilesDiffCursor([(1, 1)])

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost", "catalog": "iceberg"}, connector=connector)
    result = engine.execute(
        RenderedStatement(
            action=OperationType.OPTIMIZE,
            sql="ALTER TABLE iceberg.demo.t EXECUTE optimize",
            engine=EngineType.TRINO,
        ),
    )

    assert result.ok is True
    assert result.actual_bytes_freed is None
    assert not any('"$files"' in sql for sql in cursor.executed)


def test_trino_engine_works_without_on_engine_ref_callback() -> None:
    """ExecCtx with no on_engine_ref (or ctx=None entirely) must not break -- most callers
    historically don't pass one."""
    cursor = FakeCursor([(1,)])

    def connector(_coordinator):
        return FakeConnection(cursor)

    engine = TrinoEngine({"host": "localhost", "catalog": "iceberg"}, connector=connector)
    result = engine.execute(
        RenderedStatement(
            action=OperationType.OPTIMIZE,
            sql="ALTER TABLE iceberg.demo.t EXECUTE optimize",
            engine=EngineType.TRINO,
        )
    )

    assert result.ok is True
    assert result.engine_ref == "query_123"
