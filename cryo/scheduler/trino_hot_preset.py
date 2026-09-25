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


class EmptyHotRankingError(RuntimeError):
    """Raised instead of reconciling to an empty target set.

    Distinct from the ValueError for a missing preset: that one means "not set up yet", this
    one means "set up, but today's telemetry can't tell us what's hot". Both are recoverable
    without operator action, but only this one implies data loss if ignored.
    """


@dataclass(frozen=True)
class RepinResult:
    preset_id: uuid.UUID
    pinned: int
    unpinned: int
    unchanged: int
    # Which telemetry source ranked this repin -- see TrinoHotTable.source.
    ranking_source: str = "query_logs"
    # Hot tables whose only pin pointed at a disabled preset. Those slots do not
    # schedule anything, so they are reclaimed rather than treated as operator intent.
    reclaimed_from_disabled: int = 0


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
    if not hot_tables:
        # Reconciling to an empty target set would delete every pin this preset holds and
        # leave the fleet's hottest tables with no scheduled maintenance at all. An empty
        # ranking means the telemetry is missing, not that no table is queried -- both
        # ranking sources going quiet is an ingest failure, and the correct response to
        # "I can't tell what's hot right now" is to keep yesterday's answer.
        logger.error(
            "Trino-hot ranking returned no tables over a %dd window -- keeping the existing "
            "pins rather than unpinning the whole preset. Check telemetry ingest "
            "(query_logs / table_query_activity).",
            window_days,
        )
        raise EmptyHotRankingError(
            f"no hot tables found over a {window_days}d window; refusing to unpin {preset_name!r}"
        )

    target_table_ids = {hot.table_id for hot in hot_tables}

    # Every TableAssignment row currently pointing at THIS preset -- the "who does this preset
    # currently claim" side of the reconciliation. Only ever touches assignments this preset
    # itself owns; a table pinned to a different preset is never read or written here.
    existing = session.scalars(
        select(TableAssignment).where(TableAssignment.preset_id == preset.id)
    ).all()
    existing_by_table = {row.table_id: row for row in existing}

    # TableAssignment.table_id is the primary key -- one assignment per table, fleet-wide. A
    # hot table already pinned to a DIFFERENT preset therefore cannot just be inserted; the
    # insert would violate the PK. Taking the table over is the intended behavior (the point
    # of this preset is that the fleet's hottest tables are maintained here, not wherever
    # they happened to land), so reassign the existing row instead of adding a second one.
    # Operator intent is still respected: a row this job did not create is left alone.
    claimed_elsewhere = {
        row.table_id: row
        for row in session.scalars(
            select(TableAssignment).where(
                TableAssignment.table_id.in_(target_table_ids),
                TableAssignment.preset_id != preset.id,
            )
        ).all()
    }
    # A pin whose preset is disabled does not schedule maintenance. Leaving it in place
    # stranded hot tables on the legacy fleet-wide presets after those presets were turned
    # off (2026-09-22: 20 top-500 tables had to be cleared by hand). Treat that slot as
    # vacant even when an operator created the pin — the pin is not doing any work.
    foreign_preset_ids = {row.preset_id for row in claimed_elsewhere.values()}
    disabled_preset_ids = (
        set(
            session.scalars(
                select(Preset.id).where(
                    Preset.id.in_(foreign_preset_ids),
                    Preset.enabled.is_(False),
                )
            ).all()
        )
        if foreign_preset_ids
        else set()
    )

    pinned = 0
    reassigned = 0
    unchanged = 0
    skipped_operator_pins = 0
    reclaimed_from_disabled = 0
    for hot in hot_tables:
        row = existing_by_table.get(hot.table_id)
        if row is not None:
            unchanged += 1
            continue
        foreign = claimed_elsewhere.get(hot.table_id)
        if foreign is not None:
            on_disabled = foreign.preset_id in disabled_preset_ids
            if foreign.assigned_by != assigned_by and not on_disabled:
                # An operator deliberately pinned this table somewhere else. Overriding that
                # silently is exactly the kind of surprise that makes a scheduler untrustworthy.
                skipped_operator_pins += 1
                continue
            if on_disabled and foreign.assigned_by != assigned_by:
                # Own the row so a later repin can release it if the table cools off.
                # Leaving the operator marker would pin it to this preset forever.
                foreign.assigned_by = assigned_by
                reclaimed_from_disabled += 1
            foreign.preset_id = preset.id
            foreign.pinned = True
            reassigned += 1
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

    ranking_source = hot_tables[0].source
    logger.info(
        "Repinned %s (top %d hot tables by %s, %dd window): pinned=%d reassigned=%d "
        "unpinned=%d unchanged=%d left_on_operator_pins=%d reclaimed_from_disabled=%d",
        preset_name,
        limit,
        ranking_source,
        window_days,
        pinned,
        reassigned,
        unpinned,
        unchanged,
        skipped_operator_pins,
        reclaimed_from_disabled,
    )
    return RepinResult(
        preset_id=preset.id,
        pinned=pinned + reassigned,
        unpinned=unpinned,
        unchanged=unchanged,
        ranking_source=ranking_source,
        reclaimed_from_disabled=reclaimed_from_disabled,
    )
