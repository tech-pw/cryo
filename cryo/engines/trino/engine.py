from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cryo.config import Settings, get_settings
from cryo.domain.enums import EngineType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan
from cryo.engines.base import ExecCtx, ExecResult, RenderedStatement
from cryo.engines.trino.client import TrinoCoordinator, connect, coordinators_from_config
from cryo.engines.trino.load import probe_capacity as probe_trino_capacity
from cryo.engines.trino.sql import render, session_setup


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

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult:
        coordinator = self._coordinator(ctx)
        start = time.monotonic()
        connection: Any = self.connector(coordinator)
        cursor: Any = connection.cursor()
        engine_ref: str | None = None
        try:
            for setup_sql in self.session_setup():
                cursor.execute(setup_sql)
            cursor.execute(stmt.sql)
            rows = tuple(tuple(row) for row in (cursor.fetchall() or ()))
            engine_ref = _query_id(cursor)

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

            return ExecResult(
                ok=True,
                engine_ref=engine_ref,
                detail=stmt.sql,
                rows=rows,
                duration_ms=_elapsed_ms(start),
                input_files_count=input_files,
                output_files_count=output_files,
            )
        except Exception as exc:
            engine_ref = _query_id(cursor)
            return ExecResult(
                ok=False,
                engine_ref=engine_ref,
                detail=stmt.sql,
                error=str(exc),
                duration_ms=_elapsed_ms(start),
            )
        finally:
            cursor.close()
            connection.close()

    def _coordinator(self, ctx: ExecCtx | None) -> TrinoCoordinator:
        replica = getattr(ctx, "replica", None)
        if replica:
            for coordinator in self.coordinators:
                if replica in {coordinator.label, coordinator.host}:
                    return coordinator
        return self.coordinators[0]


def _query_id(cursor: object) -> str | None:
    stats = getattr(cursor, "stats", None)
    if isinstance(stats, dict):
        query_id = stats.get("queryId") or stats.get("query_id")
        return str(query_id) if query_id else None
    query_id = getattr(cursor, "query_id", None)
    return str(query_id) if query_id else None


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
