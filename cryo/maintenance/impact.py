"""Measured maintenance outcomes for one table, summed from its audit rows.

Only what an engine reported counts: bytes freed and input/output file counts recorded on
successful operations. Operations that reported nothing are counted separately so the UI can
say how much of the total is measured instead of treating silence as zero.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from cryo.db.models import AuditLog
from cryo.domain.enums import OperationType
from cryo.planner.guardrails import CORRECT_ORDER

_SUCCESS = ("OK", "SUCCESS", "SUCCEEDED")
_OPERATIONS = tuple(op.value for op in OperationType)


@dataclass
class OperationImpact:
    operation: str
    ops: int = 0
    ops_with_bytes: int = 0
    ops_with_files: int = 0
    bytes_freed: int = 0
    files_in: int = 0
    files_out: int = 0


@dataclass
class ImpactWindow:
    days: int | None
    since: datetime | None
    by_operation: list[OperationImpact] = field(default_factory=list)

    @property
    def ops(self) -> int:
        return sum(item.ops for item in self.by_operation)

    @property
    def ops_with_bytes(self) -> int:
        return sum(item.ops_with_bytes for item in self.by_operation)

    @property
    def bytes_freed(self) -> int:
        return sum(item.bytes_freed for item in self.by_operation)

    @property
    def files_in(self) -> int:
        return sum(item.files_in for item in self.by_operation)

    @property
    def files_out(self) -> int:
        return sum(item.files_out for item in self.by_operation)

    @property
    def ops_with_files(self) -> int:
        return sum(item.ops_with_files for item in self.by_operation)


def table_maintenance_impact(
    session: Session,
    table_id: uuid.UUID,
    *,
    now: datetime,
    window_days: int = 30,
) -> tuple[ImpactWindow, ImpactWindow]:
    since = now - timedelta(days=window_days)
    recent = AuditLog.ts >= since
    has_bytes = AuditLog.actual_bytes_freed.isnot(None)
    has_files = AuditLog.input_files_count.isnot(None) & AuditLog.output_files_count.isnot(None)

    def total(
        column: InstrumentedAttribute[int | None], condition: ColumnElement[bool]
    ) -> ColumnElement[Any]:
        return func.coalesce(func.sum(case((condition, column), else_=0)), 0)

    def count(condition: ColumnElement[bool]) -> ColumnElement[Any]:
        return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)

    rows = session.execute(
        select(
            AuditLog.action,
            func.count(AuditLog.id),
            count(has_bytes),
            count(has_files),
            total(AuditLog.actual_bytes_freed, has_bytes),
            total(AuditLog.input_files_count, has_files),
            total(AuditLog.output_files_count, has_files),
            count(recent),
            count(recent & has_bytes),
            count(recent & has_files),
            total(AuditLog.actual_bytes_freed, recent & has_bytes),
            total(AuditLog.input_files_count, recent & has_files),
            total(AuditLog.output_files_count, recent & has_files),
            func.min(AuditLog.ts),
        )
        .where(
            AuditLog.table_id == table_id,
            AuditLog.action.in_(_OPERATIONS),
            func.upper(AuditLog.status).in_(_SUCCESS),
        )
        .group_by(AuditLog.action)
    ).all()

    lifetime = ImpactWindow(days=None, since=None)
    window = ImpactWindow(days=window_days, since=since)
    for row in rows:
        action = str(row[0])
        lifetime.by_operation.append(
            OperationImpact(action, *(max(int(value or 0), 0) for value in row[1:7]))
        )
        if int(row[7] or 0):
            window.by_operation.append(
                OperationImpact(action, *(max(int(value or 0), 0) for value in row[7:13]))
            )
        first = row[13]
        if first is not None and (lifetime.since is None or first < lifetime.since):
            lifetime.since = first
    order = {op.value: index for index, op in enumerate(CORRECT_ORDER)}
    for item in (lifetime, window):
        item.by_operation.sort(key=lambda impact: order.get(impact.operation, len(order)))
    return window, lifetime
