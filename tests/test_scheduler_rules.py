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


def test_effective_preset_returns_none_when_unconfigured() -> None:
    # No platform-default fallback exists by design; tables without an assignment or
    # matching rule resolve to None (provenance="none").
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
    assert effective is None


def test_effective_preset_prefers_rule_over_unassigned() -> None:
    standard = PresetConfig("standard", "standard", "trino", priority=100)
    large = PresetConfig("large", "large", "spark", priority=200)
    manual = PresetConfig("manual", "manual", "trino", priority=50)
    # An is_default preset exists but is no longer used as a fallback.
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

    effective = effective_preset(table, [standard, large, manual, default_native], rules)
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

    # No rules, no assignment -> None even with an is_default preset present.
    unassigned = effective_preset(table, [standard, large, manual, default_native], rules=())
    assert unassigned is None


def test_effective_preset_falls_through_to_rules_when_pinned_preset_is_disabled() -> None:
    # A table pinned to a preset that has since been disabled (retired, replaced) must
    # not be blackholed forever -- it should fall through to the normal rule engine.
    # Found live in production: 21 tables pinned to disabled presets, including a 24TB
    # table sitting with needs_compaction=True, never rescheduled because the pin
    # short-circuited before the rule loop ever ran.
    stale_preset = PresetConfig("stale", "retired-preset", "spark", priority=999, enabled=False)
    large = PresetConfig("large", "large", "spark", priority=200)
    table = TableCandidate("table-1", "mview", "events", {"total_size_gb": 300})
    rules = [
        PresetRule("large-table", "large", {"field": "total_size_gb", "op": ">", "value": 100}),
    ]

    effective = effective_preset(
        table,
        [stale_preset, large],
        rules,
        [TableAssignment("table-1", "stale")],
    )
    assert effective is not None
    assert effective.preset.id == "large"
    assert effective.provenance == "rule_after_stale_pin"


def test_effective_preset_falls_through_to_rules_when_pinned_preset_does_not_exist() -> None:
    # Same fallback behavior when the pinned preset id references a preset that was
    # deleted outright, not just disabled.
    large = PresetConfig("large", "large", "spark", priority=200)
    table = TableCandidate("table-1", "mview", "events", {"total_size_gb": 300})
    rules = [
        PresetRule("large-table", "large", {"field": "total_size_gb", "op": ">", "value": 100}),
    ]

    effective = effective_preset(
        table,
        [large],
        rules,
        [TableAssignment("table-1", "deleted-preset-id")],
    )
    assert effective is not None
    assert effective.preset.id == "large"
    assert effective.provenance == "rule_after_stale_pin"


def test_effective_preset_still_none_when_stale_pin_and_no_rule_matches() -> None:
    # The fallback only helps when a rule actually matches -- a stale pin with no
    # matching rule still resolves to None, same as the no-assignment-no-rule case.
    stale_preset = PresetConfig("stale", "retired-preset", "spark", priority=999, enabled=False)
    large = PresetConfig("large", "large", "spark", priority=200)
    table = TableCandidate("table-1", "mview", "events", {"total_size_gb": 5})
    rules = [
        PresetRule("large-table", "large", {"field": "total_size_gb", "op": ">", "value": 100}),
    ]

    effective = effective_preset(
        table,
        [stale_preset, large],
        rules,
        [TableAssignment("table-1", "stale")],
    )
    assert effective is None


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


def test_tables_for_preset_skips_unassigned_when_no_default_fallback() -> None:
    # Without rules or assignments, no tables match — even if the preset has is_default.
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
    assert matched == []
