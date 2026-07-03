from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
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


@dataclass(frozen=True)
class ExecCtx:
    job_id: UUID | None = None
    table_id: UUID | None = None
    replica: str | None = None


@dataclass(frozen=True)
class ExecResult:
    ok: bool
    engine_ref: str | None = None
    detail: str | None = None
    rows: tuple[tuple[object, ...], ...] = ()
    error: str | None = None
    duration_ms: int | None = None


class MaintenanceEngine(Protocol):
    type: EngineType

    def probe_capacity(self) -> CapacitySnapshot: ...

    def render(self, plan: MaintenancePlan) -> list[RenderedStatement]: ...

    def session_setup(self) -> list[str]: ...

    def execute(self, stmt: RenderedStatement, ctx: ExecCtx | None = None) -> ExecResult: ...
