from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from cryo.domain.enums import EngineType, OperationType
from cryo.domain.models import CapacitySnapshot, MaintenancePlan


@dataclass(frozen=True)
class RenderedStatement:
    action: OperationType
    sql: str
    engine: EngineType
    metadata: dict[str, object] = field(default_factory=dict)
    payload: dict[str, object] = field(default_factory=dict)


ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ExecCtx:
    job_id: UUID | None = None
    table_id: UUID | None = None
    replica: str | None = None
    run_id: str | None = None
    on_progress: ProgressCallback | None = None
    session_factory: Any = None
    chunk_checkpointing: bool = False


@dataclass(frozen=True)
class ExecResult:
    ok: bool
    engine_ref: str | None = None
    detail: str | None = None
    rows: tuple[tuple[object, ...], ...] = ()
    error: str | None = None
    duration_ms: int | None = None
    input_files_count: int | None = None
    output_files_count: int | None = None
    actual_bytes_freed: int | None = None
    snapshots_expired: int | None = None
    manifests_rewritten: int | None = None
    skipped: bool = False


class MaintenanceEngine(Protocol):
    type: EngineType

    def probe_capacity(self) -> CapacitySnapshot: ...

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]: ...

    def session_setup(self) -> list[str]: ...

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult: ...

    def execute_plan(
        self, plan: MaintenancePlan, ctx: ExecCtx | None = None
    ) -> list[ExecResult]: ...
