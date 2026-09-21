"""Daily repin for the Trino-cost-reduction preset: pins exactly the current top-N tables by
real Trino query volume to a dedicated preset, unpinning any table that held a pin from a prior
run but fell out of this run's top-N.

Design (see docs discussion, 2026-09-20): a table is pinned via TableAssignment, which is a
one-preset-per-table relationship (table_id is its primary key) -- pinning here deliberately
takes the table's maintenance over from whatever preset covered it before, for as long as it
stays in the top-N. This is the intended behavior: the whole point is that Cryo's actual
maintenance effort each day concentrates on the tables driving the most Trino query cost, not
that this preset runs *alongside* whatever else was already scheduled for them.

The preset's own rule (needs_compaction=true) and the scheduler's existing priority ordering
(apply_priority_order, already query_count_30d-weighted) still gate/rank which of the pinned
tables actually get a job in a given run -- being in the top 500 makes a table ELIGIBLE for this
preset, it does not force a job to run against a table that doesn't need one.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import Preset, TableAssignment
from cryo.planner.trino_hot_tables import top_trino_query_tables

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepinResult:
    preset_id: uuid.UUID
    pinned: int
    unpinned: int
    unchanged: int


def repin_trino_hot_tables(
    session: Session,
    *,
    preset_name: str,
    limit: int = 500,
    window_days: int = 30,
    assigned_by: str = "trino_hot_preset_repin",
    now: datetime | None = None,
) -> RepinResult:
    """Idempotent: safe to run once a day (or re-run manually) -- recomputes the top-N from
    scratch each call and reconciles TableAssignment rows to match exactly, rather than only
    ever adding pins. A table's TableAssignment.pinned defaults true and is left true here
    (this function never creates an unpinned assignment) -- a table this job pins is always
    meant to be exclusively on this preset, not just eligible alongside its prior one.
    """
    current = now or datetime.now(UTC)
    preset = session.scalar(select(Preset).where(Preset.name == preset_name))
    if preset is None:
        raise ValueError(f"preset {preset_name!r} not found -- seed it before running repin")

    hot_tables = top_trino_query_tables(session, limit=limit, window_days=window_days, now=current)
    target_table_ids = {hot.table_id for hot in hot_tables}

    # Every TableAssignment row currently pointing at THIS preset -- the "who does this preset
    # currently claim" side of the reconciliation. Only ever touches assignments this preset
    # itself owns; a table pinned to a different preset is never read or written here.
    existing = session.scalars(
        select(TableAssignment).where(TableAssignment.preset_id == preset.id)
    ).all()
    existing_by_table = {row.table_id: row for row in existing}

    pinned = 0
    unchanged = 0
    for hot in hot_tables:
        row = existing_by_table.get(hot.table_id)
        if row is not None:
            unchanged += 1
            continue
        session.add(
            TableAssignment(
                table_id=hot.table_id,
                preset_id=preset.id,
                assigned_by=assigned_by,
                pinned=True,
            )
        )
        pinned += 1

    unpinned = 0
    for table_id, row in existing_by_table.items():
        if table_id in target_table_ids:
            continue
        # Only remove assignments THIS job created -- a table an operator manually pinned to
        # this preset (assigned_by set to something else) is left alone even if it drops out of
        # the current top-N; this job must not silently override explicit operator intent.
        if row.assigned_by != assigned_by:
            continue
        session.delete(row)
        unpinned += 1

    session.commit()

    logger.info(
        "Repinned %s (top %d Trino-hot tables, %dd window): pinned=%d unpinned=%d unchanged=%d",
        preset_name,
        limit,
        window_days,
        pinned,
        unpinned,
        unchanged,
    )
    return RepinResult(preset_id=preset.id, pinned=pinned, unpinned=unpinned, unchanged=unchanged)
