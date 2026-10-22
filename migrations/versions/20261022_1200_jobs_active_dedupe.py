"""jobs.operations / jobs.dedupe_key and a unique index over active jobs

Revision ID: 20261022_1200
Revises: 20261018_1400

One active job per (table, effective operation set). ``jobs.operations`` records an explicit
subset when dedupe trimmed a job to the steps no other active job covers; ``jobs.dedupe_key``
is the canonical string of the job's effective operations and is what the partial unique index
keys on. A NULL key exempts a job (forced runs, creators that do not dedupe).

Existing queues already hold duplicates, which would make the index build fail, so this first
backfills keys for active jobs and retires the extras: of each (table, key) group the job
furthest along survives (running > admitted > queued > deferred, then oldest run). Extra queued
or deferred jobs become ``skipped`` with ``error = 'superseded_by:<id>'``; extra admitted or
running jobs have no safe way to stop here, so they just lose their key and finish normally.
The governor's stale-entry purge drops the retired jobs' Redis entries on its next wave.

The effective-operations rules are inlined (no app imports) so the migration stays valid when
the application code moves on. They mirror ``cryo.planner.job_coverage``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20261022_1200"
down_revision = "20261018_1400"
branch_labels = None
depends_on = None

_INDEX = "ux_jobs_active_table_ops"
_STATUS_RANK = {"running": 0, "admitted": 1, "queued": 2, "deferred": 3}
_BATCH = 2000

_CORRECT_ORDER = (
    "convert_equality_deletes",
    "convert_deletes",
    "rewrite_position_deletes",
    "optimize",
    "compute_statistics",
    "optimize_manifests",
    "expire_data",
    "expire_snapshots",
    "remove_orphan_files",
)
_LIGHT = {"expire_snapshots", "expire_data"}
_STANDARD = _LIGHT | {
    "convert_equality_deletes",
    "convert_deletes",
    "rewrite_position_deletes",
    "optimize",
    "compute_statistics",
}
_MODE_OPERATIONS: dict[str, set[str]] = {
    "light": _LIGHT,
    "standard": _STANDARD,
    "deep": _STANDARD | {"remove_orphan_files", "optimize_manifests"},
    "manifest_only": {"optimize_manifests"},
}


def _canonical(operations: Iterable[Any]) -> tuple[str, ...]:
    wanted = {str(op_name) for op_name in operations}
    return tuple(name for name in _CORRECT_ORDER if name in wanted)


def _as_list(value: Any) -> list[Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, list) and value else None


def _effective_key(mode: str, summary: Any) -> str | None:
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except ValueError:
            summary = None
    run_operations = _as_list(summary.get("operations")) if isinstance(summary, dict) else None
    operations = _canonical(run_operations) if run_operations else ()
    if not operations:
        operations = _canonical(_MODE_OPERATIONS.get(mode, ()))
    return ",".join(operations) if operations else None


def _chunks(items: Sequence[dict[str, Any]]) -> Iterable[Sequence[dict[str, Any]]]:
    for start in range(0, len(items), _BATCH):
        yield items[start : start + _BATCH]


def _backfill_and_retire_duplicates() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT j.id, j.table_id, j.mode, j.status, r.created_at, r.summary "
            "FROM cryo.jobs j JOIN cryo.runs r ON r.id = j.run_id "
            "WHERE j.status IN ('queued', 'admitted', 'running', 'deferred')"
        )
    ).all()

    groups: dict[tuple[str, str], list[tuple[Any, ...]]] = {}
    for job_id, table_id, mode, status, run_created_at, summary in rows:
        key = _effective_key(str(mode), summary)
        if key is None:
            continue
        rank = _STATUS_RANK.get(str(status), len(_STATUS_RANK))
        groups.setdefault((str(table_id), key), []).append(
            (rank, run_created_at, str(job_id), status)
        )

    keyed: list[dict[str, Any]] = []
    unkeyed: list[dict[str, Any]] = []
    retired: list[dict[str, Any]] = []
    for (_table_id, key), members in groups.items():
        members.sort(key=lambda member: (member[0], member[1], member[2]))
        winner_id = members[0][2]
        keyed.append({"id": winner_id, "key": key})
        for _rank, _created, job_id, status in members[1:]:
            if status in ("queued", "deferred"):
                retired.append({"id": job_id, "winner": f"superseded_by:{winner_id}"})
            else:
                unkeyed.append({"id": job_id})

    for chunk in _chunks(keyed):
        bind.execute(
            sa.text("UPDATE cryo.jobs SET dedupe_key = :key WHERE id = :id"),
            list(chunk),
        )
    for chunk in _chunks(retired):
        bind.execute(
            sa.text(
                "UPDATE cryo.jobs SET status = 'skipped', error = :winner, finished_at = now(), "
                "dedupe_key = NULL WHERE id = :id AND status IN ('queued', 'deferred')"
            ),
            list(chunk),
        )


def upgrade() -> None:
    op.execute("ALTER TABLE cryo.jobs ADD COLUMN IF NOT EXISTS operations JSONB")
    op.execute("ALTER TABLE cryo.jobs ADD COLUMN IF NOT EXISTS dedupe_key TEXT")

    _backfill_and_retire_duplicates()

    # CONCURRENTLY: jobs is written by every worker; a plain CREATE INDEX blocks those writes.
    # A previous attempt that failed mid-build leaves an INVALID index of the same name, which
    # IF NOT EXISTS would silently keep, so clear it first.
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX,
            table_name="jobs",
            schema="cryo",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.create_index(
            _INDEX,
            "jobs",
            ["table_id", "dedupe_key"],
            unique=True,
            schema="cryo",
            postgresql_where=sa.text(
                "status IN ('queued', 'admitted', 'running', 'deferred') AND dedupe_key IS NOT NULL"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX,
            table_name="jobs",
            schema="cryo",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.execute("ALTER TABLE cryo.jobs DROP COLUMN IF EXISTS dedupe_key")
    op.execute("ALTER TABLE cryo.jobs DROP COLUMN IF EXISTS operations")
