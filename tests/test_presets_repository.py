from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import sqlalchemy as sa
from fastapi import HTTPException

from cryo.api.routes import _resolve_selection, _sort_clauses
from cryo.api.schemas import AssignmentSelection
from cryo.db.models import (
    Destination,
    Preset,
    PresetRule,
    Source,
    Table,
    TableAssignment,
    TableCost,
    TableStats,
)
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


def test_table_to_candidate_defaults_last_maintained_at_to_none() -> None:
    """Baseline, 2026-09-10: omitting last_maintained_at keeps today's exact behavior
    (None) -- table_to_candidate never queries the DB itself, see its own docstring."""
    table = Table(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        schema_name="fixtures",
        table_name="fixture_day",
        is_active=True,
    )

    candidate = presets.table_to_candidate(table)

    assert candidate.last_maintained_at is None


def test_table_to_candidate_accepts_caller_supplied_last_maintained_at() -> None:
    """Real fix, 2026-09-10: SQLSchedulerStore.list_tables now batch-fetches this and
    passes it in per-table -- table_to_candidate just needs to carry it through."""
    table = Table(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        schema_name="fixtures",
        table_name="fixture_day",
        is_active=True,
    )
    when = datetime(2026, 9, 1, tzinfo=UTC)

    candidate = presets.table_to_candidate(table, last_maintained_at=when)

    assert candidate.last_maintained_at == when


def test_table_to_candidate_exposes_snapshot_and_manifest_counts_for_rule_matching() -> None:
    """Regression, 2026-08-10: total_snapshots/manifest_count were readable via a table's own
    stats but weren't in _stats_dict, so a PresetRule predicate like `total_snapshots > 100`
    would silently never match -- evaluate_predicate()'s row.get() returns None for a missing
    key rather than raising, so this failed quietly instead of loudly. Added alongside
    FIELD_MAP support for these two fields so backlog-style rules can actually route tables,
    not just filter the /tables API."""
    from cryo.domain.predicates import evaluate_predicate

    table_id = uuid.uuid4()
    table = Table(
        id=table_id,
        source_id=uuid.uuid4(),
        schema_name="fixtures",
        table_name="bloated_snapshots",
        is_active=True,
    )
    table.stats = TableStats(
        table_id=table_id,
        scan_at=datetime.now(UTC),
        needs_compaction=True,
        compaction_priority="HIGH",
        grade="F",
        overall_score=20,
        total_files=10,
        small_files_count=8,
        total_size_gb=0.1,
        total_snapshots=499,
        manifest_count=13,
    )

    candidate = presets.table_to_candidate(table)

    assert candidate.row["total_snapshots"] == 499
    assert candidate.row["manifest_count"] == 13
    predicate = {
        "all": [
            {"field": "total_size_gb", "op": "<", "value": 10},
            {"field": "total_snapshots", "op": ">", "value": 100},
        ]
    }
    assert evaluate_predicate(predicate, candidate.row) is True


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


def test_sort_clauses_accepts_consecutive_scan_failures() -> None:
    """Regression test: consecutive_scan_failures was already in the filter allowlist
    (FIELD_MAP, cryo/api/predicates.py) but missing from SORT_FIELDS -- the Governance page's
    "Broken tables" panel filters and sorts by this same field in one request, so the sort half
    failed with a 400 even though the filter half worked, which read as "Failed to load broken
    tables" with no obvious cause."""
    clauses = _sort_clauses("-consecutive_scan_failures")

    assert len(clauses) == 1


# ---------------------------------------------------------------------------
# simulate_preset -- must not eager-load TableStats.raw / TableCost.raw
# ---------------------------------------------------------------------------


def test_simulate_preset_does_not_load_raw_jsonb_blob(session) -> None:
    """Regression, 2026-08-10: simulate_preset's query used a bare
    `selectinload(Table.stats)` with no column restriction, which pulls every column --
    including `raw`, a large per-table JSONB blob (file samples, per-column quality
    stats, health deductions) -- for the whole matched fleet at once. Confirmed live:
    this OOM-killed the API pod (6Gi limit) via a fleet-wide predicate. STATS_LOAD_ONLY_
    COLUMNS/COST_LOAD_ONLY_COLUMNS must keep `raw` out of what's actually selected from
    the DB, while still returning correct values for the columns predicates/reporting
    actually use.
    """
    source = Source(id=uuid.uuid4(), name=f"source-{uuid.uuid4()}", type="rest", config={})
    destination = Destination(
        id=uuid.uuid4(), name=f"dest-{uuid.uuid4()}", engine="native", config={}
    )
    session.add_all([source, destination])
    session.flush()

    preset = Preset(
        id=uuid.uuid4(),
        name=f"preset-{uuid.uuid4()}",
        destination_id=destination.id,
        mode="standard",
        operations=["optimize"],
        params={},
        guardrails={},
        priority=0,
    )
    session.add(preset)
    session.flush()
    session.add(
        PresetRule(
            id=uuid.uuid4(),
            preset_id=preset.id,
            predicate={"field": "is_active", "op": "=", "value": True},
            enabled=True,
        )
    )

    table = Table(
        id=uuid.uuid4(),
        source_id=source.id,
        schema_name="fixtures",
        table_name="fixture_day",
        is_active=True,
    )
    session.add(table)
    session.flush()
    session.add(
        TableStats(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            needs_compaction=True,
            compaction_priority="HIGH",
            grade="C",
            overall_score=64,
            total_files=100,
            small_files_count=80,
            total_size_gb=1.25,
            # The large blob that must NOT be pulled by the restricted query.
            raw={"wair_file_sample": ["x"] * 5000, "column_quality": ["y"] * 5000},
        )
    )
    session.add(
        TableCost(
            table_id=table.id,
            scan_at=datetime.now(UTC),
            precision="metadata",
            price_per_gb_month=0.023,
            live_bytes=0,
            dead_bytes=0,
            metadata_bytes=0,
            current_monthly_cost_usd=0.0,
            projected_monthly_cost_usd=0.0,
            projected_savings_usd_month=0.0,
            raw={"padding": ["z"] * 5000},
        )
    )
    session.commit()

    # The fixed query, run directly against this session (mirrors simulate_preset's
    # own query construction) so we can inspect what actually got loaded.
    from sqlalchemy.orm import selectinload

    loaded = session.scalars(
        sa.select(Table)
        .where(Table.is_active.is_(True))
        .options(
            selectinload(Table.stats).load_only(*presets.STATS_LOAD_ONLY_COLUMNS),
            selectinload(Table.cost).load_only(*presets.COST_LOAD_ONLY_COLUMNS),
        )
    ).one()

    stats_state = sa.inspect(loaded.stats)
    cost_state = sa.inspect(loaded.cost)
    assert "raw" in stats_state.unloaded, "TableStats.raw must stay unloaded at fleet scale"
    assert "raw" in cost_state.unloaded, "TableCost.raw must stay unloaded at fleet scale"

    # Correctness: the columns rule evaluation / reporting actually need are still there.
    candidate = presets.table_to_candidate(loaded)
    assert candidate.row["grade"] == "C"
    assert candidate.row["needs_compaction"] is True
    assert candidate.row["small_files_count"] == 80
    assert candidate.row["cost_precision"] == "metadata"

    # simulate_preset itself, end to end: matches the table via the fleet-wide rule
    # and doesn't blow up doing it.
    matched, estimate = presets.simulate_preset(session, preset)
    assert estimate["matched_count"] == 1
    assert matched[0].id == str(table.id)


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
