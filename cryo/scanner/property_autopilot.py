"""Autonomous application of safe (tier1, non-risky) table property recommendations.

Closes a real gap found live, 2026-08-10: `compute_property_recommendations()` and
`layout_analyzer.py`'s scheduled generation of them are both real and running -- confirmed
against a live production table returning
specific, well-reasoned recommendations with `"action": "apply"`. `apply_table_properties()`
(the function that actually executes the change) is also real and already used by the
`/tables/{id}/properties/apply` API route. But grepping the whole codebase for any consumer
of a recommendation's `action` field turned up nothing -- the only caller of
`apply_table_properties()` was a human clicking a button. This module is that missing
consumer.

Deliberately narrow, by design, not by oversight:
- Only ever applies items that are ALL of: `tier == "tier1"`, `action == "apply"`,
  `not needs_confirmation`. Tier2 (`link_layout`/`link_bloom` -- a real data rewrite or a
  bloom-filter build, not a metadata-only property flip) and tier3 (`read_only`/`banner`)
  always require a human. `needs_confirmation` items (format-version) always require a
  human regardless of tier.
- `apply_table_properties()` is called completely unchanged -- its own `TIER1_APPLYABLE_KEYS`
  allowlist is the actual safety boundary, checked server-side independent of this module's
  own filter. Even a bug here can't smuggle a non-allowlisted property through.
- Off by default (`Settings.property_autopilot_enabled`) -- opt-in, matching the "test
  before default" pattern used for every other autonomy change this session (the fleet-wide
  scheduler itself, the backlog-aware retention tightening).
- Every autonomous change is audit-logged with a distinct actor (`AUTOPILOT_ACTOR`) so it's
  always distinguishable after the fact from a human-triggered apply.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from cryo.db.models import AuditLog, Table, TableStats
from cryo.scanner.property_recommendations import (
    apply_table_properties,
    compute_property_recommendations,
)

AUTOPILOT_ACTOR = "cryo-property-autopilot"


@dataclass(frozen=True, slots=True)
class PropertyAutopilotTableResult:
    table_id: uuid.UUID
    applied: dict[str, str]
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PropertyAutopilotResult:
    tables_considered: int
    tables_changed: int
    tables_failed: int
    per_table: list[PropertyAutopilotTableResult] = field(default_factory=list)


def _safe_recommendations(session: Session, table_id: uuid.UUID) -> dict[str, str]:
    """The subset of a table's recommendations safe for unattended application."""
    items = compute_property_recommendations(session, table_id)
    return {
        item.property_key: item.recommended_value
        for item in items
        if item.tier == "tier1"
        and item.action == "apply"
        and not item.needs_confirmation
        and item.recommended_value is not None
    }


def run_property_autopilot(
    session: Session,
    *,
    max_tables: int = 100,
    dry_run: bool = False,
) -> PropertyAutopilotResult:
    """Apply safe property recommendations across a bounded batch of active tables.

    Newest-scanned tables first -- freshest stats are the most trustworthy signal for
    whether a recommendation still applies. Active tables with no TableStats yet are
    included first (Day-0 write.target-file-size-bytes and other tier1 apply items still
    work without a scan). One table's analysis or apply failure is caught and recorded
    per-table; it does not stop the rest of the batch (same pattern as the curator-tick
    loop this reuses the calling cadence from).
    """
    no_stats_ids = list(
        session.scalars(
            select(Table.id)
            .outerjoin(TableStats, TableStats.table_id == Table.id)
            .where(Table.is_active.is_(True), TableStats.table_id.is_(None))
            .order_by(Table.first_seen_at.desc())
            .limit(max_tables)
        )
    )
    remaining = max_tables - len(no_stats_ids)
    stats_ids: list[uuid.UUID] = []
    if remaining > 0:
        stats_ids = list(
            session.scalars(
                select(TableStats.table_id)
                .join(Table, Table.id == TableStats.table_id)
                .where(Table.is_active.is_(True))
                .order_by(TableStats.scan_at.desc())
                .limit(remaining)
            )
        )
    table_ids = no_stats_ids + stats_ids

    per_table: list[PropertyAutopilotTableResult] = []
    tables_changed = 0
    tables_failed = 0

    for table_id in table_ids:
        try:
            safe_props = _safe_recommendations(session, table_id)
        except Exception as exc:  # noqa: BLE001 - one table's analysis must not stop the batch
            per_table.append(PropertyAutopilotTableResult(table_id, {}, error=str(exc)))
            tables_failed += 1
            continue

        if not safe_props:
            continue

        if dry_run:
            per_table.append(PropertyAutopilotTableResult(table_id, safe_props))
            tables_changed += 1
            continue

        try:
            applied = apply_table_properties(session, table_id, safe_props)
        except Exception as exc:  # noqa: BLE001 - one table's apply must not stop the batch
            per_table.append(PropertyAutopilotTableResult(table_id, {}, error=str(exc)))
            tables_failed += 1
            continue

        detail = " ".join(f"{k}={v}" for k, v in sorted(applied.items()))
        session.add(
            AuditLog(
                table_id=table_id,
                action="apply_table_properties",
                status="OK",
                engine="pyiceberg",
                detail=f"{detail} note=autonomous tier1 apply",
                actor=AUTOPILOT_ACTOR,
            )
        )
        session.commit()
        per_table.append(PropertyAutopilotTableResult(table_id, applied))
        tables_changed += 1

    return PropertyAutopilotResult(
        tables_considered=len(table_ids),
        tables_changed=tables_changed,
        tables_failed=tables_failed,
        per_table=per_table,
    )
