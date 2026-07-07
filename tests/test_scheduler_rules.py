from __future__ import annotations

from cryo.domain.enums import Mode
from cryo.scheduler.rules import (
    PresetConfig,
    PresetRule,
    TableAssignment,
    TableCandidate,
    effective_preset,
    evaluate_predicate,
    tables_for_preset,
)


def test_evaluate_predicate_supports_shared_filter_grammar() -> None:
    row = {
        "schema_name": "mview_sales",
        "table_name": "orders_daily",
        "total_size_gb": 250,
        "small_files_count": 1_200,
        "grade": "F",
    }

    assert evaluate_predicate(
        {
            "all": [
                {"field": "schema_name", "op": "glob", "value": "mview_*"},
                {"field": "total_size_gb", "op": ">", "value": 100},
                {"field": "grade", "op": "in", "value": ["D", "F"]},
                {"not": {"field": "table_name", "op": "like", "value": "%tmp%"}},
            ]
        },
        row,
    )


def test_effective_preset_uses_platform_default_when_unconfigured() -> None:
    default_native = PresetConfig(
        "default-native",
        "default-native-deep",
        "native",
        mode=Mode.DEEP,
        priority=0,
        is_default=True,
    )
    table = TableCandidate("table-1", "demo", "orders", {"total_size_gb": 5})
    effective = effective_preset(table, [default_native], rules=())
    assert effective is not None
    assert effective.preset.id == "default-native"
    assert effective.provenance == "default"


def test_effective_preset_prefers_rule_over_default() -> None:
    standard = PresetConfig("standard", "standard", "trino", priority=100)
    large = PresetConfig("large", "large", "spark", priority=200)
    manual = PresetConfig("manual", "manual", "trino", priority=50)
    default_native = PresetConfig(
        "default-native",
        "default-native-deep",
        "native",
        priority=0,
        is_default=True,
    )
    table = TableCandidate(
        "table-1",
        "mview",
        "events",
        {"total_size_gb": 300, "small_files_count": 1_000},
    )
    rules = [
        PresetRule(
            "small-files", "standard", {"field": "small_files_count", "op": ">", "value": 10}
        ),
        PresetRule("large-table", "large", {"field": "total_size_gb", "op": ">", "value": 100}),
    ]

    effective = effective_preset(
        table, [standard, large, manual, default_native], rules
    )
    assert effective is not None
    assert effective.preset.id == "large"
    assert effective.provenance == "rule"

    assigned = effective_preset(
        table,
        [standard, large, manual, default_native],
        rules,
        [TableAssignment("table-1", "manual")],
    )
    assert assigned is not None
    assert assigned.preset.id == "manual"
    assert assigned.provenance == "assignment"


def test_tables_for_preset_uses_effective_preset_and_skips_inactive_tables() -> None:
    standard = PresetConfig("standard", "standard", "trino", priority=100)
    large = PresetConfig("large", "large", "spark", priority=200)
    tables = [
        TableCandidate("table-1", "mview", "events", {"total_size_gb": 300}),
        TableCandidate("table-2", "raw", "clicks", {"total_size_gb": 20}),
        TableCandidate("table-3", "raw", "old", {"total_size_gb": 500}, is_active=False),
    ]
    rules = [
        PresetRule("default", "standard", {"field": "total_size_gb", "op": ">=", "value": 0}),
        PresetRule("large-table", "large", {"field": "total_size_gb", "op": ">", "value": 100}),
    ]

    assert [table.id for table in tables_for_preset(large, tables, [standard, large], rules)] == [
        "table-1"
    ]
    assert [
        table.id for table in tables_for_preset(standard, tables, [standard, large], rules)
    ] == ["table-2"]


def test_tables_for_preset_includes_default_fallback_tables() -> None:
    default_native = PresetConfig(
        "default-native",
        "default-native-deep",
        "native",
        priority=0,
        is_default=True,
    )
    tables = [
        TableCandidate("table-1", "demo", "orders", {"total_size_gb": 5}),
        TableCandidate("table-2", "demo", "events", {"total_size_gb": 1}),
    ]
    matched = tables_for_preset(default_native, tables, [default_native], rules=())
    assert [table.id for table in matched] == ["table-1", "table-2"]
