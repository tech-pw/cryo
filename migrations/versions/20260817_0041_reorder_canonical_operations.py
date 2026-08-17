"""Reorder preset operations into the new canonical maintenance sequence.

The canonical order moved from "clean up, then compact" to "compact, then clean up":

    old: expire_snapshots, remove_orphan_files, convert_deletes,
         rewrite_position_deletes, optimize, optimize_manifests
    new: convert_deletes, rewrite_position_deletes, optimize, compute_statistics,
         optimize_manifests, expire_snapshots, remove_orphan_files

``cryo.presets.operations`` stores an ordered JSONB array, so stored rows carry the old
sequence. This backfill resequences each row IN PLACE: it never adds or removes an
operation, so a preset holding 2 of the 7 ops keeps exactly those 2, just reordered.

Operations the enum doesn't recognise are ranked last and keep their relative order.
"""

from __future__ import annotations

from alembic import op

revision = "20260817_0041"
down_revision = "20260813_0040"
branch_labels = None
depends_on = None

# Presets are written as list[str] (cryo/api/schemas.py::PresetCreate), but routes.py has
# long-standing defensive handling for a legacy [{"type": ...}] form -- COALESCE reads both.
_OP_VALUE = "COALESCE(elem.value ->> 'type', elem.value #>> '{}')"

_NEW_RANKS = (
    ("convert_deletes", 1),
    ("rewrite_position_deletes", 2),
    ("optimize", 3),
    ("compute_statistics", 4),
    ("optimize_manifests", 5),
    ("expire_snapshots", 6),
    ("remove_orphan_files", 7),
)

# Pre-change behaviour: compute_statistics was absent from CORRECT_ORDER and the planner
# always appended it last, so it ranks after every other op on the way down.
_OLD_RANKS = (
    ("expire_snapshots", 1),
    ("remove_orphan_files", 2),
    ("convert_deletes", 3),
    ("rewrite_position_deletes", 4),
    ("optimize", 5),
    ("optimize_manifests", 6),
    ("compute_statistics", 7),
)


def _reorder_sql(ranks: tuple[tuple[str, int], ...]) -> str:
    cases = "\n                        ".join(f"WHEN '{name}' THEN {rank}" for name, rank in ranks)
    return f"""
        WITH ranked AS (
            SELECT
                p.id AS preset_id,
                COALESCE(
                    jsonb_agg(elem.value ORDER BY
                        CASE {_OP_VALUE}
                        {cases}
                        ELSE 1000
                        END,
                        elem.ord
                    ),
                    '[]'::jsonb
                ) AS new_ops
            FROM cryo.presets AS p
            CROSS JOIN LATERAL jsonb_array_elements(p.operations)
                WITH ORDINALITY AS elem(value, ord)
            WHERE jsonb_typeof(p.operations) = 'array'
            GROUP BY p.id
        )
        UPDATE cryo.presets AS p
        SET operations = ranked.new_ops
        FROM ranked
        WHERE p.id = ranked.preset_id
          AND p.operations IS DISTINCT FROM ranked.new_ops
    """


def upgrade() -> None:
    op.execute(_reorder_sql(_NEW_RANKS))


def downgrade() -> None:
    op.execute(_reorder_sql(_OLD_RANKS))
