"""20261118_1200: stored preset operations rewritten to the mode's list (#594)."""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from cryo.api.routes import list_preset_modes
from cryo.domain.enums import Mode
from cryo.planner.guardrails import CORRECT_ORDER, mode_operation_values

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "20261118_1200_preset_operations_from_mode.py"
)
_PG_URL = "postgresql+psycopg://cryo:cryo@localhost:5432/cryo"


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("preset_operations_from_mode", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_snapshot_matches_what_presets_modes_serves() -> None:
    # If this fails, the mode table changed after 20261118_1200. Do not edit the snapshot:
    # write a new data migration for the new lists.
    served = {
        item.mode.value: tuple(op.value for op in item.operations)
        for item in list_preset_modes().modes
    }
    assert _migration().MODE_OPERATION_LISTS == served
    assert served == {mode.value: tuple(mode_operation_values(mode)) for mode in Mode}


def test_snapshot_lists_are_in_canonical_order() -> None:
    rank = {op.value: index for index, op in enumerate(CORRECT_ORDER)}
    for ops in _migration().MODE_OPERATION_LISTS.values():
        assert list(ops) == sorted(ops, key=rank.__getitem__)


def test_rewrite_only_touches_known_modes() -> None:
    migration = _migration()
    sql = migration.rewrite_sql()
    assert "UPDATE cryo.presets" in sql
    assert "WHERE mode IN ('light', 'standard', 'deep', 'manifest_only')" in sql
    assert "IS DISTINCT FROM" in sql
    assert f"'{json.dumps(list(migration.MODE_OPERATION_LISTS['deep']))}'::jsonb" in sql


def test_chains_to_the_previous_head_and_downgrade_is_a_no_op() -> None:
    migration = _migration()
    assert migration.revision == "20261118_1200"
    assert migration.down_revision == "20261116_1000"
    migration.downgrade()


def test_rewrite_against_postgres() -> None:
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
        rows = {
            "deep": '["optimize"]',
            "standard": '["expire_snapshots", "optimize"]',
            "manifest_only": "[]",
            "custom": '["optimize"]',
        }
        names = {mode: f"{mode}-{uuid.uuid4()}" for mode in rows}
        for mode, ops in rows.items():
            connection.execute(
                text(
                    "INSERT INTO cryo.presets (name, destination_id, mode, operations, params, "
                    "priority, enabled, guardrails, is_default) VALUES (:name, :dest, :mode, "
                    "CAST(:ops AS jsonb), '{}'::jsonb, 0, true, '{}'::jsonb, false)"
                ),
                {"name": names[mode], "dest": dest, "mode": mode, "ops": ops},
            )
        connection.execute(text(migration.rewrite_sql()))
        stored = {
            mode: connection.execute(
                text("SELECT operations FROM cryo.presets WHERE name = :n"), {"n": names[mode]}
            ).scalar_one()
            for mode in rows
        }
        assert stored["deep"] == list(migration.MODE_OPERATION_LISTS["deep"])
        assert stored["standard"] == list(migration.MODE_OPERATION_LISTS["standard"])
        assert stored["manifest_only"] == ["optimize_manifests"]
        assert stored["custom"] == ["optimize"]
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()
