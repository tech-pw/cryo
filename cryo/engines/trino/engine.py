from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cryo.config import Settings, get_settings
from cryo.domain.enums import EngineType, OperationType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement, truncate_error_message
from cryo.engines.trino.client import TrinoCoordinator, connect, coordinators_from_config
from cryo.engines.trino.load import probe_capacity as probe_trino_capacity
from cryo.engines.trino.sql import render, session_setup

logger = logging.getLogger(__name__)

_SQL_LOG_TRUNCATE = 200

# Operations that remove or rewrite files still referenced by the table's current snapshot --
# their effect is visible as a live-file byte-sum delta on Iceberg's `$files` metadata table,
# queryable before and after the statement with no extra privileges beyond what the statement
# itself already needed. remove_orphan_files is deliberately excluded: orphan files are by
# definition *not* in `$files` (that's what makes them orphans), so a $files diff is always
# zero for that operation regardless of how much was actually deleted on S3 -- reporting 0
# there would be a silent wrong answer, not an honest "unmeasured". optimize_manifests and
# compute_statistics don't touch data files at all.
_BYTES_MEASURABLE_VIA_FILES_DIFF = frozenset(
    {
        OperationType.EXPIRE_SNAPSHOTS,
        OperationType.OPTIMIZE,
        OperationType.CONVERT_DELETES,
        OperationType.EXPIRE_DATA,
    }
)


@dataclass
class TrinoEngine:
    config: dict[str, Any]
    settings: Settings | None = None
    connector: Callable[[TrinoCoordinator], Any] = connect

    type: EngineType = EngineType.TRINO

    def __post_init__(self) -> None:
        self.settings = self.settings or get_settings()
        self.coordinators = coordinators_from_config(self.config)
        self.per_replica_cap = int(
            self.config.get("per_replica_cap")
            or self.config.get("target_concurrency")
            or self.settings.default_per_replica_optimize_cap
        )

    def probe_capacity(self) -> CapacitySnapshot:
        return probe_trino_capacity(self.coordinators, self.per_replica_cap, self.connector)

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]:
        return render(plan)

    def session_setup(self) -> list[str]:
        return session_setup()

    def execute_plan(self, plan: MaintenancePlan, ctx: ExecCtx | None = None) -> list[ExecResult]:
        """Execute each rendered statement in order, short-circuiting on failure."""
        results: list[ExecResult] = []
        for stmt in self.render(plan):
            result = self.execute(stmt, ctx)
            results.append(result)
            if not result.ok:
                break
        return results

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        coordinator = self._coordinator(ctx)
        start = time.monotonic()
        connection: Any = self.connector(coordinator)
        cursor: Any = connection.cursor()
        engine_ref: str | None = None
        table_id = getattr(ctx, "table_id", None)
        on_engine_ref = getattr(ctx, "on_engine_ref", None)
        # cursor.execute() below blocks synchronously until Trino finishes the statement --
        # for a large OPTIMIZE that can be many minutes with zero output otherwise, making a
        # genuinely in-progress query indistinguishable from a hung one from Cryo's side.
        logger.info(
            "Submitting %s to Trino coordinator=%s table_id=%s sql=%s",
            stmt.action.value,
            coordinator.label,
            table_id,
            stmt.sql[:_SQL_LOG_TRUNCATE],
        )
        table_ref_raw = stmt.metadata.get("table_ref")
        table_ref = table_ref_raw if isinstance(table_ref_raw, str) else None
        measure_bytes = stmt.action in _BYTES_MEASURABLE_VIA_FILES_DIFF and table_ref is not None
        try:
            for setup_sql in self._statement_session_setup(stmt, coordinator):
                cursor.execute(setup_sql)
            bytes_before = (
                _live_file_bytes(cursor, table_ref) if measure_bytes and table_ref else None
            )
            # Regression, confirmed live 2026-09-09/10: a worker crash (OOM, pod eviction)
            # while blocked inside the synchronous cursor.execute() below left the job
            # permanently stuck at status='running', engine_ref=null forever -- the trino
            # client's own TrinoQuery.execute() (cryo/engines/trino/client.py's underlying
            # lib) sets cursor.query_id from the *first* HTTP response, well before the
            # polling loop that blocks for actual completion, but that assignment happens
            # deep inside the same synchronous call this function makes, so there was no
            # earlier point to capture it from without changing execution shape. Recurred
            # twice on catalog.schema.example_table (15h+, then 23h+) before this fix --
            # each time required a manual /runs/{id}/cancel, and nothing could auto-recover
            # it (unlike the Spark path, which has a real K8s Job to probe). Run the
            # blocking call on a background thread and poll cursor.query_id from here; as
            # soon as it's non-None, persist it via ctx.on_engine_ref immediately -- the
            # exact same callback worker.py already wires through for every other engine,
            # so a crash after this point leaves a real engine_ref to recover/probe from.
            statement_thread_exc: list[BaseException] = []

            def _run_statement() -> None:
                try:
                    cursor.execute(stmt.sql)
                except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread below
                    statement_thread_exc.append(exc)

            worker_thread = threading.Thread(
                target=_run_statement, name="trino-execute", daemon=True
            )
            worker_thread.start()
            reported_ref = False
            while worker_thread.is_alive():
                if not reported_ref and on_engine_ref is not None:
                    ref = _query_id(cursor)
                    if ref:
                        on_engine_ref(ref)
                        reported_ref = True
                worker_thread.join(timeout=0.2)
            if statement_thread_exc:
                if not reported_ref and on_engine_ref is not None:
                    ref = _query_id(cursor)
                    if ref:
                        on_engine_ref(ref)
                        reported_ref = True
                raise statement_thread_exc[0]
            rows = tuple(tuple(row) for row in (cursor.fetchall() or ()))
            engine_ref = _query_id(cursor)
            if not reported_ref and on_engine_ref is not None and engine_ref:
                on_engine_ref(engine_ref)
            logger.info(
                "Trino %s finished coordinator=%s table_id=%s query_id=%s duration_ms=%d",
                stmt.action.value,
                coordinator.label,
                table_id,
                engine_ref,
                _elapsed_ms(start),
            )

            # Extract file counts for OPTIMIZE operations
            input_files = None
            output_files = None
            if stmt.action.value == "optimize" and rows:
                # Trino OPTIMIZE returns: (rewritten_data_files_count, added_data_files_count, ...)
                # rewritten_data_files_count = input files compacted
                # added_data_files_count = output files created
                try:
                    if len(rows[0]) >= 2:
                        input_files = int(rows[0][0]) if rows[0][0] is not None else None
                        output_files = int(rows[0][1]) if rows[0][1] is not None else None
                except (ValueError, IndexError, TypeError):
                    pass

            actual_bytes_freed = None
            if bytes_before is not None and table_ref:
                bytes_after = _live_file_bytes(cursor, table_ref)
                if bytes_after is not None:
                    # Clamp at zero: OPTIMIZE/expire_snapshots only remove/rewrite files, they
                    # never add net live bytes, but a concurrent writer landing between the
                    # before- and after- $files reads could otherwise produce a negative delta.
                    actual_bytes_freed = max(bytes_before - bytes_after, 0)

            return ExecResult(
                ok=True,
                engine_ref=engine_ref,
                detail=stmt.sql,
                rows=rows,
                duration_ms=_elapsed_ms(start),
                input_files_count=input_files,
                output_files_count=output_files,
                actual_bytes_freed=actual_bytes_freed,
            )
        except Exception as exc:
            engine_ref = _query_id(cursor)
            error_message = truncate_error_message(str(exc))
            logger.warning(
                "Trino %s failed coordinator=%s table_id=%s query_id=%s duration_ms=%d error=%s",
                stmt.action.value,
                coordinator.label,
                table_id,
                engine_ref,
                _elapsed_ms(start),
                error_message,
            )
            return ExecResult(
                ok=False,
                outcome_known=False,
                engine_ref=engine_ref,
                detail=stmt.sql,
                error=error_message,
                duration_ms=_elapsed_ms(start),
            )
        finally:
            cursor.close()
            connection.close()

    def _statement_session_setup(
        self, stmt: RenderedStatement, coordinator: TrinoCoordinator
    ) -> list[str]:
        """Engine-wide session setup, then the statement's own catalog session properties.

        Catalog properties are qualified by the catalog the statement targets: the first part
        of a three-part table_ref, else the coordinator's default catalog. With neither, they
        are left out and Trino applies its own floor, which is what happened before #139.
        """
        setup = list(self.session_setup())
        properties = stmt.metadata.get("catalog_session_properties")
        if not isinstance(properties, dict) or not properties:
            return setup
        table_ref = stmt.metadata.get("table_ref")
        catalog = _catalog_of(table_ref) if isinstance(table_ref, str) else None
        catalog = catalog or coordinator.catalog
        if not catalog:
            logger.warning(
                "No catalog for %s; leaving Trino's default retention floor in place",
                stmt.action.value,
            )
            return setup
        qualifier = _session_identifier(catalog)
        for name, value in properties.items():
            escaped = str(value).replace("'", "''")
            setup.append(f"SET SESSION {qualifier}.{_session_identifier(str(name))} = '{escaped}'")
        return setup

    def _coordinator(self, ctx: ExecCtx | None) -> TrinoCoordinator:
        replica = getattr(ctx, "replica", None)
        if replica:
            for coordinator in self.coordinators:
                if replica in {coordinator.label, coordinator.host}:
                    return coordinator
        return self.coordinators[0]


def _live_file_bytes(cursor: Any, table_ref: str) -> int | None:
    """Sum of file_size_in_bytes over a table's currently-referenced data/delete files.

    Best-effort: any failure (unsupported connector, permission gap, transient error) returns
    None rather than raising -- a byte-measurement side query must never fail the maintenance
    operation itself. None is later treated the same as "couldn't measure", same as if this
    weren't attempted at all.
    """
    try:
        cursor.execute(f'SELECT COALESCE(SUM(file_size_in_bytes), 0) FROM {table_ref}."$files"')
        row = cursor.fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])
    except Exception:  # noqa: BLE001 - measurement is best-effort, never fatal
        logger.debug("Live $files byte sum failed for %s", table_ref, exc_info=True)
        return None


def _split_ref(table_ref: str) -> list[str]:
    """Split a dotted reference into unquoted parts. Double-quoted parts may contain dots."""
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    i = 0
    while i < len(table_ref):
        char = table_ref[i]
        if quoted:
            if char == '"' and table_ref[i + 1 : i + 2] == '"':
                current.append('"')
                i += 1
            elif char == '"':
                quoted = False
            else:
                current.append(char)
        elif char == '"':
            quoted = True
        elif char == ".":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
        i += 1
    parts.append("".join(current))
    return parts


def _catalog_of(table_ref: str) -> str | None:
    parts = _split_ref(table_ref)
    if len(parts) >= 3 and parts[0]:
        return parts[0]
    return None


def _session_identifier(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return '"' + name.replace('"', '""') + '"'


def _query_id(cursor: object) -> str | None:
    stats = getattr(cursor, "stats", None)
    if isinstance(stats, dict):
        query_id = stats.get("queryId") or stats.get("query_id")
        return str(query_id) if query_id else None
    query_id = getattr(cursor, "query_id", None)
    return str(query_id) if query_id else None


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
