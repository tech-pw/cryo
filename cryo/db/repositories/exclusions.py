from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, MaintenanceExclusion, Table


class ExclusionExistsError(ValueError):
    pass


def table_is_excluded() -> ColumnElement[bool]:
    """Correlated predicate over ``Table``: true when an exclusion covers the row."""
    return exists().where(
        MaintenanceExclusion.source_id == Table.source_id,
        MaintenanceExclusion.schema_name == Table.schema_name,
        or_(
            MaintenanceExclusion.table_name.is_(None),
            MaintenanceExclusion.table_name == Table.table_name,
        ),
    )


def excluded_table_ids(session: Session, table_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    if not table_ids:
        return set()
    return set(
        session.scalars(select(Table.id).where(Table.id.in_(list(table_ids)), table_is_excluded()))
    )


def list_exclusions(session: Session) -> list[MaintenanceExclusion]:
    return list(
        session.scalars(
            select(MaintenanceExclusion).order_by(
                MaintenanceExclusion.schema_name,
                MaintenanceExclusion.table_name.nulls_first(),
            )
        )
    )


def _target(exclusion: MaintenanceExclusion) -> str:
    if exclusion.table_name is None:
        return f"schema {exclusion.schema_name}"
    return f"table {exclusion.schema_name}.{exclusion.table_name}"


def _table_id_for(session: Session, exclusion: MaintenanceExclusion) -> uuid.UUID | None:
    if exclusion.table_name is None:
        return None
    return session.scalar(
        select(Table.id).where(
            Table.source_id == exclusion.source_id,
            Table.schema_name == exclusion.schema_name,
            Table.table_name == exclusion.table_name,
        )
    )


def create_exclusion(
    session: Session,
    *,
    source_id: uuid.UUID,
    schema_name: str,
    table_name: str | None,
    reason: str,
    actor: str,
) -> MaintenanceExclusion:
    duplicate = session.scalar(
        select(MaintenanceExclusion.id).where(
            MaintenanceExclusion.source_id == source_id,
            MaintenanceExclusion.schema_name == schema_name,
            MaintenanceExclusion.table_name.is_(None)
            if table_name is None
            else MaintenanceExclusion.table_name == table_name,
        )
    )
    if duplicate is not None:
        raise ExclusionExistsError("this target is already excluded")
    row = MaintenanceExclusion(
        source_id=source_id,
        schema_name=schema_name,
        table_name=table_name,
        reason=reason,
        created_by=actor,
    )
    session.add(row)
    session.add(
        AuditLog(
            table_id=_table_id_for(session, row),
            action="maintenance_exclude",
            status="OK",
            detail=f"{_target(row)}; source={source_id}; reason={reason}",
            actor=actor,
        )
    )
    session.flush()
    return row


def delete_exclusion(session: Session, exclusion: MaintenanceExclusion, *, actor: str) -> None:
    session.add(
        AuditLog(
            table_id=_table_id_for(session, exclusion),
            action="maintenance_include",
            status="OK",
            detail=f"{_target(exclusion)}; source={exclusion.source_id}",
            actor=actor,
        )
    )
    session.delete(exclusion)
    session.flush()


def excluded_table_count(session: Session, exclusion: MaintenanceExclusion) -> int:
    conditions = [
        Table.source_id == exclusion.source_id,
        Table.schema_name == exclusion.schema_name,
        Table.is_active.is_(True),
    ]
    if exclusion.table_name is not None:
        conditions.append(Table.table_name == exclusion.table_name)
    return int(
        session.scalar(select(func.count()).select_from(Table).where(and_(*conditions))) or 0
    )
