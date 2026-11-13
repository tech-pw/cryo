from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, Table, TableOperationExclusion
from cryo.domain.lifecycle import TableLifecycle, lifecycle_excluded_operations
from cryo.planner.guardrails import CORRECT_ORDER

_EXCLUDABLE_VALUES = frozenset(op.value for op in CORRECT_ORDER)


@dataclass(frozen=True, slots=True)
class OperationExclusionChange:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    audit_ids: tuple[int, ...]


def excluded_operations(session: Session, table_id: uuid.UUID | str) -> tuple[str, ...]:
    """Operations no plan may include for this table: the operator's exclusions plus the
    ones an archived lifecycle drops (#519)."""
    table_uuid = table_id if isinstance(table_id, uuid.UUID) else uuid.UUID(str(table_id))
    operations = set(
        session.scalars(
            select(TableOperationExclusion.operation).where(
                TableOperationExclusion.table_id == table_uuid
            )
        )
    )
    lifecycle = session.scalar(select(Table.lifecycle).where(Table.id == table_uuid))
    operations.update(lifecycle_excluded_operations(lifecycle))
    return tuple(sorted(operations))


def excluded_operations_by_table(
    session: Session, table_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, ...]]:
    if not table_ids:
        return {}
    grouped: dict[uuid.UUID, list[str]] = {}
    rows = session.execute(
        select(TableOperationExclusion.table_id, TableOperationExclusion.operation).where(
            TableOperationExclusion.table_id.in_(list(table_ids))
        )
    )
    for table_id, operation in rows:
        grouped.setdefault(table_id, []).append(operation)
    archived = session.scalars(
        select(Table.id).where(
            Table.id.in_(list(table_ids)), Table.lifecycle == TableLifecycle.ARCHIVED.value
        )
    )
    for table_id in archived:
        grouped.setdefault(table_id, []).extend(
            lifecycle_excluded_operations(TableLifecycle.ARCHIVED)
        )
    return {table_id: tuple(sorted(set(ops))) for table_id, ops in grouped.items()}


def list_operation_exclusions(
    session: Session, table_id: uuid.UUID
) -> list[TableOperationExclusion]:
    return list(
        session.scalars(
            select(TableOperationExclusion)
            .where(TableOperationExclusion.table_id == table_id)
            .order_by(TableOperationExclusion.operation)
        )
    )


def set_excluded_operations(
    session: Session,
    table_id: uuid.UUID,
    operations: Collection[str],
    *,
    reason: str,
    actor: str,
) -> OperationExclusionChange:
    wanted = {str(op) for op in operations}
    unknown = sorted(wanted - _EXCLUDABLE_VALUES)
    if unknown:
        raise ValueError(f"not an excludable operation: {', '.join(unknown)}")
    existing = {row.operation: row for row in list_operation_exclusions(session, table_id)}
    added = sorted(wanted - existing.keys())
    removed = sorted(existing.keys() - wanted)
    for operation in added:
        session.add(
            TableOperationExclusion(
                table_id=table_id, operation=operation, reason=reason, created_by=actor
            )
        )
    for operation in removed:
        session.delete(existing[operation])
    audits = [
        AuditLog(
            table_id=table_id,
            action=action,
            status="OK",
            detail=f"operations={','.join(ops)}; reason={reason}",
            actor=actor,
        )
        for action, ops in (("operation_exclude", added), ("operation_include", removed))
        if ops
    ]
    session.add_all(audits)
    session.flush()
    return OperationExclusionChange(
        added=tuple(added),
        removed=tuple(removed),
        audit_ids=tuple(int(audit.id) for audit in audits),
    )
