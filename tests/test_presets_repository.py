from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import HTTPException

from cryo.api.routes import _resolve_selection, _sort_clauses
from cryo.api.schemas import AssignmentSelection
from cryo.db.models import Preset, PresetRule, Table, TableAssignment, TableStats
from cryo.db.repositories import presets


def test_table_to_candidate_merges_table_and_stats_fields() -> None:
    table_id = uuid.uuid4()
    table = Table(
        id=table_id,
        source_id=uuid.uuid4(),
        schema_name="fixtures",
        table_name="fixture_day",
        is_active=True,
    )
    table.stats = TableStats(
        table_id=table_id,
        scan_at=datetime.now(UTC),
        needs_compaction=True,
        compaction_priority="HIGH",
        grade="C",
        overall_score=64,
        total_files=100,
        small_files_count=80,
        total_size_gb=1.25,
    )

    candidate = presets.table_to_candidate(table)

    assert candidate.id == str(table_id)
    assert candidate.schema_name == "fixtures"
    assert candidate.row["grade"] == "C"
    assert candidate.row["small_files_count"] == 80


def test_repository_adapters_preserve_scheduler_resolution_shape() -> None:
    preset_id = uuid.uuid4()
    table_id = uuid.uuid4()
    destination_id = uuid.uuid4()
    preset = Preset(
        id=preset_id,
        name="standard",
        destination_id=destination_id,
        mode="standard",
        operations=["expire_snapshots", "optimize"],
        params={"min_small_files": 10},
        guardrails={"cooldown_hours": 24},
        priority=100,
        enabled=True,
    )
    rule = PresetRule(
        id=uuid.uuid4(),
        preset_id=preset_id,
        predicate={"field": "small_files_count", "op": ">", "value": 10},
        enabled=True,
    )
    assignment = TableAssignment(table_id=table_id, preset_id=preset_id, pinned=True)

    preset_config = presets.preset_to_config(preset)
    rule_config = presets.rule_to_config(rule)
    assignment_config = presets.assignment_to_config(assignment)

    assert preset_config.id == str(preset_id)
    assert preset_config.destination_id == str(destination_id)
    assert rule_config.predicate["field"] == "small_files_count"
    assert assignment_config.table_id == str(table_id)


def test_sort_clauses_reject_unknown_fields() -> None:
    clauses = _sort_clauses("-small_files_count,table_name")

    assert len(clauses) == 2

    try:
        _sort_clauses("unknown")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Unsupported sort field" in str(exc.detail)
    else:
        raise AssertionError("unknown sort field should fail")


def test_resolve_selection_translates_repository_errors_to_http() -> None:
    session = SimpleNamespace()
    selection = AssignmentSelection(mode="ids", ids=[], filter=None)

    try:
        _resolve_selection(session, selection)  # type: ignore[arg-type]
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "ids selection requires" in str(exc.detail)
    else:
        raise AssertionError("invalid selection should fail")
