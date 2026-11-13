"""Deep default preset + per-table orphan cadence (remove_orphan_files on schedule)."""

from __future__ import annotations

import importlib.util
import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from cryo.api.schemas import PresetCreate
from cryo.config import Settings
from cryo.domain.enums import Mode
from cryo.planner.execution import build_execution_plan
from cryo.planner.orphan_cadence import (
    decide_orphan_cadence,
    next_orphan_cleanup_due,
    stagger_slot,
)
from cryo.planner.planner import build_plan
from cryo.scheduler.rules import TableCandidate
from cryo.scheduler.scheduler import apply_priority_order

NOW = datetime(2026, 11, 13, 2, 5, tzinfo=UTC)
ORPHAN = "remove_orphan_files"


class _Dest:
    engine = "spark_emr"
    config = {"catalog": "iceberg"}


def _table_on_slot_today(cadence: int = 7) -> str:
    today = (NOW.date() - date(1970, 1, 1)).days % cadence
    while True:
        table_id = str(uuid.uuid4())
        if stagger_slot(table_id, cadence) == today:
            return table_id


def _table_off_slot_today(cadence: int = 7) -> str:
    today = (NOW.date() - date(1970, 1, 1)).days % cadence
    while True:
        table_id = str(uuid.uuid4())
        if stagger_slot(table_id, cadence) != today:
            return table_id


def _stats(**extra: Any) -> dict[str, Any]:
    return {
        "catalog": "iceberg",
        "schema_name": "analytics",
        "table_name": "events",
        "small_files_count": 500,
        "total_files": 600,
        "total_size_gb": 10,
        **extra,
    }


def _plan(stats: dict[str, Any], params: dict[str, Any] | None = None, **kwargs: Any):
    return build_execution_plan(
        stats=stats,
        partitions=[],
        mode=Mode.DEEP,
        params=params or {},
        destination=_Dest(),
        operations=None,
        now=NOW,
        **kwargs,
    )


def _types(plan) -> list[str]:
    return [op.type.value for op in plan.operations]


def test_new_presets_default_to_deep() -> None:
    preset = PresetCreate(name="p", destination_id=uuid.uuid4())
    assert preset.mode is Mode.DEEP


def test_cadence_settings_defaults() -> None:
    settings = Settings()
    assert settings.orphan_removal_cadence_days == 7
    assert settings.orphan_max_listing_keys > 0


def test_deep_default_plans_orphan_removal_on_the_tables_slot() -> None:
    plan = _plan(_stats(table_id=_table_on_slot_today()))
    assert ORPHAN in _types(plan)
    assert "optimize_manifests" in _types(plan)


def test_cadence_skips_a_table_cleaned_less_than_n_days_ago() -> None:
    table_id = _table_on_slot_today()
    last = NOW - timedelta(days=3)
    plan = _plan(_stats(table_id=table_id, last_orphan_removal_at=last.isoformat()))
    assert ORPHAN not in _types(plan)
    due = next_orphan_cleanup_due(table_id, last, NOW)
    assert any(f"Next orphan cleanup due {due.isoformat()}" in note for note in plan.notes)
    assert due >= (last + timedelta(days=7)).date()
    # The rest of the deep plan still runs.
    assert "expire_snapshots" in _types(plan)


def test_cadence_runs_again_once_n_days_have_passed_on_the_slot() -> None:
    table_id = _table_on_slot_today()
    # Cleaned exactly seven days ago, at a later hour than this run: day granularity.
    last = NOW - timedelta(days=7) + timedelta(hours=1)
    plan = _plan(_stats(table_id=table_id, last_orphan_removal_at=last.isoformat()))
    assert ORPHAN in _types(plan)


def test_off_slot_table_waits_for_its_slot_with_a_note() -> None:
    plan = _plan(_stats(table_id=_table_off_slot_today()))
    assert ORPHAN not in _types(plan)
    assert any("staggered" in note for note in plan.notes)


def test_staggering_spreads_the_fleet_across_the_nights() -> None:
    ids = [str(uuid.uuid4()) for _ in range(7000)]
    per_slot = Counter(stagger_slot(table_id, 7) for table_id in ids)
    assert set(per_slot) == set(range(7))
    assert max(per_slot.values()) < 1.15 * 1000
    nightly = Counter()
    for table_id in ids:
        for offset in range(7):
            now = NOW + timedelta(days=offset)
            if decide_orphan_cadence(table_id=table_id, last_success_at=None, now=now).run:
                nightly[offset] += 1
    assert sum(nightly.values()) == len(ids)
    assert max(nightly.values()) < 1.15 * 1000


def test_never_cleaned_table_with_a_large_backlog_catches_up_ahead_of_its_slot() -> None:
    plan = _plan(_stats(table_id=_table_off_slot_today(), orphan_bytes=50 * 1024**3))
    assert ORPHAN in _types(plan)
    assert any("ahead of its stagger slot" in note for note in plan.notes)


def test_small_backlog_does_not_jump_the_stagger() -> None:
    plan = _plan(_stats(table_id=_table_off_slot_today(), orphan_bytes=1024))
    assert ORPHAN not in _types(plan)


def test_backlog_ordering_puts_the_largest_orphan_backlog_first() -> None:
    tables = [
        TableCandidate(
            id=f"t{gb}",
            schema_name="s",
            table_name=f"t{gb}",
            stats={"compaction_priority": "MEDIUM", "reclaimable_bytes": gb * 1024**3},
        )
        for gb in (1, 4000, 50)
    ]
    assert [table.id for table in apply_priority_order(tables)] == ["t4000", "t50", "t1"]


def test_explicit_request_bypasses_cadence_but_not_safety() -> None:
    last = (NOW - timedelta(days=1)).isoformat()
    params = {"requested_operations": [ORPHAN]}
    plan = _plan(_stats(table_id=_table_off_slot_today(), last_orphan_removal_at=last), params)
    assert _types(plan) == [ORPHAN]
    shared = _plan(_stats(table_id=_table_on_slot_today()), params, has_shared_location=True)
    assert ORPHAN not in _types(shared)


def test_cadence_zero_turns_the_gate_off() -> None:
    last = (NOW - timedelta(days=1)).isoformat()
    plan = _plan(
        _stats(table_id=_table_off_slot_today(), last_orphan_removal_at=last),
        {"orphan_cadence_days": 0},
    )
    assert ORPHAN in _types(plan)


def test_huge_listing_is_skipped_with_a_plan_note() -> None:
    stats = _stats(table_id=_table_on_slot_today(), total_files=6_000_000, total_size_gb=600)
    plan = _plan(stats)
    assert ORPHAN not in _types(plan)
    assert any("listing cap" in note for note in plan.notes)


def test_orphan_estimate_counts_toward_the_listing_cap() -> None:
    # 1M referenced files of ~1 MiB, plus 5 TiB of orphans => ~6M keys.
    stats = _stats(
        table_id=_table_on_slot_today(),
        total_files=1_000_000,
        total_size_gb=1000,
        orphan_bytes=5 * 1024**4,
    )
    assert ORPHAN not in _types(_plan(stats))


@pytest.mark.parametrize(
    ("kwargs", "stats_extra"),
    [
        ({"has_shared_location": True}, {}),
        ({"shared_location_unverified": True}, {}),
        ({}, {"raw": {"iceberg_properties": {"gc.enabled": "false"}}}),
    ],
    ids=["shared-location", "stale-or-failed-overlap-check", "gc-disabled"],
)
def test_every_safety_gate_still_drops_orphan_removal_under_deep_default(
    kwargs: dict[str, Any], stats_extra: dict[str, Any]
) -> None:
    stats = _stats(table_id=_table_on_slot_today(), **stats_extra)
    assert ORPHAN in _types(_plan(_stats(table_id=stats["table_id"])))
    plan = _plan(stats, **kwargs)
    assert ORPHAN not in _types(plan)
    assert any("remove_orphan_files" in note for note in plan.notes)


def test_orphan_min_age_is_never_below_seven_days() -> None:
    plan = _plan(_stats(table_id=_table_on_slot_today()), {"orphan_retention_days": 2})
    orphan = next(op for op in plan.operations if op.type.value == ORPHAN)
    assert orphan.params["retention_days"] == 7
    assert orphan.params["orphan_min_age_hours"] == 168
    assert any("7-day minimum" in note for note in plan.notes)


def test_op_order_is_unchanged_under_deep_default() -> None:
    plan = _plan(_stats(table_id=_table_on_slot_today(), total_delete_files=0))
    types = _types(plan)
    assert types.index("optimize") < types.index("optimize_manifests")
    assert types.index("optimize_manifests") < types.index("expire_snapshots")
    assert types.index("expire_snapshots") < types.index(ORPHAN)


def test_optimize_manifests_is_gated_by_manifest_count() -> None:
    low = build_plan(_stats(manifest_count=12), [], Mode.DEEP, {})
    assert "optimize_manifests" not in _types(low)
    assert any("manifest" in note for note in low.notes)
    high = build_plan(_stats(manifest_count=400), [], Mode.DEEP, {})
    assert "optimize_manifests" in _types(high)
    unknown = build_plan(_stats(), [], Mode.DEEP, {})
    assert "optimize_manifests" in _types(unknown)
    requested = build_plan(
        _stats(manifest_count=1),
        [],
        Mode.DEEP,
        {"requested_operations": ["optimize_manifests"]},
    )
    assert "optimize_manifests" in _types(requested)
    manifest_only = build_plan(_stats(manifest_count=1), [], Mode.MANIFEST_ONLY, {})
    assert _types(manifest_only) == ["optimize_manifests"]


# --- migration: only the unmodified seeded default flips ---------------------------------

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20261113_1300_default_preset_deep.py"
)
_PG_URL = "postgresql+psycopg://cryo:cryo@localhost:5432/cryo"


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("default_preset_deep", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_only_flips_the_unmodified_seeded_default() -> None:
    try:
        engine = create_engine(_PG_URL)
        connection = engine.connect()
    except OperationalError:
        pytest.skip("local Postgres not available")
    migration = _migration()
    transaction = connection.begin()
    try:
        try:
            dest = connection.execute(
                text(
                    "INSERT INTO cryo.destinations (name, engine, config, enabled) "
                    "VALUES (:n, 'spark_emr', '{}'::jsonb, true) RETURNING id"
                ),
                {"n": f"dest-{uuid.uuid4()}"},
            ).scalar_one()
        except ProgrammingError:
            pytest.skip("local Postgres is not migrated")
        # is_default=false here: one default per tenant is a unique index, and the real
        # default may already exist. The migration's predicate is checked with it false too.
        rows = {
            "seeded": (migration.SEEDED_PARAMS, migration.SEEDED_DESCRIPTION),
            "edited-params": ('{"min_small_files": 50}', migration.SEEDED_DESCRIPTION),
            "edited-description": (migration.SEEDED_PARAMS, "ours"),
        }
        names = {key: f"{key}-{uuid.uuid4()}" for key in rows}
        for key, (params, description) in rows.items():
            connection.execute(
                text(
                    "INSERT INTO cryo.presets (name, description, destination_id, mode, "
                    "operations, params, schedule_cron, priority, enabled, guardrails, "
                    "is_default) VALUES (:name, :description, :dest, 'standard', "
                    "'[\"optimize\"]'::jsonb, CAST(:params AS jsonb), '0 2 * * *', 0, true, "
                    "'{\"cooldown_hours\": 24}'::jsonb, false)"
                ),
                {"name": names[key], "description": description, "dest": dest, "params": params},
            )
            sql = migration._flip(
                from_mode="standard",
                to_mode="deep",
                from_ops=migration.SEEDED_OPERATIONS,
                to_ops=migration.DEEP_OPERATIONS,
                from_description=migration.SEEDED_DESCRIPTION,
                to_description=migration.DEEP_DESCRIPTION,
                name=names[key],
            ).replace("is_default = true", "is_default = false")
            connection.execute(text(sql))
        modes = {
            key: connection.execute(
                text("SELECT mode FROM cryo.presets WHERE name = :n"), {"n": names[key]}
            ).scalar_one()
            for key in rows
        }
        assert modes == {
            "seeded": "deep",
            "edited-params": "standard",
            "edited-description": "standard",
        }
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()


def test_migration_requires_is_default_and_targets_the_seeded_name() -> None:
    migration = _migration()
    sql = migration._flip(
        from_mode="standard",
        to_mode="deep",
        from_ops=migration.SEEDED_OPERATIONS,
        to_ops=migration.DEEP_OPERATIONS,
        from_description=migration.SEEDED_DESCRIPTION,
        to_description=migration.DEEP_DESCRIPTION,
    )
    assert "is_default = true" in sql
    assert "name = 'default-spark-compact'" in sql
    assert "mode = 'standard'" in sql
    assert migration.down_revision == "20261113_1200"
