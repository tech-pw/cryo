"""New-table bookkeeping for the default preset (#119).

A table that no pin or rule claims is maintained by the tenant's default preset from its first
scan. The scanner records how many newly registered tables landed there, and writes one
``table_auto_assigned_default`` audit row per table, so operators can see they were picked up.
Default routing stays implicit: no TableAssignment row is written.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session, selectinload

from cryo.db.models import AuditLog, Table

logger = logging.getLogger(__name__)

TableKey = tuple[uuid.UUID, str, str]

# Bounds the audit rows one scan chunk can write (a first scan of a large catalog discovers
# every table at once); the run summary count is still exact.
MAX_AUDIT_ROWS_PER_SCAN = 1000
_KEY_QUERY_BATCH = 500


def candidate_key(candidate: object) -> TableKey:
    source = candidate.source  # type: ignore[attr-defined]
    return (source.id, candidate.schema_name, candidate.table_name)  # type: ignore[attr-defined]


def unregistered_keys(session: Session, keys: Iterable[TableKey]) -> set[TableKey]:
    """The subset of ``keys`` with no row in cryo.tables yet."""
    wanted = list(dict.fromkeys(keys))
    existing: set[TableKey] = set()
    for start in range(0, len(wanted), _KEY_QUERY_BATCH):
        batch = wanted[start : start + _KEY_QUERY_BATCH]
        rows = session.execute(
            select(Table.source_id, Table.schema_name, Table.table_name).where(
                tuple_(Table.source_id, Table.schema_name, Table.table_name).in_(batch)
            )
        )
        existing.update((row[0], row[1], row[2]) for row in rows)
    return set(wanted) - existing


def record_new_tables_on_default(session: Session, new_keys: Iterable[TableKey]) -> int:
    """Count (and audit) newly registered tables whose effective preset is the default."""
    from cryo.db.repositories.presets import (
        COST_LOAD_ONLY_COLUMNS,
        STATS_LOAD_ONLY_COLUMNS,
        load_resolution_inputs,
        table_to_candidate,
    )
    from cryo.scheduler.rules import effective_preset

    keys = list(dict.fromkeys(new_keys))
    if not keys:
        return 0
    presets, rules, assignments = load_resolution_inputs(session)
    if not any(preset.is_default and preset.enabled for preset in presets):
        return 0
    on_default = 0
    audited = 0
    for start in range(0, len(keys), _KEY_QUERY_BATCH):
        batch = keys[start : start + _KEY_QUERY_BATCH]
        tables = session.scalars(
            select(Table)
            .where(tuple_(Table.source_id, Table.schema_name, Table.table_name).in_(batch))
            .options(
                selectinload(Table.stats).load_only(*STATS_LOAD_ONLY_COLUMNS),
                selectinload(Table.cost).load_only(*COST_LOAD_ONLY_COLUMNS),
            )
        )
        for table in tables:
            effective = effective_preset(table_to_candidate(table), presets, rules, assignments)
            if effective is None or effective.provenance != "default":
                continue
            on_default += 1
            if audited >= MAX_AUDIT_ROWS_PER_SCAN:
                continue
            audited += 1
            session.add(
                AuditLog(
                    table_id=table.id,
                    action="table_auto_assigned_default",
                    status="OK",
                    engine="cryo",
                    actor="scanner",
                    detail=(
                        f"table={table.schema_name}.{table.table_name} "
                        f"preset={effective.preset.name} preset_id={effective.preset.id}"
                        + (" paused=true" if effective.preset.paused else "")
                    ),
                )
            )
    session.commit()
    return on_default
